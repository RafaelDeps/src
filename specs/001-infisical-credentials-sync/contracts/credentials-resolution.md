# Contract — Resolução de credenciais (`src_etl.credentials`)

Interface pública da funcionalidade: a resolução de `USER`/`PASSWORD` do SRC usada pelo ETL. Deriva de
FR-001…FR-012 e do contrato observável nos Use Cases. **Não há console script.**

## Invocação (biblioteca)

```python
from src_etl.credentials import resolve_credentials

creds = resolve_credentials(".env")      # Credentials(user, password, source)
```

Consumidor do ETL (assinatura preservada):

```python
from src_etl.etl.gerenciar import carregar_credenciais

user, senha = carregar_credenciais(".env")   # tuple[str, str]
```

## Ordem de resolução

| # | Fonte | Condição | Rede? | Grava? |
|---|---|---|---|---|
| 1 | `.env` (`USER`/`PASSWORD`) | ambos presentes | Não | Não |
| 2 | Ambiente (`SRC_USER`/`SRC_PASS`) | ambos presentes após (1) | Não | Não |
| 3 | Infisical (`src-user`/`src-password`) | (1) e (2) incompletos | Sim | **Nunca** |

`source` retornado: `env_file`, `environment` ou `infisical`.

## Variáveis de ambiente consumidas (bootstrap do Infisical)

`INFISICAL_HOST` (padrão `https://app.infisical.com`), `INFISICAL_CLIENT_ID` **(obrigatória)**,
`INFISICAL_CLIENT_SECRET` **(obrigatória)**, `INFISICAL_PROJECT_ID` **(obrigatória)**, `INFISICAL_ENV` (padrão
`dev`), `INFISICAL_SECRET_PATH` (padrão `/`). Precedência: ambiente > `.env` > padrão.

## Mapeamento fixo

| Segredo no Infisical | Chave |
|---|---|
| `src-user` | `USER` |
| `src-password` | `PASSWORD` |

Nenhum outro segredo é lido.

## Efeitos colaterais

- **Nenhum** sobre o sistema de arquivos: o `.env` é lido (opcional) e nunca escrito; nenhum arquivo é criado.
- Os valores do Infisical existem apenas em memória durante a execução.

## Saída (logging, stderr, mascarada)

```text
[src_etl.credentials] Credenciais do SRC obtidas do arquivo .env (USER=al*********).
# ou
[src_etl.credentials] Login OK (Universal Auth)
[src_etl.credentials] Credenciais do SRC obtidas do Infisical em runtime (USER=al*********); não persistidas.
```

Nunca há valor bruto em logs. Não há opção de exibir valores.

## Erros (mensagem acionável, sempre sem valores brutos)

| Condição | Comportamento | Retry? |
|---|---|---|
| `.env` sem credenciais e `INFISICAL_CLIENT_ID/SECRET/PROJECT_ID` ausente | `ConfigError` orientando preencher `USER`/`PASSWORD` no `.env` **ou** configurar `INFISICAL_*` | Não |
| Credenciais de identidade inválidas (401) | `CredentialsError` de autenticação | Não |
| Identidade sem acesso ao projeto (403) | `CredentialsError` de autorização | Não |
| Segredo `src-user`/`src-password` ausente (404) ou valor vazio | `CredentialsError` indicando o segredo | Não |
| Timeout / queda de conexão / 5xx | Repete com backoff exponencial; se esgotar, `CredentialsError` | **Sim** |

`carregar_credenciais` converte `CredentialsError` em `RuntimeError` (mesmo contrato textual do consumidor).

## Garantias testáveis

1. Com `USER`/`PASSWORD` no `.env`, há **zero** chamadas de rede e o arquivo fica byte a byte intacto (SC-001).
2. Sem credenciais no `.env`, os valores vêm do Infisical e **nenhum** arquivo é criado/modificado (SC-002/SC-004).
3. Apenas `src-user`/`src-password` são lidos; outros segredos são ignorados (FR-002).
4. Nenhum valor bruto aparece nos logs (SC-003).
5. Senhas com `#`, `=`, aspas e espaços sobrevivem à leitura (SC-006).
6. Falhas permanentes não são repetidas; transitórias são repetidas com backoff (FR-011).
