<!--
Sync Impact Report
- Version change: placeholder template (unratified) → 1.0.0
- Ratification: initial adoption on 2026-10-13
- Modified principles (placeholders → concrete):
  - [PRINCIPLE_1_NAME] → I. Extração Ética e Legal (NON-NEGOTIABLE)
  - [PRINCIPLE_2_NAME] → II. HTTP Antes de Browser
  - [PRINCIPLE_3_NAME] → III. Resiliência e Falha Rápida
  - [PRINCIPLE_4_NAME] → IV. Transformação Validada e Determinística
  - [PRINCIPLE_5_NAME] → V. Carga Idempotente e Código Observável
- Added sections: "Segurança e Configuração" (Section 2); "Fluxo de Desenvolvimento e Portões de Qualidade" (Section 3)
- Removed sections: none
- Templates/commands requiring updates: none (dependent templates read the constitution at runtime)
- Follow-up TODOs: none
-->

# src-etl Constitution

## Core Principles

### I. Extração Ética e Legal (NON-NEGOTIABLE)

O sistema MUST extrair apenas dados públicos e para os quais exista autorização. Toda coleta MUST
respeitar o `robots.txt` e os Termos de Uso da fonte, os limites de taxa acordados e uma pausa entre
requisições, com User-Agent realista. Credenciais e dados extraídos sensíveis MUST NOT ser commitados.

Rationale: o ETL acessa sistemas de terceiros e dados de pessoas; violar esses limites acarreta bloqueio
técnico, dano legal e de reputação.

### II. HTTP Antes de Browser

A extração MUST preferir HTTP direto (`httpx`/`requests` + parser) para páginas estáticas. Playwright MUST
ser usado apenas quando houver conteúdo renderizado por JS, login ou interação indispensável. Sob
Playwright, o código MUST ser assíncrono (`async_playwright`), MUST esperar por seletores
(`wait_for_selector`) em vez de `sleep` fixo, e MUST tratar explicitamente paginação, scroll infinito e
lazy-load, reusando contexto/sessão para cookies. Em falha, MUST capturar screenshot ou HTML bruto para
depuração.

Rationale: o navegador é caro e frágil; usar o mínimo necessário reduz custo, tempo e taxa de quebra.

### III. Resiliência e Falha Rápida

Erros de rede MUST ser repetidos com backoff exponencial (`tenacity`), com limite de tentativas. Falhas de
schema/validação MUST interromper o pipeline de forma alta e explícita — MUST NOT ser silenciadas nem
tratadas como sucesso.

Rationale: recuperar de falhas transitórias evita reexecução manual; falhar cedo e alto impede propagar
dados inválidos para a carga.

### IV. Transformação Validada e Determinística

Cada registro MUST ser validado por um schema `pydantic` antes de seguir; registros inválidos MUST ser
descartados e logados, nunca gravados. Valores MUST ser normalizados: datas em ISO 8601, números com o tipo
correto e strings com `.strip()`; duplicatas MUST ser removidas por chave natural. As funções de
transformação MUST ser puras e determinísticas, com o I/O isolado nas bordas.

Rationale: validação e determinismo tornam a saída confiável, testável e reproduzível.

### V. Carga Idempotente e Código Observável

A carga MUST ser idempotente (upsert por chave natural), nunca append cego, e MUST registrar os metadados de
origem (fonte, timestamp de extração, contagem). O código MUST usar type hints, logging estruturado
(`logging`, nunca `print`) e manter toda a configuração (URLs, seletores, credenciais) fora do código, via
variáveis de ambiente ou arquivo de config. Segredos MUST NOT ser embutidos no código-fonte.

Rationale: reexecuções seguras, rastreabilidade e observabilidade são indispensáveis para operar ETL de
forma contínua.

## Segurança e Configuração

- Credenciais e segredos MUST vir de variáveis de ambiente ou de arquivo de config não versionado; o
  arquivo `.env` MUST permanecer no `.gitignore`.
- Identidades e credenciais MUST ter o menor privilégio necessário à tarefa.
- Dados extraídos sensíveis MUST NOT ser commitados no repositório.
- Dependências MUST ser declaradas em `pyproject.toml` e o ambiente de desenvolvimento MUST ser
  reprodutível (`uv`/`pip`).

## Fluxo de Desenvolvimento e Portões de Qualidade

- O pipeline MUST seguir a separação E→T→L (`extract/`, `transform/`, `load/`, `models/`, `config.py`,
  `pipeline.py`).
- Parsers e seletores MUST ser testados contra HTML real salvo (fixtures), não apenas contra o site ao vivo.
- Toda mudança MUST preservar type hints e passar na suíte de testes antes do commit.
- Revisões e PRs MUST verificar conformidade com esta constituição; a complexidade adicional MUST ser
  justificada.

## Governance

- Esta constituição supersedes as demais práticas do projeto. O `AGENTS.md` é a orientação operacional de
  runtime e MUST permanecer consistente com ela.
- Emendas MUST ser propostas via PR, descrevendo a mudança, o impacto e o plano de migração, com aprovação
  documentada antes do merge.
- Versionamento (SemVer): MAJOR para remoção ou redefinição incompatível de princípios; MINOR para novo
  princípio/seção ou expansão material; PATCH para esclarecimentos, correções de texto e refinamentos não
  semânticos.
- Conformidade: cada PR/revisão MUST verificar os princípios; violações MUST ser justificadas de forma
  explícita ou corrigidas antes do merge.

**Version**: 1.0.0 | **Ratified**: 2026-10-13 | **Last Amended**: 2026-10-13
