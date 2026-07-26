"""Exporta os dados de cada página do site como arquivos JSON (API estática).

Gera, em <out>/api/, um espelho em dados do que as páginas HTML mostram:

    api/index.json                     — inventário da API + totais
    api/painel.json                    — agregados das 4 abas do painel
    api/busca.json                     — índice de busca por palavras-chave
    api/acoes/index.json               — lista resumida das ações
    api/acoes/<acao_id>.json           — uma ação + atividades + equipe
    api/atividades/<atividade_id>.json — uma atividade
    api/extensionistas/index.json      — lista de extensionistas
    api/extensionistas/<slug>.json     — trajetória + resumo IA
    api/sem-participacao.json          — ações sem participações (c/ coordenador)
    api/pendencias-relatorio.json      — ações sem relatório aprovado

Privacidade (mesma política das páginas): público-alvo aparece APENAS como
contagens/situação; equipe de execução sai como crédito público — nome, função
e vínculo; nunca CPF, e-mail ou data de nascimento.

CLI:  src-etl-export --consolidado data/serra_consolidado.json --out docs
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .extensionistas import _CACHE_PADRAO, _norm, coautoria, coletar_extensionistas
from .formados import agregar_formados
from .forproex import agregar_forproex
from .impacto import agregar_impacto
from .indicadores import agregar_indicadores
from .investimento import agregar_investimento
from .jornada import agregar_jornada
from .rede import agregar_rede
from .relatorio import _carregar_acoes, _carregar_participacoes, agregar
from .site import _agrupar_atividades
from .temas import _CACHE_DESC, agregar_temas, mapa_temas, payload_treemap_tema

_CAMPOS_ACAO = [
    "acao_id", "Processo nº", "Título ação", "Natureza", "Tipo ação",
    "Coordenador(a)", "Fomento", "Ação vinculante", "Grande área conhecimento",
    "Grande área conhecimento (inferida)", "Área temática principal",
    "Área temática principal (inferida)", "Relatório aprovado",
    "Data último relatório", "Data de cadastro", "Resumo", "Campus",
]


# ---------------------------------------------------------------- trava de PII
_RE_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_RE_11DIG = re.compile(r"(?<!\d)\d{11}(?!\d)")
_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_CHAVES_PROIBIDAS = {"CPF", "cpf", "E-mail", "Email", "email", "Nasc.", "nascimento"}


def auditar_pii(texto: str, origem: str = "") -> None:
    """Levanta RuntimeError se o texto contiver CPF, e-mail ou chave proibida.

    Trava de segurança: NENHUM JSON exportado pode conter dado pessoal de aluno.
    """
    if _RE_CPF.search(texto):
        raise RuntimeError(f"PII bloqueada (CPF formatado) em {origem}")
    if _RE_EMAIL.search(texto):
        raise RuntimeError(f"PII bloqueada (e-mail) em {origem}")
    if _RE_11DIG.search(texto):
        raise RuntimeError(f"PII bloqueada (11 dígitos, possível CPF) em {origem}")
    for chave in _CHAVES_PROIBIDAS:
        if f'"{chave}"' in texto:
            raise RuntimeError(f"PII bloqueada (chave '{chave}') em {origem}")


def _grava(path: Path, dados) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=2, default=list)
    auditar_pii(texto, str(path))   # nunca grava JSON com PII
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(texto, encoding="utf-8")


def _equipe_publica(membros: list[dict]) -> list[dict]:
    """Equipe como crédito público — sem CPF/e-mail/nascimento."""
    return [{"nome": (m.get("Nome") or "").strip(),
             "funcao": (m.get("Função") or "").strip(),
             "vinculo": (m.get("Vínculo") or "").strip()} for m in membros]


def _atividade_json(a: dict, aid: str, m: dict) -> dict:
    return {
        "atividade_id": aid,
        "numero": m["num"],
        "atividade": m["nome"],
        "acao_id": a.get("acao_id"),
        "acao_titulo": a.get("Título ação"),
        "processo": a.get("Processo nº"),
        "coordenador_acao": a.get("Coordenador(a)"),
        "publico_alvo": {"total": m["pub"], "aprovados": m["aprov"],
                         "certificados": m["cert"],
                         "situacao": dict(m["situ"])},
        "equipe_execucao": _equipe_publica(m["eq"]),
    }


def _acao_json(a: dict) -> dict:
    ativm = _agrupar_atividades(a)
    equipe: dict[str, dict] = {}
    for p in a.get("participacoes", []):
        if not p.get("tipo", "").startswith("Público"):
            nome = (p.get("Nome") or "").strip()
            e = equipe.setdefault(nome, {"nome": nome, "funcoes": set(), "vinculo":
                                         (p.get("Vínculo") or "").strip()})
            e["funcoes"].add((p.get("Função") or "").strip())
    dados = {k: a.get(k) for k in _CAMPOS_ACAO if a.get(k) is not None}
    dados["total_participacoes"] = a.get("total_participacoes", 0)
    dados["publico_alvo_total"] = sum(m["pub"] for m in ativm.values())
    dados["atividades"] = [
        {"atividade_id": aid, "numero": m["num"], "atividade": m["nome"],
         "publico": m["pub"], "aprovados": m["aprov"], "certificados": m["cert"],
         "equipe": len(m["eq"])}
        for aid, m in sorted(ativm.items(), key=lambda kv: kv[1]["num"] or "")]
    dados["equipe_execucao"] = sorted(
        ({**e, "funcoes": sorted(e["funcoes"])} for e in equipe.values()),
        key=lambda x: x["nome"])
    return dados


def _agregar_pendencias(cons: dict, slugs: dict) -> dict:
    """Espelho em dados da página pendencias-relatorio.html.

    Duas listas — `com` (pendentes com participação) e `zero` (sem participação) —
    mais o ranking de coordenadores com pendências. Colunas: Ação · Tipo ·
    Coordenador(a)(+slug) · Início · Término · Público · Equipe · Últ. relatório ·
    Modelo. Datas derivadas das participações (a ação não traz início/término).
    """
    from datetime import datetime as _dt

    def _d(s):
        try:
            return _dt.strptime((s or "").strip(), "%d/%m/%Y")
        except (ValueError, AttributeError):
            return None

    itens = []
    for a in cons["acoes"]:
        pub, eq, inis, terms = set(), set(), [], []
        for p in a.get("participacoes", []):
            di, dtm = _d(p.get("Início")), _d(p.get("Término"))
            if di:
                inis.append(di)
            if dtm:
                terms.append(dtm)
            if (p.get("tipo") or "").startswith("Públic"):
                pid = p.get("CPF") or p.get("Nome")
                if pid:
                    pub.add(pid)
            else:
                nm = (p.get("Nome") or "").strip()
                if nm:
                    eq.add(nm)
        coord = (a.get("Coordenador(a)") or "—").strip()
        pendente = (a.get("Relatório aprovado") or "").strip().lower() != "sim"
        itens.append({
            "acao_id": a.get("acao_id"), "titulo": a.get("Título ação"),
            "tipo": a.get("Tipo ação"), "coordenador": coord,
            "coordenador_slug": slugs.get(_norm(coord)),
            "ano": (a.get("Data de cadastro") or "")[-4:],
            "inicio": min(inis).strftime("%d/%m/%Y") if inis else "",
            "termino": max(terms).strftime("%d/%m/%Y") if terms else "",
            "ultimo": a.get("Data último relatório") or "nunca enviado",
            "pendente": pendente, "pub": len(pub), "eq": len(eq)})
    itens.sort(key=lambda x: (x["coordenador"], x["ano"]))
    com = [x for x in itens if x["pendente"] and x["pub"] + x["eq"] > 0]
    zero = [x for x in itens if x["pub"] + x["eq"] == 0]
    leaderboard = Counter(i["coordenador"] for i in itens if i["pendente"]).most_common(12)
    return {"com": com, "zero": zero, "leaderboard": leaderboard}


def _gravar_llms_txt(out_dir: Path, stats: dict) -> None:
    """Gera docs/llms.txt (padrão llms.txt) descrevendo o acervo para uma IA."""
    ac, at, ex = stats["acoes"], stats["atividades"], stats["extensionistas"]
    txt = f"""# Extensão IFES Campus Serra — Dados Abertos (SRC)

> Acervo público das ações de extensão e ensino do IFES Campus Serra, extraído do
> SRC (Sistema de Registro e Emissão de Certificados, src.ifes.edu.br).
> {ac} ações, {at} atividades, {ex} extensionistas. SEM dados pessoais de alunos:
> público-alvo aparece só como contagens; equipe como crédito público
> (nome/função/vínculo). Nunca CPF, e-mail ou data de nascimento. JSON UTF-8.

Os links abaixo são relativos a esta pasta (onde está o llms.txt). Endpoints com
`<...>` são padrões — troque pelo identificador desejado.

## Pacote completo (ZIP)

- [dados-abertos.zip](dados-abertos.zip): toda a API JSON + este llms.txt + os modelos de
  relatório (.odt/.pdf). Reempacotado automaticamente toda semana pelo CI.

## Conjuntos de dados

- [Inventário da API](api/index.json): índice de endpoints e totais — ações, atividades, extensionistas, alunos únicos e participações. Basta este arquivo para exibir os números gerais.
- [Painel agregado](api/painel.json): visão geral, indicadores, rede de programas, formados e impacto (agregados).
- [Investimento](api/investimento.json): iniciativas por nicho × impacto (público) × status (ativa/dormente), com onde investir, o que reativar e nota sobre impacto não-contável (ex.: ConectaFapes).
- [Ações — lista](api/acoes/index.json): as {ac} ações resumidas (id, processo, título, tipo, natureza, coordenador, ano, total de participações).
- [Extensionistas — completo](api/extensionistas/todos.json): as {ex} pessoas com trajetória (ações coordenadas e em equipe), colaboradores e resumo gerado por IA.
- [Extensionistas — lista](api/extensionistas/index.json): índice de extensionistas.
- [Busca — índice](api/busca.json): blob de palavras-chave por ação (título, coordenador, área, fomento, resumo).
- [Ações sem participações](api/sem-participacao.json): ações com público-alvo e equipe zerados.
- [Pendências de relatório](api/pendencias-relatorio.json): ações sem relatório final aprovado.

## Por item (um arquivo cada)

- `api/acoes/<acao_id>.json` — uma ação: metadados + lista de atividades (público em contagens) + equipe de execução ({ac} arquivos).
- `api/atividades/<atividade_id>.json` — uma atividade: público-alvo (total/aprovados/certificados/situação) + equipe ({at} arquivos).
- `api/extensionistas/<slug>.json` — uma pessoa: anos, funções, ações coordenadas, participações em equipe, colaboradores, resumo IA ({ex} arquivos).

## Campos principais

- Ação: `acao_id`, `Processo nº`, `Título ação`, `Natureza` (Extensão/Ensino), `Tipo ação`, `Coordenador(a)`, `Fomento`, `Grande área conhecimento`, `Área temática principal`, `Relatório aprovado`, `Data de cadastro`, `Resumo`, `total_participacoes`, `publico_alvo_total`, `atividades[]`, `equipe_execucao[]`.
- Atividade: `atividade_id`, `numero`, `atividade`, `acao_id`, `publico_alvo{{total,aprovados,certificados,situacao}}`, `equipe_execucao[]`.
- Extensionista: `slug`, `nome`, `resumo_ia`, `anos[]`, `funcoes[]`, `acoes_coordenadas[]`, `participacoes_equipe[]`, `colaboradores[]`.

## Privacidade

Sem CPF, e-mail ou data de nascimento. Público-alvo só como contagens
(total, aprovados, certificados, situação) — nunca nomes. Equipe de execução
como crédito público: nome, função e vínculo. A exportação tem trava automática
que aborta se qualquer PII vazar.

## Fonte

SRC/Ifes — https://src.ifes.edu.br. Gerado pela biblioteca `src_etl`
(github.com/ifesserra-lab/src). Página web: dados-abertos.html.
"""
    auditar_pii(txt, "llms.txt")
    (out_dir / "llms.txt").write_text(txt, encoding="utf-8")


def exportar_api(
    consolidado_json: str | Path = "data/serra_consolidado.json",
    acoes_dir: str | Path = "data/serra",
    part_dir: str | Path = "data/participacoes",
    formandos_dir: str | Path = "data/formandos",
    out_dir: str | Path = "docs",
) -> dict:
    """Gera a API JSON estática em <out_dir>/api. Retorna contagens."""
    cons = json.loads(Path(consolidado_json).read_text(encoding="utf-8"))
    api = Path(out_dir) / "api"

    # ações (uma a uma + índice)
    indice_acoes = []
    n_ativ = 0
    for a in cons["acoes"]:
        _grava(api / "acoes" / f"{a.get('acao_id')}.json", _acao_json(a))
        indice_acoes.append({
            "acao_id": a.get("acao_id"), "processo": a.get("Processo nº"),
            "titulo": a.get("Título ação"), "tipo": a.get("Tipo ação"),
            "natureza": a.get("Natureza"),
            "coordenador": (a.get("Coordenador(a)") or "").strip(),
            "ano": (a.get("Data de cadastro") or "")[-4:],
            "total_participacoes": a.get("total_participacoes", 0),
            "url": f"api/acoes/{a.get('acao_id')}.json"})
        for aid, m in _agrupar_atividades(a).items():
            if aid and aid != "?":
                _grava(api / "atividades" / f"{aid}.json", _atividade_json(a, aid, m))
                n_ativ += 1
    _grava(api / "acoes" / "index.json", indice_acoes)

    # extensionistas
    resumos = {}
    if Path(_CACHE_PADRAO).exists():
        resumos = json.loads(Path(_CACHE_PADRAO).read_text(encoding="utf-8"))
    pessoas = coletar_extensionistas(cons)
    _co = coautoria(cons)
    _sl = {_norm(p["nome"]): p["slug"] for p in pessoas}
    _tmap = mapa_temas(cons)   # {acao_id: tema/cluster} p/ "Temas de atuação"
    idx_ext, todos_ext = [], []
    for p in pessoas:
        # impacto (mesma conta honesta de extensionistas._impacto_extensionistas):
        #  coordenou → público da ação inteira; equipe → público das atividades em que
        #  atuou (nível atividade), só quando NÃO coordena a mesma ação (evita duplicar).
        coord_ids = {r["acao_id"] for r in p["coordena"]}
        imp_por_acao: dict[str, int] = {}
        for at in p.get("atividades", []):
            imp_por_acao[at["acao_id"]] = imp_por_acao.get(at["acao_id"], 0) + at.get("pub", 0)
        imp_coord = sum(r.get("pub", 0) for r in p["coordena"])
        imp_eq = sum(v for aid, v in imp_por_acao.items() if aid not in coord_ids)
        # temas de atuação: clusters das ações que coordenou/participou
        _tc: Counter = Counter()
        for r in [*p["coordena"], *p["participa"]]:
            tm = _tmap.get(r["acao_id"])
            if tm:
                _tc[tm] += 1
        temas_pessoa = [{"tema": t, "n": n} for t, n in _tc.most_common()]
        registro = {
            "slug": p["slug"], "nome": p["nome"],
            "resumo_ia": resumos.get(p["slug"]),
            "anos": p["anos"], "funcoes": p["funcoes"],
            "acoes_coordenadas": p["coordena"],
            "participacoes_equipe": p["participa"],
            "atividades": p.get("atividades", []),
            "imp_coord": imp_coord, "imp_eq": imp_eq, "impacto": imp_coord + imp_eq,
            "temas": temas_pessoa,
            "colaboradores": [{"nome": cn, "slug": _sl.get(_norm(cn)), "acoes_comuns": cnt}
                              for cn, cnt in _co.get(_norm(p["nome"]), Counter()).most_common()]}
        _grava(api / "extensionistas" / f"{p['slug']}.json", registro)
        todos_ext.append(registro)
        idx_ext.append({"slug": p["slug"], "nome": p["nome"],
                        "funcoes": p["funcoes"], "anos": p["anos"],
                        "coordena": len(p["coordena"]), "equipe": len(p["participa"]),
                        "imp_coord": imp_coord, "imp_eq": imp_eq, "impacto": imp_coord + imp_eq,
                        "url": f"api/extensionistas/{p['slug']}.json"})
    _grava(api / "extensionistas" / "index.json", idx_ext)
    # lista completa (todos os extensionistas com trajetória e resumo) num só arquivo
    _grava(api / "extensionistas" / "todos.json", todos_ext)

    # agregados do painel (4 abas)
    acoes_raw = _carregar_acoes(acoes_dir)
    parts_raw = _carregar_participacoes(part_dir)
    a_rel = agregar(acoes_raw, parts_raw)
    a_ind = agregar_indicadores(cons)
    a_net = agregar_rede(cons)
    try:
        a_form = agregar_formados(cons, formandos_dir)
    except Exception:
        a_form = None
    a_imp = agregar_impacto(cons)
    try:
        a_fpx = agregar_forproex(cons, formandos_dir=formandos_dir)
    except Exception as e:      # FORPROEX depende dos formados (xlsx); nunca quebra o export
        print("forproex:", e)
        a_fpx = None
    _grava(api / "painel.json", {"visao_geral": a_rel, "indicadores": a_ind,
                                 "rede_programas": a_net, "formados": a_form,
                                 "impacto": a_imp, "forproex": a_fpx})

    # análise de investimento (nicho × impacto × status) — espelho da página
    _grava(api / "investimento.json", agregar_investimento(cons))

    # temas & clusters (treemap tema › tipo › iniciativa + cards por cluster)
    temas_desc = {}
    if _CACHE_DESC.exists():
        temas_desc = json.loads(_CACHE_DESC.read_text(encoding="utf-8"))
    _grava(api / "temas.json", {
        "treemap": payload_treemap_tema(cons),
        "temas": [{**t, "resumo": temas_desc.get(t["tema"])} for t in agregar_temas(cons, _sl)]})

    # jornada do formado (ingresso → 1ª extensão → formatura) + público aluno×não-aluno
    try:
        _grava(api / "jornada.json", agregar_jornada(cons, formandos_dir))
    except Exception as e:      # depende dos formados (xlsx)
        print("jornada:", e)

    # listas de gestão (pendências + sem participação) — espelho da página única
    pend = _agregar_pendencias(cons, _sl)
    _grava(api / "pendencias-relatorio.json", pend)
    # compat: lista achatada só das ações sem participação (usada pela API/dados abertos)
    _grava(api / "sem-participacao.json", [
        {"acao_id": x.get("acao_id"), "titulo": x.get("Título ação"),
         "tipo": x.get("Tipo ação"),
         "coordenador": (x.get("Coordenador(a)") or "").strip(),
         "ano": (x.get("Data de cadastro") or "")[-4:]}
        for x in cons["acoes"] if x.get("total_participacoes", 0) == 0])

    # índice de busca (mesmo blob das páginas)
    _grava(api / "busca.json", [
        {"acao_id": a.get("acao_id"), "titulo": a.get("Título ação"),
         "coordenador": (a.get("Coordenador(a)") or "").strip(),
         "tipo": a.get("Tipo ação"), "natureza": a.get("Natureza"),
         "ano": (a.get("Data de cadastro") or "")[-4:],
         "area_tematica": a.get("Área temática principal")
                          or a.get("Área temática principal (inferida)"),
         "grande_area": a.get("Grande área conhecimento")
                        or a.get("Grande área conhecimento (inferida)"),
         "fomento": a.get("Fomento"), "processo": a.get("Processo nº"),
         "resumo": (a.get("Resumo") or "")[:400]}
        for a in cons["acoes"]])

    stats = {"acoes": len(indice_acoes), "atividades": n_ativ,
             "extensionistas": len(pessoas)}
    _gravar_llms_txt(Path(out_dir), stats)
    _grava(api / "index.json", {
        "descricao": "API estática do painel de Extensão — SRC/Ifes Campus Serra",
        "privacidade": "Sem dados pessoais de alunos (público-alvo só contagens); "
                       "equipe como crédito público (nome/função/vínculo).",
        "endpoints": ["api/painel.json", "api/investimento.json", "api/temas.json",
                      "api/jornada.json", "api/busca.json",
                      "api/acoes/index.json",
                      "api/acoes/<acao_id>.json", "api/atividades/<atividade_id>.json",
                      "api/extensionistas/index.json", "api/extensionistas/todos.json",
                      "api/extensionistas/<slug>.json",
                      "api/sem-participacao.json", "api/pendencias-relatorio.json"],
        **stats,
        # totais de alcance no próprio índice: quem só quer exibir os números
        # gerais (a home da organização, por exemplo) não precisa baixar o
        # painel.json inteiro (~49 KB) por causa de dois inteiros.
        "alunos_unicos": a_ind.get("alunos_unicos"),
        "total_publico": a_ind.get("total_publico")})
    return stats


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="src-etl-export",
                                 description="Exporta os dados das páginas como JSON (API estática).")
    ap.add_argument("--consolidado", default="data/serra_consolidado.json")
    ap.add_argument("--acoes", default="data/serra")
    ap.add_argument("--part", default="data/participacoes")
    ap.add_argument("--formandos", default="data/formandos")
    ap.add_argument("--out", default="docs")
    args = ap.parse_args(argv)
    s = exportar_api(args.consolidado, args.acoes, args.part, args.formandos, args.out)
    print(f"API JSON gerada em {args.out}/api: {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
