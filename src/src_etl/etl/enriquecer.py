"""Enriquecimento dos dados com a API do Mistral.

Deduz, a partir do Título + Resumo, as categorias que ficaram VAZIAS
("Grande área conhecimento", "Área temática principal"), escolhendo sempre
dentro do CONJUNTO FECHADO de categorias que já existem no sistema.

Não-destrutivo: o valor original vazio é preservado; a dedução vai em campos
novos com sufixo " (inferida)" e um bloco "_inferencia" com modelo/confiança.

Credenciais: MISTRAL_KEY (ou MISTRAL_API_KEY) no .env / ambiente.

CLI:  src-etl-enrich --acoes data/serra --min-conf 0.6
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path

import httpx

API_URL = "https://api.mistral.ai/v1/chat/completions"
MODELO_PADRAO = "mistral-small-latest"

# Área temática principal — conjunto fechado do dropdown do SRC (extensão)
AREAS_TEMATICAS = [
    "Comunicação", "Cultura", "Direitos Humanos e Justiça", "Educação",
    "Meio Ambiente", "Saúde", "Tecnologia e Produção", "Trabalho",
]

# Grande área do conhecimento — tabela CNPq
GRANDES_AREAS = [
    "Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias",
    "Ciências da Saúde", "Ciências Agrárias", "Ciências Sociais Aplicadas",
    "Ciências Humanas", "Linguística, Letras e Artes", "Outros",
]

_CHAVE_GA = "Grande área conhecimento"
_CHAVE_AT = "Área temática principal"


def carregar_chave(env_path: str | Path = ".env") -> str:
    """Lê MISTRAL_KEY / MISTRAL_API_KEY do .env (autoritativo) ou do ambiente."""
    arq: dict[str, str] = {}
    p = Path(env_path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                v = v.split("#", 1)[0].strip()  # descarta comentário inline
                arq[k] = v
    chave = (arq.get("MISTRAL_KEY") or arq.get("MISTRAL_API_KEY")
             or os.environ.get("MISTRAL_KEY") or os.environ.get("MISTRAL_API_KEY"))
    if not chave:
        raise RuntimeError("Defina MISTRAL_KEY (ou MISTRAL_API_KEY) no .env")
    return chave


def _casar(valor, permitidas: list[str]) -> str | None:
    """Casa (case-insensitive) o valor devolvido pelo modelo com o conjunto fechado.

    Tolera respostas fora do formato (dict/lista/None): devolve None.
    """
    if isinstance(valor, dict):  # modelo às vezes aninha {"categoria": ...}
        valor = valor.get("categoria") or valor.get("valor") or valor.get("nome")
    if not isinstance(valor, str):
        return None
    v = valor.strip().lower()
    for c in permitidas:
        if c.lower() == v:
            return c
    return None


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _classificar(client: httpx.Client, chave: str, modelo: str,
                 titulo: str, resumo: str, pedir_ga: bool, pedir_at: bool) -> dict:
    """Uma chamada ao Mistral pedindo JSON com as categorias faltantes."""
    alvos = []
    if pedir_ga:
        alvos.append(f'- "grande_area": UMA de {GRANDES_AREAS} (ou "INDEFINIDO")')
    if pedir_at:
        alvos.append(f'- "area_tematica": UMA de {AREAS_TEMATICAS} (ou "INDEFINIDO")')
    campos = "\n".join(alvos)
    sistema = ("Você classifica ações acadêmicas de extensão/ensino do IFES. "
               "Escolha SEMPRE dentro das listas fechadas fornecidas; nunca invente "
               "categorias. Se não houver base suficiente, use \"INDEFINIDO\". "
               "Responda APENAS um objeto JSON.")
    usuario = (
        f"Título: {titulo}\n\nResumo: {resumo}\n\n"
        f"Devolva JSON com os campos (e uma confiança 0..1 por campo):\n{campos}\n"
        'Formato: {"grande_area": "...", "confianca_grande_area": 0.0, '
        '"area_tematica": "...", "confianca_area_tematica": 0.0} '
        "(inclua só os campos pedidos)."
    )
    body = {
        "model": modelo,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": usuario}],
    }
    for tentativa in range(4):
        r = client.post(API_URL, json=body,
                        headers={"Authorization": f"Bearer {chave}"}, timeout=60)
        if r.status_code == 429:  # rate limit -> backoff
            time.sleep(2 * (tentativa + 1))
            continue
        r.raise_for_status()
        conteudo = r.json()["choices"][0]["message"]["content"]
        return json.loads(conteudo)
    raise RuntimeError("Mistral: rate limit persistente")


def enriquecer_acoes(
    acoes_dir: str | Path = "data/serra",
    *,
    modelo: str = MODELO_PADRAO,
    min_conf: float = 0.6,
    limite: int | None = None,
    on_progress=None,
) -> dict:
    """Preenche categorias vazias via Mistral (in-place, aditivo). Retorna stats."""
    log = on_progress or (lambda _m: None)
    chave = carregar_chave()
    quando = time.strftime("%Y-%m-%dT%H:%M:%S")

    arquivos = sorted(glob.glob(str(Path(acoes_dir) / "acao_*.json")))
    stats = {"total": 0, "inferidos_ga": 0, "inferidos_at": 0, "pulados": 0, "erros": 0}
    feitos = 0

    with httpx.Client() as client:
        for f in arquivos:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            # falta = original vazio E ainda sem inferência (resume real, não regasta Mistral)
            falta_ga = not (d.get(_CHAVE_GA) or "").strip() and not d.get(f"{_CHAVE_GA} (inferida)")
            falta_at = not (d.get(_CHAVE_AT) or "").strip() and not d.get(f"{_CHAVE_AT} (inferida)")
            if not (falta_ga or falta_at):
                stats["pulados"] += 1
                continue
            titulo = d.get("Título ação") or ""
            resumo = d.get("Resumo") or ""
            if not (titulo or resumo):
                stats["pulados"] += 1
                continue

            stats["total"] += 1
            try:
                res = _classificar(client, chave, modelo, titulo, resumo, falta_ga, falta_at)
                if not isinstance(res, dict):
                    raise ValueError(f"resposta inesperada: {type(res).__name__}")
                inf = {"modelo": modelo, "quando": quando}
                if falta_ga:
                    cat = _casar(res.get("grande_area"), GRANDES_AREAS)
                    conf = _num(res.get("confianca_grande_area"))
                    if cat and conf >= min_conf:
                        d[f"{_CHAVE_GA} (inferida)"] = cat
                        inf["confianca_grande_area"] = conf
                        stats["inferidos_ga"] += 1
                if falta_at:
                    cat = _casar(res.get("area_tematica"), AREAS_TEMATICAS)
                    conf = _num(res.get("confianca_area_tematica"))
                    if cat and conf >= min_conf:
                        d[f"{_CHAVE_AT} (inferida)"] = cat
                        inf["confianca_area_tematica"] = conf
                        stats["inferidos_at"] += 1
            except Exception as e:  # resposta ruim/rate limit não derruba o run
                stats["erros"] += 1
                log(f"  ! erro {Path(f).name}: {str(e)[:70]}")
                continue

            if len(inf) > 2:  # gravou ao menos uma inferência
                d["_inferencia"] = inf
                Path(f).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

            feitos += 1
            log(f"  [{feitos}] {d.get('acao_id')} ga={d.get(_CHAVE_GA+' (inferida)','-')} "
                f"at={d.get(_CHAVE_AT+' (inferida)','-')}")
            time.sleep(0.4)  # gentil com o rate limit
            if limite and feitos >= limite:
                break

    log(f"stats: {stats}")
    return stats


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        prog="src-etl-enrich",
        description="Complementa categorias vazias das ações via API do Mistral.")
    ap.add_argument("--acoes", default="data/serra", help="Diretório das ações")
    ap.add_argument("--model", default=MODELO_PADRAO, help="Modelo Mistral")
    ap.add_argument("--min-conf", type=float, default=0.6, help="Confiança mínima p/ aplicar")
    ap.add_argument("--limite", type=int, default=None, help="Máx de ações (teste)")
    args = ap.parse_args(argv)
    stats = enriquecer_acoes(args.acoes, modelo=args.model, min_conf=args.min_conf,
                             limite=args.limite,
                             on_progress=lambda m: print(f"[enrich] {m}", file=sys.stderr))
    print(f"inferidos: grande_área={stats['inferidos_ga']} "
          f"área_temática={stats['inferidos_at']} | erros={stats['erros']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
