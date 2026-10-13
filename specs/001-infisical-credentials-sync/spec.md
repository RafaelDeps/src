# Feature Specification: Resolução de credenciais do SRC (`.env` ou Infisical em runtime, sem persistir)

**Feature Branch**: `001-infisical-credentials-sync`

**Created**: 2026-10-13

**Status**: Rascunho (revisado em 2026-10-13)

**Input**: User description: "O ETL deve funcionar com `USER`/`PASSWORD` preenchidos no `.env` **ou** obtidos em runtime via Infisical (Machine Identity / Universal Auth), usando os secrets `src-user`/`src-password`. Os valores vindos do Infisical **nunca** devem ser gravados no `.env` nem em nenhum outro lugar."

## Clarifications

### Session 2026-10-13

- Q: Como as credenciais do SRC devem ser obtidas? → A: O ETL usa `USER`/`PASSWORD` do `.env` (ou `SRC_USER`/`SRC_PASS` do ambiente) quando ambos existem; caso contrário, busca `src-user`/`src-password` no Infisical em runtime.
- Q: Onde os valores obtidos do Infisical são armazenados? → A: **Em lugar nenhum**: apenas em memória durante a execução. Nunca no `.env`, em arquivo, cache ou log.
- Q: Deve existir um comando para gravar/sincronizar credenciais no `.env`? → A: **Não**. O comando `src-etl-credentials` foi removido; a resolução é automática dentro do ETL.
- Q: Como preservar senhas com caracteres especiais (`#`, `=`, aspas, espaços) no `.env`? → A: Leitura quote-aware: aspas simples quase literais e aspas duplas com escapes, preservando o valor sem truncamento.
- Q: Deve haver retry em falhas? → A: Repetir erros transitórios de rede/serviço (timeout, queda de conexão, 5xx) com backoff exponencial; falhar rápido em erros permanentes (autenticação/autorização e segredo ausente).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Obter credenciais do SRC sem persistir segredos (Priority: P1)

Um operador do ETL roda a etapa autenticada (`src-etl-part`). Se o `.env` já tiver `USER`/`PASSWORD`, o ETL os usa diretamente (funciona offline). Se não tiver, o ETL busca `src-user`/`src-password` no Infisical em runtime — sem nunca gravar esses valores em disco.

**Why this priority**: É o núcleo. Permite operar tanto com `.env` local quanto sem ele, eliminando a senha persistida em disco sem exigir passos manuais.

**Independent Test**: (a) Com `USER`/`PASSWORD` no `.env`, rodar a resolução e confirmar que os valores vêm do arquivo e que **não há chamada de rede** e o arquivo fica inalterado. (b) Sem esses valores no `.env` (mas com identidade Infisical configurada), confirmar que os valores vêm do Infisical e que **nenhum arquivo é criado/modificado**.

**Acceptance Scenarios**:

1. **Given** um `.env` com `USER`/`PASSWORD`, **When** o ETL resolve as credenciais, **Then** usa os valores do arquivo, sem consultar o Infisical e sem alterar o `.env`.
2. **Given** um `.env` sem `USER`/`PASSWORD` e uma identidade com acesso, **When** o ETL resolve as credenciais, **Then** usa `src-user`/`src-password` do Infisical em memória.
3. **Given** que o serviço contém outros segredos (`sigpesq-*`, `Teste`), **When** a resolução ocorre, **Then** somente `src-user`/`src-password` são lidos.
4. **Given** qualquer execução, **When** ela termina, **Then** nenhum valor de segredo foi gravado no `.env` ou em qualquer outro arquivo.

---

### User Story 2 - Falhar com segurança e jamais expor segredos (Priority: P2)

Um operador precisa que erros de configuração ou indisponibilidade não vazem valores nem deixem o ambiente inconsistente, e que todo erro seja acionável.

**Why this priority**: Credenciais são sensíveis; um log com valor bruto ou uma falha confusa inviabilizam o uso.

**Independent Test**: Simular identidade inválida, segredo ausente e indisponibilidade; confirmar erro acionável, sem valor bruto na saída, e sem escrita em disco.

**Acceptance Scenarios**:

1. **Given** identidade inválida, segredo obrigatório ausente/vazio ou serviço inacessível, **When** a resolução ocorre, **Then** termina com erro e mensagem acionável, sem expor valores.
2. **Given** qualquer execução, **When** logs/saída são inspecionados, **Then** nenhum valor bruto de segredo aparece.
3. **Given** que o `.env` não tem credenciais e o Infisical não está configurado, **When** a resolução ocorre, **Then** a mensagem orienta preencher `USER`/`PASSWORD` no `.env` **ou** configurar `INFISICAL_*`.

---

### User Story 3 - Escolher de qual ambiente/pasta vêm as credenciais (Priority: P3)

Um operador quer apontar a busca em runtime para o ambiente/pasta corretos (`dev`/`prod`, `/`) sem editar código.

**Why this priority**: Reuso entre ambientes; o valor principal já é entregue com `dev`/`/`.

**Independent Test**: Configurar ambiente/pasta diferentes e confirmar que os valores dessas localizações são usados; sem configuração, confirmar `dev` e `/`.

**Acceptance Scenarios**:

1. **Given** ambiente/pasta configurados, **When** a resolução busca no Infisical, **Then** usa essa localização.
2. **Given** nenhuma configuração, **When** a resolução busca no Infisical, **Then** usa `dev` em `/`.

---

### Edge Cases

- `.env` ausente: não é erro por si só; se não houver credenciais em lugar algum, cai no fluxo de erro acionável.
- `.env` com apenas uma das chaves (`USER` sem `PASSWORD`): a busca em runtime ocorre e fornece o par completo.
- Valor de senha com caracteres especiais (`#`, `=`, aspas, espaços nas extremidades): preservado na leitura.
- Segredo existente porém vazio: tratado como ausente (falha segura).
- Identidade sem acesso ao projeto ou credenciais incorretas: erro claro (401/403/404), sem expor valores.
- Indisponibilidade/lentidão do serviço: erro acionável após esgotar as tentativas (FR-011).
- Colisão com a variável de ambiente `USER` do sistema: o valor do `.env` tem precedência; a busca de ambiente usa apenas `SRC_USER`/`SRC_PASS` (nomes inequívocos).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O sistema MUST obter o usuário e a senha de login do SRC a partir do `.env`/ambiente quando ambos estiverem presentes, e MUST ignorar qualquer outra credencial de origem.
- **FR-002**: Quando `USER`/`PASSWORD` não estiverem disponíveis, o sistema MUST obtê-los do serviço central de segredos, usando exatamente `src-user` e `src-password`, e MUST ignorar todos os demais segredos.
- **FR-003**: O sistema MUST autenticar-se no serviço de segredos por meio de uma identidade de máquina (não interativa), com credenciais fornecidas por configuração/ambiente (variável de ambiente > `.env`) e nunca embutidas no código-fonte.
- **FR-004**: O sistema MUST usar os valores do `.env` (ou `SRC_USER`/`SRC_PASS` do ambiente) quando ambos existirem, **sem** consultar o serviço de segredos nesse caso.
- **FR-005**: Os valores obtidos do serviço de segredos MUST existir apenas em memória durante a execução e MUST NOT ser gravados no `.env`, em qualquer arquivo, cache, log ou outra forma de persistência.
- **FR-006**: O leitor do `.env` MUST ser quote-aware, de modo que valores contendo `#`, `=`, espaços ou aspas sobrevivam à leitura sem truncamento ou alteração.
- **FR-007**: O sistema MUST falhar com segurança: diante de falha de autenticação, segredo obrigatório ausente ou vazio, ou serviço inacessível, MUST encerrar com erro e emitir mensagem acionável, sem qualquer escrita.
- **FR-008**: Por padrão, o sistema MUST mascarar os valores dos segredos em toda saída de console/log; exibição de valores brutos fica fora do escopo.
- **FR-009**: O sistema MUST permitir selecionar o ambiente e a pasta de origem dos segredos por configuração, assumindo `dev` e `/` como padrão. A precedência de resolução MUST ser: variável de ambiente > entrada no `.env` > padrão.
- **FR-010**: O sistema MUST NOT exigir novas etapas manuais dos consumidores: a etapa autenticada do ETL MUST continuar obtendo `USER`/`PASSWORD` pela mesma função (`carregar_credenciais`), com o `.env` tendo precedência sobre variáveis de ambiente.
- **FR-011**: O sistema MUST repetir automaticamente falhas transitórias de rede ou do serviço (timeout, queda de conexão, resposta 5xx) com espera exponencial, e MUST falhar imediatamente — sem repetir — em erros permanentes (autenticação/autorização e segredo obrigatório ausente).
- **FR-012**: A resolução MUST ser determinística e idempotente: entradas iguais produzem o mesmo par `USER`/`PASSWORD`, sem efeitos colaterais em disco.

### Key Entities *(include if feature involves data)*

- **Configuração de acesso ao serviço de segredos**: endereço do serviço, credenciais da identidade de máquina, identificador do projeto e seleção de ambiente/pasta.
- **Segredo de origem**: credencial nomeada no serviço (`src-user`, `src-password`) com valor e versão.
- **Credenciais resolvidas**: par `USER`/`PASSWORD` válido apenas para a execução atual, com a origem registrada (`env_file`, `environment` ou `infisical`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Com `USER`/`PASSWORD` no `.env`, o ETL resolve as credenciais **sem nenhuma chamada de rede** e sem alterar o arquivo.
- **SC-002**: Sem `USER`/`PASSWORD` no `.env`, o ETL obtém as credenciais do Infisical em runtime e autentica com sucesso, sem nenhuma gravação em disco.
- **SC-003**: Nenhum valor bruto de segredo aparece na saída/log em nenhuma execução (verificável por varredura).
- **SC-004**: 100% das execuções deixam o `.env` (e qualquer outro arquivo) inalterado; nenhum arquivo novo é criado.
- **SC-005**: Falha de configuração/acesso/serviço termina com erro acionável, sem expor valores.
- **SC-006**: Senhas contendo `#`, `=`, aspas ou espaços são lidas sem truncamento ou alteração.

## Assumptions

- O `.env` da raiz é uma fonte **opcional** de credenciais; o ETL funciona sem ele desde que o Infisical esteja configurado.
- Os nomes dos segredos de origem são exatamente `src-user` e `src-password`; demais segredos (`sigpesq-*`, `Teste`) estão fora do escopo.
- Existe uma identidade de máquina com permissão de leitura sobre o projeto, com credenciais disponíveis por variáveis de ambiente ou pelo próprio `.env`.
- Os valores obtidos do Infisical são usados apenas em memória, no processo que roda a coleta; não há persistência em nenhum formato.
- O `.env` permanece ignorado pelo controle de versão e não é publicado.
- Estão fora de escopo: gravar/rotacionar segredos, interface gráfica, fontes de segredos diferentes da adotada e execução distribuída.
