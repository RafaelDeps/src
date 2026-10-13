# Implementation Plan: Resolução de credenciais do SRC (`.env` ou Infisical em runtime)

**Branch**: `001-infisical-credentials-sync` | **Date**: 2026-10-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-infisical-credentials-sync/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

Fornecer ao ETL uma **resolução de credenciais em runtime** que:

1. usa `USER`/`PASSWORD` do `.env` (ou `SRC_USER`/`SRC_PASS` do ambiente) quando ambos existem — sem rede; e
2. caso contrário, lê `src-user`/`src-password` do Infisical via Machine Identity (Universal Auth) **em memória**, válidos apenas para a execução atual.

Os valores vindos do Infisical **nunca** são gravados no `.env` nem em qualquer outro arquivo, cache ou log (FR-005). Não há comando de escrita/sincronização: o antigo `src-etl-credentials` é removido. O consumidor (`carregar_credenciais`, em `etl/gerenciar.py`) mantém a mesma assinatura e passa a delegar à resolução. Como efeito colateral mantido, o leitor de credenciais é **quote-aware**, garantindo que senhas com `#`, `=`, aspas ou espaços sobrevivam sem truncamento (FR-006).

Abordagem técnica: mínimo de dependências — `infisicalsdk` (login + leitura por nome) e `tenacity` (retry exponencial). Sem `python-dotenv` (não há mais escrita; a leitura é feita por um parser próprio quote-aware).

## Technical Context

**Language/Version**: Python 3.10+ (declarado em `pyproject.toml`; convenção do repo 3.11+; runtime de desenvolvimento 3.14)

**Primary Dependencies**: `infisicalsdk>=1.0.3` (Universal Auth + leitura de segredos por nome), `tenacity>=8.2` (retry com backoff exponencial); já existentes: `httpx`, `pydantic`

**Storage**: nenhum — os segredos existem apenas em memória; o `.env` é **somente leitura** (opcional)

**Testing**: `pytest` (extra `dev` já existente); testes de unidade com cliente Infisical falso (injeção/monkeypatch); fixtures em formato `.env`; **sem rede**

**Target Platform**: estação de trabalho de desenvolvimento (Linux/macOS); CLI Python

**Project Type**: projeto único (pacote Python `src_etl`); sem novo console script

**Performance Goals**: com `.env` preenchido, resolução puramente local (sem rede); com Infisical, 1 login + 2 leituras por nome, sem retentativas quando o serviço responde normalmente

**Constraints**: nunca persistir segredos; somente duas credenciais por execução; mascarar por padrão; repetir apenas falhas transitórias; falhar fechado e acionável

**Scale/Scope**: 2 segredos por execução, 0 arquivos escritos, execuções por processo de coleta, usuário único

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constituição **ratificada** em `.specify/memory/constitution.md` — **v1.0.0** (2026-10-13). Os gates são
avaliados contra os princípios I–V e as duas seções adicionais; a coluna "Gate anterior" preserva a
numeração G1–G6 derivada de `AGENTS.md` referenciada em `tasks.md`.

| Princípio / Regra (v1.0.0) | Gate anterior | Situação |
|---|---|---|
| I. Extração Ética e Legal (NON-NEGOTIABLE) | — | **PASS** — acesso autorizado ao Infisical por identidade de máquina; nenhum dado sensível é persistido |
| II. HTTP Antes de Browser | — | **N/A** — não há navegador; a leitura usa o SDK do Infisical |
| III. Resiliência e Falha Rápida | G4, G5 | **PASS** — FR-011 (retry com backoff em transitórios) e FR-007 (falha segura e acionável) |
| IV. Transformação Validada e Determinística | G6 | **PASS** — mapeamento fixo; segredo vazio tratado como ausente; resolução determinística (FR-012) |
| V. Carga Idempotente e Código Observável | G1, G2, G3 | **PASS** — sem carga/escrita (nada a tornar idempotente em disco); logging estruturado mascarado; type hints; config fora do código |
| Seção: Segurança e Configuração | G1, G2 | **PASS** — segredos via env/`.env`; **nunca persistidos**; identidade de menor privilégio |
| Seção: Fluxo de Desenvolvimento e Portões de Qualidade | G5, G6 | **PASS** — testes com fixtures offline obrigatórios; type hints preservados |

**Resultado**: sem violações. Re-verificado após o design da Fase 1 — resultado inalterado; sem entradas em *Complexity Tracking*.

## Project Structure

### Documentation (this feature)

```text
specs/001-infisical-credentials-sync/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   └── credentials-resolution.md
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/src_etl/
├── credentials.py          # NOVO: resolução em runtime (Credentials, InfisicalConfig, resolve_credentials, fetch_required_secrets)
├── etl/
│   ├── gerenciar.py        # MODIFICADO: carregar_credenciais delega a resolve_credentials; leitor quote-aware movido p/ credentials.py
│   └── ...
├── cli.py                  # (inalterado) console script src-etl
└── cli_part.py             # (inalterado) consome USER/PASSWORD via gerenciar.carregar_credenciais

tests/
├── test_credentials.py     # NOVO: cliente Infisical falso; `.env` sem rede; runtime sem persistir; falha segura; retry; máscara
└── test_env_reader.py      # mantido: leitor quote-aware via carregar_credenciais

pyproject.toml              # MODIFICADO: deps (infisicalsdk, tenacity); sem console script de credenciais; remove python-dotenv
```

**Structure Decision**: Projeto único (opção padrão), estendendo o pacote existente `src_etl`.
A funcionalidade entra como um **módulo novo** `src_etl/credentials.py`. A única mudança no código
existente é `carregar_credenciais` (`src_etl/etl/gerenciar.py`) delegar à resolução e absorver o
leitor quote-aware. Nenhum novo console script; o antigo `src-etl-credentials` e o módulo
`credentials_sync.py` são removidos. O módulo expõe `resolve_credentials`, `resolve_infisical_config`,
`fetch_required_secrets`, `create_infisical_client` e as entidades `Credentials`/`InfisicalConfig` —
ver `data-model.md`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Nenhuma violação — tabela intencionalmente vazia.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
