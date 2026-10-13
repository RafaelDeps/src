"""Resolução das credenciais do SRC em runtime (`.env`/ambiente ou Infisical).

Consumido por ``etl/gerenciar.py`` (via ``carregar_credenciais``):

1. se ``USER``/``PASSWORD`` já estiverem no ``.env`` — ou ``SRC_USER``/``SRC_PASS``
   no ambiente — são usados diretamente, **sem rede**;
2. caso contrário, ``src-user``/``src-password`` são lidos do Infisical via
   Machine Identity (Universal Auth) **em memória**, apenas para a execução atual.

Os valores vindos do Infisical **nunca** são gravados no ``.env`` nem em qualquer
outro arquivo/cache. Este módulo não expõe comando de escrita.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_none,
)

log = logging.getLogger("src_etl.credentials")

# ------------------------------------------------------------------ padrões (R7/R8)
DEFAULT_HOST = "https://app.infisical.com"
DEFAULT_ENVIRONMENT = "dev"
DEFAULT_SECRET_PATH = "/"
DEFAULT_ENV_FILE = ".env"

#: Variáveis de bootstrap do Infisical resolvidas com precedência ambiente > `.env` > padrão.
BOOTSTRAP_VARS = (
    "INFISICAL_HOST",
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_PROJECT_ID",
    "INFISICAL_ENV",
    "INFISICAL_SECRET_PATH",
)

#: Mapeamento fixo e imutável (FR-001): só estes dois segredos são lidos.
SECRET_MAPPING: tuple[tuple[str, str], ...] = (
    ("src-user", "USER"),
    ("src-password", "PASSWORD"),
)
SECRET_NAMES: tuple[str, ...] = tuple(source for source, _ in SECRET_MAPPING)

#: Nº total de tentativas em falhas transitórias (1 execução + retries).
TRANSIENT_MAX_ATTEMPTS = 4


# ------------------------------------------------------------------ erros de domínio
class CredentialsError(Exception):
    """Falha ao obter credenciais (mensagem acionável, sem valores brutos)."""


class ConfigError(CredentialsError):
    """Bootstrap do Infisical ausente/inválido — falha rápida, sem retry."""


# ------------------------------------------------------------------ entidades (data-model.md)
CredentialSource = Literal["env_file", "environment", "infisical"]


@dataclass(frozen=True)
class Credentials:
    """Credenciais do SRC resolvidas para uma execução. Nunca persistidas."""

    user: str
    password: str
    source: CredentialSource


@dataclass(frozen=True)
class InfisicalConfig:
    """Configuração efetiva de acesso ao Infisical (bootstrap resolvido)."""

    host: str
    client_id: str
    client_secret: str
    project_id: str
    environment_slug: str = DEFAULT_ENVIRONMENT
    secret_path: str = DEFAULT_SECRET_PATH
    env_file_path: Path = Path(DEFAULT_ENV_FILE)


# ------------------------------------------------------------------ máscara (FR-009)
def mask(value: str) -> str:
    """Expõe no máximo os 2 primeiros caracteres; o restante vira ``*`` (R6)."""

    if not value:
        return ""
    if len(value) <= 2:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 2)


# ------------------------------------------------------------------ leitura do .env (R4)
def _unescape_single(text: str) -> str:
    """Decodifica ``\\\\`` e ``\\'`` em valores entre aspas simples."""

    saida: list[str] = []
    i = 0
    while i < len(text):
        atual = text[i]
        if atual == "\\" and i + 1 < len(text) and text[i + 1] in ("\\", "'"):
            saida.append(text[i + 1])
            i += 2
        else:
            saida.append(atual)
            i += 1
    return "".join(saida)


def _unescape_double(text: str) -> str:
    """Decodifica ``\\n``, ``\\"`` e ``\\\\`` em valores entre aspas duplas."""

    escapes = {"n": "\n", '"': '"', "\\": "\\"}
    saida: list[str] = []
    i = 0
    while i < len(text):
        atual = text[i]
        if atual == "\\" and i + 1 < len(text):
            proximo = text[i + 1]
            saida.append(escapes.get(proximo, "\\" + proximo))
            i += 2
        else:
            saida.append(atual)
            i += 1
    return "".join(saida)


def parse_env_value(raw: str) -> tuple[str, bool]:
    """Interpreta um valor de ``.env``: retorna ``(valor, estava_entre_aspas)``.

    Aspas simples são quase literais (só decodificam ``\\\\`` e ``\\'``; ``#`` é
    preservado). Aspas duplas processam ``\\n``/``\\"``/``\\\\``. Sem aspas,
    descarta comentário inline e faz strip.
    """

    valor = raw.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        interno = valor[1:-1]
        if valor[0] == "'":
            return _unescape_single(interno), True
        return _unescape_double(interno), True
    return valor.split("#", 1)[0].strip(), False


def load_env_values(path: str | Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Lê pares chave/valor do ``.env`` (quote-aware, BOM/CRLF tolerantes)."""

    destino = Path(path)
    if not destino.exists():
        return {}
    valores: dict[str, str] = {}
    try:
        texto = destino.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise CredentialsError(f"Não foi possível ler '{destino}': {exc}.") from exc
    for raw_line in texto.splitlines():
        linha = raw_line.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, raw_value = linha.split("=", 1)
        valor, _quoted = parse_env_value(raw_value)
        valores[chave.strip()] = valor
    return valores


# ------------------------------------------------------------------ retry (FR-014)
def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _transient_types() -> tuple[type, ...]:
    tipos: list[type] = [
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
        ConnectionRefusedError,
        ConnectionAbortedError,
    ]
    try:
        from infisical_sdk.infisical_requests import NETWORK_ERRORS

        tipos.extend(NETWORK_ERRORS)
    except Exception:  # pragma: no cover - SDK indisponível
        pass
    try:
        import requests

        tipos.append(requests.exceptions.RequestException)
    except Exception:  # pragma: no cover - requests indisponível
        pass
    return tuple(dict.fromkeys(tipos))


def _is_transient(exc: BaseException) -> bool:
    """Timeouts/conexão/5xx são transitórios; 401/403/404 (e demais 4xx) não são."""

    code = _status_code(exc)
    if code is not None:
        return code >= 500
    return isinstance(exc, _transient_types())


def _run_with_retry(operation: Callable[[], Any]) -> tuple[Any, int]:
    retrying = Retrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(TRANSIENT_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, max=8.0),
        reraise=True,
    )
    tentativas = 0
    for attempt in retrying:
        with attempt:
            tentativas = getattr(attempt, "attempt_number", tentativas + 1)
            return operation(), tentativas
    raise CredentialsError("Operação sem tentativas executadas.")  # pragma: no cover


# ------------------------------------------------------------------ Infisical (FR-001/FR-002)
def create_infisical_client(config: InfisicalConfig):
    """Fábrica do cliente do Infisical (substituível em testes via monkeypatch)."""

    from infisical_sdk import InfisicalSDKClient

    return InfisicalSDKClient(host=config.host, cache_ttl=None)


def _fetch_secrets(client, config: InfisicalConfig) -> dict[str, str]:
    client.auth.universal_auth.login(config.client_id, config.client_secret)
    log.info("Login OK (Universal Auth)")
    coletados: dict[str, str] = {}
    for nome in SECRET_NAMES:
        try:
            segredo = client.secrets.get_secret_by_name(
                secret_name=nome,
                project_id=config.project_id,
                environment_slug=config.environment_slug,
                secret_path=config.secret_path,
                view_secret_value=True,
            )
        except CredentialsError:
            raise
        except Exception as exc:
            if _status_code(exc) == 404:
                raise CredentialsError(
                    f"Segredo '{nome}' não encontrado em "
                    f"{config.environment_slug}{config.secret_path}."
                ) from exc
            raise
        valor = getattr(segredo, "secretValue", None)
        if valor is None:
            valor = getattr(segredo, "secret_value", None)
        if valor is None or str(valor) == "":
            raise CredentialsError(
                f"Segredo '{nome}' está ausente ou vazio; credenciais não resolvidas (falha segura)."
            )
        coletados[nome] = str(valor)
    return coletados


def _translate_error(exc: BaseException, config: InfisicalConfig) -> CredentialsError:
    code = _status_code(exc)
    if code == 401:
        return CredentialsError(
            "Falha de autenticação no Infisical (401): verifique "
            "INFISICAL_CLIENT_ID/INFISICAL_CLIENT_SECRET."
        )
    if code == 403:
        return CredentialsError(
            "Sem autorização no Infisical (403): a identidade não tem acesso "
            "ao projeto/ambiente informado."
        )
    if code == 404:
        return CredentialsError(
            f"Segredo não encontrado em {config.environment_slug}{config.secret_path}."
        )
    if _is_transient(exc):
        return CredentialsError(
            "Serviço do Infisical indisponível após várias tentativas "
            "(timeout/conexão/5xx). Tente novamente."
        )
    return CredentialsError(f"Falha ao ler segredos do Infisical: {type(exc).__name__}.")


def fetch_required_secrets(config: InfisicalConfig, client=None) -> dict[str, str]:
    """Lê **somente** os nomes de :data:`SECRET_MAPPING`, com retry em transitórios."""

    if client is None:
        client = create_infisical_client(config)
    try:
        segredos, _tentativas = _run_with_retry(lambda: _fetch_secrets(client, config))
    except CredentialsError:
        raise
    except Exception as exc:
        raise _translate_error(exc, config) from exc
    return segredos


# ------------------------------------------------------------------ configuração (R8)
def _nonempty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _bootstrap(name: str, envfile: Mapping[str, str], default: str | None = None) -> str | None:
    """Resolve um valor de bootstrap com precedência ambiente > ``.env`` > padrão."""

    value = _nonempty(os.environ.get(name))
    if value:
        return value
    value = _nonempty(envfile.get(name))
    if value:
        return value
    return default


def resolve_infisical_config(env_path: str | Path = DEFAULT_ENV_FILE) -> InfisicalConfig:
    """Monta o :class:`InfisicalConfig` a partir do ambiente e do ``.env``.

    Precedência: variável de ambiente > ``.env`` > padrão. Exige
    ``INFISICAL_CLIENT_ID``/``INFISICAL_CLIENT_SECRET``/``INFISICAL_PROJECT_ID``.
    """

    env_file = Path(env_path)
    envfile = load_env_values(env_file)

    host = _bootstrap("INFISICAL_HOST", envfile) or DEFAULT_HOST
    environment_slug = _bootstrap("INFISICAL_ENV", envfile) or DEFAULT_ENVIRONMENT
    secret_path = _bootstrap("INFISICAL_SECRET_PATH", envfile) or DEFAULT_SECRET_PATH
    if not secret_path.startswith("/"):
        secret_path = "/" + secret_path

    client_id = _bootstrap("INFISICAL_CLIENT_ID", envfile)
    client_secret = _bootstrap("INFISICAL_CLIENT_SECRET", envfile)
    project_id = _bootstrap("INFISICAL_PROJECT_ID", envfile)

    missing = [
        name
        for name, value in (
            ("INFISICAL_CLIENT_ID", client_id),
            ("INFISICAL_CLIENT_SECRET", client_secret),
            ("INFISICAL_PROJECT_ID", project_id),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "Credenciais do SRC ausentes: defina USER e PASSWORD no .env, ou configure "
            "INFISICAL_CLIENT_ID/INFISICAL_CLIENT_SECRET/INFISICAL_PROJECT_ID para buscar "
            "em runtime no Infisical (faltando: " + ", ".join(missing) + ")."
        )

    return InfisicalConfig(
        host=host,
        client_id=client_id,  # type: ignore[arg-type]
        client_secret=client_secret,  # type: ignore[arg-type]
        project_id=project_id,  # type: ignore[arg-type]
        environment_slug=environment_slug,
        secret_path=secret_path,
        env_file_path=env_file,
    )


# ------------------------------------------------------------------ resolução (FR-003/FR-011/FR-013)
def resolve_credentials(
    env_path: str | Path = DEFAULT_ENV_FILE,
    *,
    client=None,
    config: InfisicalConfig | None = None,
) -> Credentials:
    """Resolve ``USER``/``PASSWORD`` do SRC sem jamais persistir os valores.

    1. Usa ``USER``/``PASSWORD`` do ``.env`` ou ``SRC_USER``/``SRC_PASS`` do
       ambiente, se ambos estiverem preenchidos (sem rede).
    2. Caso contrário, busca ``src-user``/``src-password`` no Infisical **em
       memória** (``client``/``config`` injetáveis em testes).
    """

    valores = load_env_values(env_path)
    file_user = valores.get("USER") or None
    file_pass = valores.get("PASSWORD") or None
    env_user = os.environ.get("SRC_USER") or None
    env_pass = os.environ.get("SRC_PASS") or None

    user = file_user or env_user
    password = file_pass or env_pass
    if user and password:
        origem: CredentialSource = "env_file" if (file_user and file_pass) else "environment"
        log.info(
            "Credenciais do SRC obtidas do %s (USER=%s).",
            "arquivo .env" if origem == "env_file" else "ambiente",
            mask(user),
        )
        return Credentials(user=user, password=password, source=origem)

    infisical = config or resolve_infisical_config(env_path)
    segredos = fetch_required_secrets(infisical, client=client)
    mapeados = {destino: segredos[origem] for origem, destino in SECRET_MAPPING}
    log.info(
        "Credenciais do SRC obtidas do Infisical em runtime (USER=%s); não persistidas.",
        mask(mapeados["USER"]),
    )
    return Credentials(user=mapeados["USER"], password=mapeados["PASSWORD"], source="infisical")
