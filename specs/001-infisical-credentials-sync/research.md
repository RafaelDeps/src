# Phase 0 — Research: Resolução de credenciais do SRC (`.env` ou Infisical em runtime)

Decisões técnicas que fundamentam o `plan.md`. Revisado em 2026-10-13 após a mudança de requisito
("nunca gravar os valores obtidos em `.env` ou em qualquer outro lugar").

## R1 — Onde a resolução acontece

**Decision**: Dentro do consumidor do ETL, na função `carregar_credenciais` (`etl/gerenciar.py`), que delega a
`resolve_credentials` (novo módulo `src_etl/credentials.py`).

**Rationale**: O ponto único onde o ETL precisa das credenciais é a coleta autenticada; centralizar ali evita
acoplamento do restante do pipeline e mantém a interface existente (`tuple[str, str]`).

**Alternatives considered**:
- *Comando separado que grava o `.env` (abordagem anterior)*: rejeitado — persistia o segredo em disco, contrariando o requisito.
- *Buscar em cada ponto de uso*: rejeitado — duplicação e risco de vazamento.

## R2 — Nenhuma persistência (FR-005)

**Decision**: Os valores obtidos do Infisical vivem apenas em memória (`Credentials`), no processo da coleta. Nada é
escrito no `.env`, em temporário, cache, log ou qualquer arquivo.

**Rationale**: Elimina a exposição do segredo em disco e mantém o `.env` como opcional e intocado.

**Alternatives considered**:
- *Gravar no `.env` (abordagem anterior)*: rejeitado pelo requisito.
- *Cache em arquivo para evitar novas chamadas*: rejeitado — ainda é persistência de segredo.

## R3 — Precedência `.env`/ambiente sobre o serviço (FR-001/FR-004)

**Decision**: Se `USER` **e** `PASSWORD` existirem no `.env` (ou `SRC_USER`/`SRC_PASS` no ambiente), usam-se esses
valores e **não** há chamada ao Infisical. Caso contrário, busca-se no serviço.

**Rationale**: Permite operar offline/em CI com `.env`, e reduz chamadas de rede. O `.env` tem precedência porque
`USER` colide com a variável de ambiente do SO; o fallback de ambiente usa apenas nomes inequívocos (`SRC_*`).

**Alternatives considered**:
- *Sempre buscar no serviço*: rejeitado — quebraria o uso offline e adicionaria latência desnecessária.
- *Usar `USER`/`PASSWORD` do ambiente*: rejeitado — colide com a variável `USER` do SO.

## R4 — Leitor de `.env` quote-aware (FR-006)

**Decision**: Parser próprio em `credentials.py` (`parse_env_value`/`load_env_values`): aspas simples quase literais
(decodificam apenas `\\` e `\'`), aspas duplas processam `\n`/`\"`/`\\`, sem aspas descarta comentário inline e faz
strip; `utf-8-sig` para tolerar BOM e suporta CRLF.

**Rationale**: Garante round-trip de senhas com `#`, `=`, aspas e espaços sem depender de `python-dotenv` (que era
usado sobretudo para escrita).

**Alternatives considered**:
- *`python-dotenv` para leitura*: rejeitado — a escrita deixou de existir; menos uma dependência.
- *Split ingênuo em `=`*: rejeitado — trunca valores com `#` ou `=`.

## R5 — Retry apenas em transitórios (FR-011)

**Decision**: `tenacity` com `stop_after_attempt(4)` e `wait_exponential`; retenta `>=500`, timeouts e erros de
conexão; falha imediata em 401/403/404 e segredo ausente/vazio.

**Rationale**: Absorve blips de rede sem mascarar erros de configuração/acesso.

**Alternatives considered**:
- *Sem retry*: rejeitado — um blip exigiria reexecução manual.
- *Backoff manual*: rejeitado — `tenacity` já é a convenção do repo.

## R6 — Mascaramento por padrão (FR-008)

**Decision**: Nenhum valor bruto é logado; logs usam `mask` (2 primeiros caracteres + `*`). Não há opção de exibir
valores brutos.

**Rationale**: Segurança por padrão; como não há escrita, "conferir o que seria gravado" deixa de fazer sentido.

## R7 — Configuração de bootstrap (FR-003/FR-009)

**Decision**: `INFISICAL_HOST` (padrão `https://app.infisical.com`), `INFISICAL_CLIENT_ID`,
`INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID` (obrigatórias), `INFISICAL_ENV` (padrão `dev`),
`INFISICAL_SECRET_PATH` (padrão `/`). Precedência: ambiente > `.env` > padrão. Sem opções de linha de comando.

**Rationale**: Permite injeção por CI/segredos do runner (G2) sem editar o `.env`, e aceita o setup local.

**Alternatives considered**:
- *Somente `.env`*: rejeitado — dificulta CI.
- *Opções de CLI*: rejeitado — não há mais CLI.

## R8 — Estratégia de testes (offline)

**Decision**: Testes de unidade com cliente Infisical **falso** (sem rede). Casos: `.env`/ambiente sem rede e arquivo
intacto; runtime quando ausente e **sem criar/modificar arquivo**; mapeamento fixo; segredo ausente/vazio; 401 sem
retry; 5xx/timeout com retry; máscara nos logs; precedência de bootstrap; mensagem acionável quando falta config.

**Rationale**: Determinismo e velocidade; a única fronteira de rede fica atrás de `create_infisical_client`, que é
monkeypatched/injetada.
