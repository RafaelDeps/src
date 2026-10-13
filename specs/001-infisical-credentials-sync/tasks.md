# Tasks: Resolução de credenciais do SRC (`.env` ou Infisical em runtime)

**Feature**: `001-infisical-credentials-sync` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode rodar em paralelo (arquivos diferentes, sem dependência).
- **[Story]**: `US1`/`US2`/`US3` (histórias do `spec.md`).

## Path Conventions

Projeto único: código em `src/src_etl/`, testes em `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Remover a dependência `python-dotenv` e o console script `src-etl-credentials` de `pyproject.toml`
- [X] T002 Garantir as dependências `infisicalsdk>=1.0.3` e `tenacity>=8.2` em `[project].dependencies`
- [X] T003 Sincronizar dependências (`uv sync --extra dev`) e confirmar `import infisical_sdk, tenacity`

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T004 [P] Criar `src/src_etl/credentials.py` com constantes (`DEFAULT_*`, `SECRET_MAPPING`, `SECRET_NAMES`) e erros de domínio (`CredentialsError`, `ConfigError`)
- [X] T005 [P] Implementar o leitor quote-aware `parse_env_value`/`load_env_values` (BOM/CRLF tolerantes) em `src/src_etl/credentials.py` (FR-006)
- [X] T006 [P] Implementar retry com `tenacity` (`_is_transient`/`_run_with_retry`) em `src/src_etl/credentials.py` (FR-011)
- [X] T007 [P] Implementar `mask` e logging mascarado por padrão em `src/src_etl/credentials.py` (FR-008)

## Phase 3: User Story 1 - Obter credenciais sem persistir (Priority: P1) 🎯 MVP

**Goal**: Usar `.env`/ambiente quando presentes (sem rede) e, caso contrário, buscar `src-user`/`src-password` no Infisical **em memória**, sem gravar nada.

**Independent Test**: Ver `quickstart.md` Cenários 1 e 2 (fingerprint do `.env` inalterado; `source` correto).

- [X] T008 [P] [US1] Implementar `resolve_infisical_config(env_path)` com precedência ambiente > `.env` > padrão e obrigatoriedade das chaves `INFISICAL_*` (FR-003/FR-009)
- [X] T009 [P] [US1] Implementar `create_infisical_client` + `fetch_required_secrets` (leitura por nome, retry em transitórios) (FR-002)
- [X] T010 [US1] Implementar `resolve_credentials(env_path, *, client, config)` com o fluxo `.env`/ambiente → Infisical e a entidade `Credentials(user, password, source)` (FR-001/FR-004/FR-005)
- [X] T011 [US1] Garantir que a resolução **não escreve** no `.env` nem cria arquivos (FR-005/FR-012)
- [X] T012 [US1] Testes offline: `.env`/ambiente sem rede e arquivo intacto; runtime sem criar/modificar arquivo; mapeamento fixo em `tests/test_credentials.py`

## Phase 4: User Story 2 - Falhar com segurança e sem expor segredos (Priority: P2)

**Goal**: Erros acionáveis (config/auth/segredo/serviço), sem valores brutos e sem escrita.

**Independent Test**: Ver `quickstart.md` Cenário 4.

- [X] T013 [US2] Garantir `ConfigError` acionável quando faltam `INFISICAL_CLIENT_ID/SECRET/PROJECT_ID` e não há credenciais no `.env` (FR-007)
- [X] T014 [US2] Tratar segredo ausente (404)/vazio como falha segura, indicando o nome do segredo (FR-007)
- [X] T015 [P] [US2] Testes de retry (só transitórios), 401 sem retry e esgotamento em `tests/test_credentials.py` (FR-011)
- [X] T016 [P] [US2] Teste de varredura de logs garantindo ausência de valores brutos em `tests/test_credentials.py` (FR-008)

## Phase 5: User Story 3 - Escolher ambiente/pasta (Priority: P3)

**Goal**: Apontar a busca em runtime para ambiente/pasta via configuração.

**Independent Test**: Variáveis `INFISICAL_ENV`/`INFISICAL_SECRET_PATH` (e `.env`) mudam a origem; padrões `dev`/`/`.

- [X] T017 [US3] Testar precedência ambiente > `.env` > padrão e normalização de `secret_path` em `tests/test_credentials.py`
- [X] T018 [P] [US3] Testar padrões (`dev`, `/`, host padrão) em `tests/test_credentials.py`

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T019 Integrar o consumidor: `carregar_credenciais` em `src/src_etl/etl/gerenciar.py` delega a `resolve_credentials` e converte `CredentialsError` em `RuntimeError` (FR-010)
- [X] T020 Remover o módulo antigo `src/src_etl/credentials_sync.py` e os helpers de parsing duplicados em `gerenciar.py`
- [X] T021 Manter os testes do leitor quote-aware em `tests/test_env_reader.py` (via `carregar_credenciais`)
- [X] T022 Atualizar `README.md` (seção de credenciais) e `.env.example` (USER/PASSWORD opcionais; bootstrap do Infisical)
- [X] T023 [P] Verificação de segurança: confirmar que nenhum valor do Infisical é persistido e que `.env` segue no `.gitignore` (FR-005/G1)
- [X] T024 [P] Manter `version` em `pyproject.toml` (`0.33.0`)
- [ ] T025 Validar `quickstart.md` (Cenários 1–5) com identidade real do Infisical e registrar os resultados

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1) → Foundational (Phase 2) → US1 (Phase 3) → US2 (Phase 4) → US3 (Phase 5) → Polish (Phase 6).

### User Story Dependencies

- US1 é o MVP e não depende de US2/US3 (a falha segura e a seleção de ambiente refinam o comportamento).
- US2 e US3 dependem apenas de US1.

### Within Each User Story

- Constantes/erros → leitor/retry → config → fetch → orquestração → testes.

### Parallel Opportunities

- T004–T007 (entidades, leitor, retry, máscara) em paralelo.
- T015/T016 e T018 em paralelo com o restante de suas fases.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 (Setup) e Phase 2 (Foundational).
2. Phase 3 (US1).
3. **STOP and VALIDATE**: `quickstart.md` Cenários 1 e 2.
4. Entregável: o ETL resolve `USER`/`PASSWORD` do `.env` **ou** do Infisical em runtime, sem persistir.

### Incremental Delivery

1. Setup + Foundational → base pronta (entidades do `data-model.md`).
2. US1 → valida → MVP.
3. US2 → falha segura e mascaramento → valida.
4. US3 → seleção de ambiente/pasta → valida.
5. Polish: integração do consumidor, remoção do fluxo antigo, docs.

### Notes

- A abordagem anterior (comando que gravava o `.env`) foi **removida**: o requisito é nunca persistir os valores.
- `credentials.py` (SDK do Infisical) é importado de forma preguiçosa; o consumidor ETL não precisa do SDK para operar só com `.env`.
- Não há `.specify/extensions.yml`; nenhum hook de extensão é executado.
