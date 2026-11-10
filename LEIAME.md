# SQL Database Workbench — versão 1.0

Consulta, exploração e exportação de dados em bancos **MySQL** e **Oracle**, em modo **somente leitura**.
É a evolução do Anonimizador: mesma base técnica (Python + FastAPI local + página HTML), mesma identidade visual
e o mesmo jeito de conectar nos bancos, agora com foco em navegar, consultar e exportar.

Tudo roda no seu computador. O navegador só conversa com o servidor local (`127.0.0.1`).

## Como abrir

**Windows:** dê dois cliques em `iniciar.bat`. Na primeira vez ele localiza o Python (inclusive pelo `py`), cria o
ambiente em `%LOCALAPPDATA%\SQLWorkbench\venv` (fora do OneDrive) e instala os pacotes. Depois abre o navegador em
`http://127.0.0.1:8765`. Precisa do Python 3.10+ e do pip com acesso à rede.

**macOS/Linux:** `bash iniciar.sh`.

Para parar, use **Encerrar** ou feche a janela preta.

## Fluxos principais

**Explorar e exportar uma tabela**
Nova conexão → Testar conexão → Salvar → Conectar → escolher o database/schema → pesquisar ou navegar na árvore →
abrir a tabela → abas **Dados** e **Estrutura** → **Exportar** → escolher a coluna de data, a data inicial e a final →
Gerar arquivo → o download começa sozinho.

**Consulta manual**
Conectar → **Editor SQL** (ou Ctrl+E) → escrever o SELECT → **Executar** (Ctrl+Enter) → ver o resultado →
**Exportar resultado**.

## Conexões salvas

- Campos conforme o banco: MySQL (host, porta 3306, database opcional, usuário, senha, SSL) e Oracle (host, porta 1521,
  Service Name ou SID, usuário, senha). No Oracle também dá para colar a string de conexão do seu script
  (`host:porta/SERVIÇO`, nome TNS ou descritor).
- **Testar conexão** e **Diagnosticar** (driver → endereço → porta → login) funcionam igual ao Anonimizador.
- A senha é guardada **cifrada**: no Windows com o DPAPI do próprio Windows (só o seu usuário, neste computador, consegue
  abrir). Em outros sistemas, com uma chave local em `config/.chave-local`. A senha nunca é mostrada, nunca vai para o
  navegador e nunca aparece nos logs. Desmarque "Salvar a senha" para digitá-la a cada conexão.
- As conexões ficam em `config/conexoes.json`. Se copiar a pasta para outro computador, informe as senhas de novo
  (o DPAPI é por usuário e por máquina).
- **Oracle Client:** o Workbench procura sozinho o client instalado (as mesmas pastas do script da empresa, como
  `C:\ORACLE19c\product\19.0.0\client_1\bin`) e usa o modo completo (thick). Se estiver em outra pasta, informe a pasta
  `bin` em "Opções avançadas".

## Navegação e visualização

- Árvore com databases (MySQL) ou schemas (Oracle), tabelas e views, carregados sob demanda. Duplo clique num
  database/schema torna-o o atual.
- Pesquisa: no schema atual filtra na hora; em "Todos" pesquisa no catálogo do banco (até 200 resultados).
- **Dados:** prévia de 100 registros. Se a tabela tiver uma coluna que registra a inserção (`created_at`,
  `data_criacao`, `dt_cadastro`, `insert_date`…), a prévia traz os 100 mais recentes por ela. Se não tiver, mostra 100
  registros e avisa que não é possível saber quais são os mais recentes. O maior ID **não** é usado como critério.
- Grid com cabeçalho fixo, rolagem horizontal, NULL destacado, ordenação das linhas carregadas (clique no cabeçalho),
  filtro rápido, filtro no banco (WHERE), seleção de célula (setas), **Ctrl+C** para copiar e duplo clique para ver o
  valor completo.
- A quantidade de registros mostrada vem das estatísticas do banco (sem `COUNT(*)`). **Contar registros exatos** faz a
  contagem só quando você pede.
- **Estrutura:** coluna, tipo, tamanho, precisão, escala, nulo, PK, padrão, extra (auto_increment, identity, índice) e
  comentário.

## Editor SQL

- Realce de sintaxe, numeração de linhas, Tab/Shift+Tab para indentar, Ctrl+/ para comentar, Ctrl+Enter para executar.
- Com texto selecionado, executa a seleção. Com várias consultas separadas por `;`, executa a que está no cursor.
- Resultado paginado (500 linhas por página), tempo de execução, quantidade retornada, "Contar total" opcional.
- Histórico das últimas 30 consultas por conexão (fica só neste navegador).

## Somente leitura

- Só `SELECT` e `WITH … SELECT` são aceitos. `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `TRUNCATE`,
  `CREATE`, `GRANT`, `REVOKE`, `EXEC`/`CALL`, `SELECT … INTO`, `FOR UPDATE`, `LOCK` e afins são bloqueados.
- A validação é feita **no servidor**, antes de conectar no banco, e a sessão é aberta em modo READ ONLY quando o banco
  permite. Não existe edição de células.
- Recomendação: mesmo assim, use um usuário de banco com permissão só de leitura.

## Exportação

- Formatos herdados do Anonimizador: **CSV padrão Brasil** (`;` e vírgula decimal, abre direto no Excel),
  **CSV internacional** e **Excel (.xlsx)**.
- A exportação roda no servidor, direto do banco para o arquivo, em lotes: não usa as linhas da prévia e não carrega o
  resultado no navegador. Uma tabela de 500 mil registros com 20 mil no período exporta os 20 mil.
- O Excel aceita até 1.048.575 linhas por planilha; acima disso, use CSV.
- Os arquivos ficam na pasta `saida/`. Dá para cancelar uma exportação em andamento.

## Configuração opcional

Crie `config/workbench.json` para mudar os limites (valores padrão):

```json
{"timeout_consulta_s": 120, "timeout_metadados_s": 60, "timeout_exportacao_s": 0,
 "linhas_previa": 100, "linhas_pagina_sql": 500, "max_exportacoes_simultaneas": 2}
```

`timeout_exportacao_s: 0` significa sem limite para exportações.

## Estrutura do código

```
app.py                     servidor local (rotas, sessão ativa, jobs de exportação)
web/index.html             interface (tema e componentes do Anonimizador)
workbench/seguranca.py     validação read-only e mascaramento de credenciais
workbench/credenciais.py   cofre de senhas (DPAPI no Windows)
workbench/conexoes.py      conexões salvas
workbench/diagnostico.py   diagnóstico por etapas
workbench/exportacao.py    gravação CSV/XLSX em lotes
workbench/drivers/base.py  operações comuns (consulta, paginação, prévia, exportação, erros)
workbench/drivers/mysql.py   específico do MySQL
workbench/drivers/oracle.py  específico do Oracle (inclui detecção do Oracle Client)
```

Para incluir outro banco no futuro: criar `workbench/drivers/<banco>.py` com uma subclasse de `Driver` e registrá-la em
`workbench/drivers/__init__.py`.
