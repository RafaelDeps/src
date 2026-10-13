"""Testes offline da resolução de credenciais do SRC (cliente Infisical falso).

Cobrem: precedência ``.env``/ambiente sobre o Infisical, busca em runtime **sem
persistir**, mapeamento fixo, falha segura (segredo ausente/vazio), retry só em
transitórios, mascaramento de logs e mensagem acionável de configuração ausente.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from infisical_sdk.infisical_requests import APIError

from src_etl import credentials as cr
from src_etl.credentials import (
    CredentialsError,
    InfisicalConfig,
    load_env_values,
    parse_env_value,
    fetch_required_secrets,
    resolve_credentials,
    resolve_infisical_config,
)
from src_etl.etl.gerenciar import carregar_credenciais

# ------------------------------------------------------------------ fakes
class _FakeUniversalAuth:
    def __init__(self, auth_error, failures_before_success, error_factory):
        self._auth_error = auth_error
        self._failures = failures_before_success
        self._error_factory = error_factory
        self.logins: list[tuple[str, str]] = []

    def login(self, client_id: str, client_secret: str) -> SimpleNamespace:
        self.logins.append((client_id, client_secret))
        if self._error_factory is not None and len(self.logins) <= self._failures:
            raise self._error_factory()
        if self._auth_error is not None:
            raise self._auth_error
        return SimpleNamespace(accessToken="tok")


class _FakeAuth:
    def __init__(self, universal_auth: _FakeUniversalAuth) -> None:
        self.universal_auth = universal_auth


class _FakeSecrets:
    def __init__(self, values, errors) -> None:
        self.values = dict(values)
        self.errors = dict(errors)
        self.calls: list[dict] = []

    def get_secret_by_name(self, secret_name, project_id=None, environment_slug=None,
                           secret_path=None, view_secret_value=True):
        self.calls.append({
            "name": secret_name,
            "project_id": project_id,
            "environment_slug": environment_slug,
            "secret_path": secret_path,
            "view_secret_value": view_secret_value,
        })
        if secret_name in self.errors:
            raise self.errors[secret_name]
        if secret_name not in self.values:
            raise APIError("not found", 404, {})
        return SimpleNamespace(secretValue=self.values[secret_name], version=3, secretPath=secret_path)


class FakeClient:
    def __init__(self, values=None, errors=None, auth_error=None,
                 failures_before_success=0, login_error_factory=None):
        self._universal_auth = _FakeUniversalAuth(
            auth_error=auth_error,
            failures_before_success=failures_before_success,
            error_factory=login_error_factory,
        )
        self.auth = _FakeAuth(self._universal_auth)
        self.secrets = _FakeSecrets(values or {}, errors or {})

    @property
    def logins(self) -> list[tuple[str, str]]:
        return self._universal_auth.logins


def _config(tmp_path: Path, **overrides) -> InfisicalConfig:
    base = {
        "host": "https://infisical.example",
        "client_id": "cid",
        "client_secret": "csecret",
        "project_id": "proj",
        "environment_slug": "dev",
        "secret_path": "/",
        "env_file_path": tmp_path / ".env",
    }
    base.update(overrides)
    return InfisicalConfig(**base)


def _segredos() -> dict[str, str]:
    return {"src-user": "alice", "src-password": "s3cr3t"}


def _limpar_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in cr.BOOTSTRAP_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in ("SRC_USER", "SRC_PASS"):
        monkeypatch.delenv(name, raising=False)


def _escrever(tmp_path: Path, conteudo: str) -> Path:
    destino = tmp_path / ".env"
    destino.write_text(conteudo, encoding="utf-8")
    return destino


# ------------------------------------------------------------------ US1: .env/ambiente primeiro (sem rede)
def test_usa_env_file_sem_consultar_infisical(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER=ana\nPASSWORD=pw\nMISTRAL_KEY=abc\n")
    original = env.read_bytes()
    client = FakeClient(values=_segredos())

    cred = resolve_credentials(env, client=client)

    assert cred.source == "env_file"
    assert (cred.user, cred.password) == ("ana", "pw")
    assert client.logins == []            # nenhuma rede
    assert env.read_bytes() == original   # arquivo intacto


def test_usa_ambiente_quando_arquivo_ausente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    monkeypatch.setenv("SRC_USER", "envuser")
    monkeypatch.setenv("SRC_PASS", "envpass")
    env = _escrever(tmp_path, "MISTRAL_KEY=abc\n")
    client = FakeClient(values=_segredos())

    cred = resolve_credentials(env, client=client)

    assert cred.source == "environment"
    assert (cred.user, cred.password) == ("envuser", "envpass")
    assert client.logins == []


def test_arquivo_tem_precedencia_sobre_ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    monkeypatch.setenv("SRC_USER", "envuser")
    monkeypatch.setenv("SRC_PASS", "envpass")
    env = _escrever(tmp_path, "USER='u1'\nPASSWORD='p1'\n")

    cred = resolve_credentials(env, config=_config(tmp_path))

    assert (cred.user, cred.password) == ("u1", "p1")
    assert cred.source == "env_file"


# ------------------------------------------------------------------ US1: runtime Infisical (sem persistir)
def test_busca_no_infisical_quando_ausente_e_nao_grava(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "MISTRAL_KEY=abc123\n")
    original = env.read_bytes()
    client = FakeClient(values={**_segredos(), "Teste": "ignorar", "sigpesq-user": "x"})

    cred = resolve_credentials(env, client=client, config=_config(tmp_path))

    assert cred.source == "infisical"
    assert (cred.user, cred.password) == ("alice", "s3cr3t")
    assert [call["name"] for call in client.secrets.calls] == ["src-user", "src-password"]
    assert env.read_bytes() == original                 # nunca grava
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # nenhum arquivo extra


def test_busca_infisical_quando_falta_um(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER=parcial\nMISTRAL_KEY=abc\n")
    client = FakeClient(values=_segredos())

    cred = resolve_credentials(env, client=client, config=_config(tmp_path))

    assert cred.source == "infisical"
    assert (cred.user, cred.password) == ("alice", "s3cr3t")
    assert len(client.logins) == 1


def test_mapeamento_fixo_le_apenas_os_dois_segredos(tmp_path: Path) -> None:
    client = FakeClient(values={**_segredos(), "Teste": "ignorar", "sigpesq-user": "x"})
    valores = fetch_required_secrets(_config(tmp_path), client=client)
    assert valores == _segredos()
    assert [call["name"] for call in client.secrets.calls] == ["src-user", "src-password"]


# ------------------------------------------------------------------ US2: falha segura
def test_segredo_ausente_levanta_erro_com_nome(tmp_path: Path) -> None:
    client = FakeClient(values={"src-user": "alice"})  # falta src-password -> 404
    with pytest.raises(CredentialsError) as excinfo:
        resolve_credentials(tmp_path / ".env", client=client, config=_config(tmp_path))
    assert "src-password" in str(excinfo.value)


def test_segredo_vazio_e_erro(tmp_path: Path) -> None:
    client = FakeClient(values={"src-user": "alice", "src-password": ""})
    with pytest.raises(CredentialsError):
        resolve_credentials(tmp_path / ".env", client=client, config=_config(tmp_path))


def test_auth_401_falha_sem_retry(tmp_path: Path) -> None:
    client = FakeClient(auth_error=APIError("unauthorized", 401, {}))
    with pytest.raises(CredentialsError):
        resolve_credentials(tmp_path / ".env", client=client, config=_config(tmp_path))
    assert len(client.logins) == 1


def test_retry_somente_em_transitorio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "wait_exponential", lambda **_: cr.wait_none())
    client = FakeClient(
        values=_segredos(),
        failures_before_success=2,
        login_error_factory=lambda: APIError("server error", 500, {}),
    )
    assert fetch_required_secrets(_config(tmp_path), client=client) == _segredos()
    assert len(client.logins) == 3


def test_retry_em_timeout_transitorio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "wait_exponential", lambda **_: cr.wait_none())
    client = FakeClient(
        values=_segredos(),
        failures_before_success=1,
        login_error_factory=lambda: TimeoutError("timed out"),
    )
    assert fetch_required_secrets(_config(tmp_path), client=client) == _segredos()


def test_retry_esgotado_levanta_erro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "wait_exponential", lambda **_: cr.wait_none())
    client = FakeClient(
        values=_segredos(),
        failures_before_success=999,
        login_error_factory=lambda: APIError("unavailable", 503, {}),
    )
    with pytest.raises(CredentialsError):
        resolve_credentials(tmp_path / ".env", client=client, config=_config(tmp_path))
    assert len(client.logins) == cr.TRANSIENT_MAX_ATTEMPTS


# ------------------------------------------------------------------ US2: mascaramento
def test_logs_mascarados(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         caplog: pytest.LogCaptureFixture) -> None:
    _limpar_bootstrap(monkeypatch)
    client = FakeClient(values=_segredos())

    with caplog.at_level(logging.INFO, logger="src_etl.credentials"):
        resolve_credentials(tmp_path / ".env", client=client, config=_config(tmp_path))

    assert "alice" not in caplog.text
    assert "s3cr3t" not in caplog.text
    assert "*" in caplog.text


# ------------------------------------------------------------------ US3: configuração de bootstrap
def test_precedencia_ambiente_sobre_arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    env = _escrever(
        tmp_path,
        "INFISICAL_ENV=fromfile\nINFISICAL_SECRET_PATH=/fromfile\n"
        "INFISICAL_CLIENT_ID=cid\nINFISICAL_CLIENT_SECRET=csecret\nINFISICAL_PROJECT_ID=proj\n",
    )
    monkeypatch.setenv("INFISICAL_ENV", "staging")

    cfg = resolve_infisical_config(env)

    assert cfg.environment_slug == "staging"
    assert cfg.secret_path == "/fromfile"


def test_padroes_host_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    env = _escrever(
        tmp_path,
        "INFISICAL_CLIENT_ID=cid\nINFISICAL_CLIENT_SECRET=csecret\nINFISICAL_PROJECT_ID=proj\n",
    )

    cfg = resolve_infisical_config(env)

    assert cfg.environment_slug == "dev"
    assert cfg.secret_path == "/"
    assert cfg.host == "https://app.infisical.com"


def test_path_sem_barra_recebe_barra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj")
    monkeypatch.setenv("INFISICAL_SECRET_PATH", "app/segredos")

    cfg = resolve_infisical_config(tmp_path / "inexistente.env")

    assert cfg.secret_path == "/app/segredos"


def test_config_ausente_mensagem_acionavel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    env = _escrever(tmp_path, "MISTRAL_KEY=abc\n")

    with pytest.raises(CredentialsError) as excinfo:
        resolve_credentials(env)

    mensagem = str(excinfo.value)
    assert "USER e PASSWORD" in mensagem
    assert "INFISICAL_CLIENT_ID" in mensagem


# ------------------------------------------------------------------ integração com o ETL
def test_carregar_credenciais_usa_arquivo(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER=alice\nPASSWORD=s3cr3t\n")
    assert carregar_credenciais(env) == ("alice", "s3cr3t")


def test_carregar_credenciais_erro_acionavel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _limpar_bootstrap(monkeypatch)
    env = _escrever(tmp_path, "MISTRAL_KEY=abc\n")
    with pytest.raises(RuntimeError) as excinfo:
        carregar_credenciais(env)
    assert "Infisical" in str(excinfo.value)


# ------------------------------------------------------------------ parser unitário
def test_parse_env_value_aspas() -> None:
    assert parse_env_value("'a#b'") == ("a#b", True)
    assert parse_env_value('"a\\nb"') == ("a\nb", True)
    assert parse_env_value("plain # comentário") == ("plain", False)


def test_load_env_values_quote_aware(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER='u#1'\nPASSWORD=\"p=w\"\n# c\nVAZIO=\n")
    valores = load_env_values(env)
    assert valores["USER"] == "u#1"
    assert valores["PASSWORD"] == "p=w"
    assert valores["VAZIO"] == ""
