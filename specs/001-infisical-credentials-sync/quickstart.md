# Quickstart & Validation: Resolução de credenciais do SRC (`.env` ou Infisical em runtime)

Guia executável para provar que a funcionalidade atende à spec e aos contratos. Detalhes de campos e de respostas
estão em [`data-model.md`](./data-model.md) e [`contracts/credentials-resolution.md`](./contracts/credentials-resolution.md);
este arquivo não os duplica.

## Pré-requisitos

- Python 3.10+ e o projeto instalado (`uv sync` / `pip install -e '.[dev]'`) — ver `pyproject.toml`.
- Dependências da feature: `infisicalsdk`, `tenacity` (em `pyproject.toml`).
- Uma Machine Identity no Infisical com **Universal Auth** e permissão de leitura do projeto alvo.
- Segredos `src-user` e `src-password` presentes no ambiente/pasta consultado.

## Configuração (bootstrap do Infisical)

Disponha no ambiente do shell **ou** no `.env` (usadas apenas quando for preciso buscar em runtime):

```
INFISICAL_HOST=https://app.infisical.com
INFISICAL_CLIENT_ID=<machine-identity-client-id>
INFISICAL_CLIENT_SECRET=<machine-identity-client-secret>
INFISICAL_PROJECT_ID=<project-id>
INFISICAL_ENV=dev
INFISICAL_SECRET_PATH=/
```

> Ambiente tem precedência sobre o `.env` para essas chaves. `USER`/`PASSWORD` no `.env` são **opcionais**.

## Cenário 1 — `.env` preenchido (US1, P1): sem rede, sem escrita

```bash
printf 'USER=alice\nPASSWORD=s3cr3t\nMISTRAL_KEY=abc\n' > /tmp/creds.env
python -c "from src_etl.credentials import resolve_credentials; print(resolve_credentials('/tmp/creds.env'))"
```

**Esperado**:
- `source='env_file'` e valores iguais aos do arquivo.
- **Nenhuma** chamada ao Infisical (pode rodar sem rede/identidade).
- `/tmp/creds.env` inalterado (comparar bytes antes/depois).

## Cenário 2 — `.env` sem credenciais (US1, P1): runtime via Infisical, sem persistir

```bash
printf 'MISTRAL_KEY=abc\n' > /tmp/sem_creds.env
cp /tmp/sem_creds.env /tmp/sem_creds.antes
python -c "from src_etl.credentials import resolve_credentials; print(resolve_credentials('/tmp/sem_creds.env'))"
diff -u /tmp/sem_creds.antes /tmp/sem_creds.env && echo "arquivo intacto"
```

**Esperado**: `source='infisical'`, `USER`/`PASSWORD` iguais aos segredos de origem, e o `.env` **intacto**
(nenhum arquivo novo criado).

## Cenário 3 — Round-trip de caracteres especiais (FR-006)

```bash
printf "USER='a#b'\nPASSWORD='p=w#d e'\n" > /tmp/special.env
python -c "from src_etl.credentials import resolve_credentials; c=resolve_credentials('/tmp/special.env'); print((c.user, c.password))"
```

**Esperado**: `('a#b', 'p=w#d e')` — sem truncamento no `#`.

## Cenário 4 — Falha segura (US2, P2 / FR-007)

```bash
printf 'MISTRAL_KEY=abc\n' > /tmp/vazio.env
env -u INFISICAL_CLIENT_ID -u INFISICAL_CLIENT_SECRET -u INFISICAL_PROJECT_ID \
  python -c "from src_etl.etl.gerenciar import carregar_credenciais; carregar_credenciais('/tmp/vazio.env')" ; echo "exit=$?"
```

**Esperado**: `exit != 0` com mensagem orientando preencher `USER`/`PASSWORD` no `.env` **ou** configurar
`INFISICAL_*`, sem valores brutos. Repita com `INFISICAL_CLIENT_SECRET=inválido` para ver o erro 401.

## Cenário 5 — Consumo final pelo ETL (US1 / FR-010)

```bash
# com USER/PASSWORD no .env (ou INFISICAL_* configurado, sem eles)
python -m src_etl.cli_part --processo <PROCESSO> --out data/participacoes --no-headless
```

**Esperado**: o login no SRC usa as credenciais resolvidas; nenhum valor é gravado em disco durante o processo.

## Testes automatizados (offline)

```bash
pytest tests/test_credentials.py tests/test_env_reader.py -q
```

Cobrem: `.env`/ambiente sem rede e arquivo intacto, runtime via Infisical **sem criar/modificar arquivo**, mapeamento
fixo dos dois pares, robustez de leitura (BOM/CRLF/sem newline final), segredo ausente/vazio, retry apenas em
transitórios, varredura de logs mascarados e mensagem acionável quando falta configuração. Tudo com cliente Infisical
falso — **sem rede**.

## Critérios de aceite resumidos

| Cenário | Requisito | Resultado esperado |
|---|---|---|
| 1 | FR-001/FR-004 | `.env` preenchido → usa arquivo, sem rede, sem escrita |
| 2 | FR-002/FR-005 | `.env` incompleto → Infisical em runtime, nada persistido |
| 3 | FR-006 | Senha com `#`/`=`/aspas/espaços sobrevive |
| 4 | FR-007 | Falha → erro acionável, sem vazar valores |
| 5 | FR-010 | ETL consome via `carregar_credenciais`, sem nova etapa manual |
