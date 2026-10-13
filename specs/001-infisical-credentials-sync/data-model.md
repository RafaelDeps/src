# Phase 1 — Data Model: Resolução de credenciais do SRC (`.env` ou Infisical em runtime)

Modelo conceitual (não é schema de banco — **não há persistência**). Deriva dos requisitos funcionais e das
clarificações de 2026-10-13.

---

## Entidade: `Credentials`

Par `USER`/`PASSWORD` resolvido para uma execução. **Existe apenas em memória** e é descartado ao fim da execução
(FR-005).

| Campo | Tipo | Regras |
|---|---|---|
| `user` | `str` | Não vazio (garantido pela resolução) |
| `password` | `str` | Não vazio |
| `source` | `enum` | `env_file` \| `environment` \| `infisical` — registra de onde veio (informativo/log) |

**Invariantes**:
- Nunca é gravado em arquivo, cache ou log (apenas o valor mascarado de `user` pode aparecer em log).
- A ordem de resolução é determinística (FR-012).

---

## Entidade: `InfisicalConfig`

Configuração efetiva de acesso ao serviço, resolvida por `resolve_infisical_config` a partir do ambiente e do `.env`.
Só é construída quando é preciso buscar em runtime.

| Campo | Tipo | Origem / Padrão | Regras |
|---|---|---|---|
| `host` | `str` | `INFISICAL_HOST` / `https://app.infisical.com` | Não vazio |
| `client_id` | `str` | `INFISICAL_CLIENT_ID` | **Obrigatório**, não vazio |
| `client_secret` | `str` | `INFISICAL_CLIENT_SECRET` | **Obrigatório**, não vazio; nunca logado |
| `project_id` | `str` | `INFISICAL_PROJECT_ID` | **Obrigatório**, não vazio |
| `environment_slug` | `str` | `INFISICAL_ENV` / `dev` | Não vazio |
| `secret_path` | `str` | `INFISICAL_SECRET_PATH` / `/` | Começa com `/` |
| `env_file_path` | `Path` | `.env` | Somente leitura |

**Precedência de configuração**: variável de ambiente > `.env` > padrão (R7).

---

## Entidade: `SecretMapping` (constante `SECRET_MAPPING`, imutável)

Define os **únicos** pares aceitos (FR-002). Qualquer outro segredo é ignorado.

| `source_name` (Infisical) | `target_key` | Obrigatório |
|---|---|---|
| `src-user` | `USER` | Sim |
| `src-password` | `PASSWORD` | Sim |

---

## Entidade: `SourceSecret`

Valor lido do Infisical (via `fetch_required_secrets`), mantido apenas em memória durante o mapeamento.

| Campo | Tipo | Regras |
|---|---|---|
| `name` | `str` | ∈ `{src-user, src-password}` |
| `value` | `str` | Não pode ser vazio — valor vazio é tratado como **ausente** (falha segura, FR-007) |
| `version` | `int \| None` | Informativo |
| `path` | `str` | Informativo; corresponde a `InfisicalConfig.secret_path` |

---

## Entidade: `EnvValues`

Visão de leitura do `.env` (opcional), produzida por `load_env_values(path) -> dict[str, str]` com `parse_env_value`.

| Regra | Descrição |
|---|---|
| Aspas simples | Quase literais; decodificam `\\` e `\'`; `#` é preservado (FR-006) |
| Aspas duplas | Decodificam `\n`, `\"` e `\\` |
| Sem aspas | Descarta comentário inline (`#…`) e faz strip |
| Robustez | Tolerante a BOM (`utf-8-sig`) e CRLF; linha ausente → chave ausente |

**Invariante**: `EnvValues` é **somente leitura**; nenhum valor é escrito de volta.

---

## Fluxo de resolução (`resolve_credentials`)

```text
START
  │ lê EnvValues do .env (se existir) + SRC_USER/SRC_PASS do ambiente
  ▼
USER e PASSWORD presentes?
  ├─ sim ─▶ Credentials(source = env_file | environment)  ──▶ FIM  (sem rede, sem escrita)
  └─ não
        │ monta InfisicalConfig (ambiente > .env > padrão); se faltar config ─▶ ERRO acionável
        ▼
     AUTHENTICATED ─(401/403 permanente)─▶ ERRO
        │  ▲ (transitório: retry com backoff)
        ▼  │
     FETCHED (usa somente src-user/src-password) ─(404/vazio)─▶ ERRO
        ▼
     Credentials(source = infisical) ──▶ FIM  (em memória; NADA é gravado)
```

**Regra de falha segura**: qualquer erro antes do FIM apenas levanta `CredentialsError` (mensagem acionável, sem
valores); como nada é escrito, não há estado parcial em disco (FR-007/FR-012).
