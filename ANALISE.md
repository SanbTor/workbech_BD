# Análise do Anonimizador e plano de transformação no SQL Workbench

Base analisada: a versão mais recente do Anonimizador (2.1, com o `iniciar.bat` corrigido), recuperada da conversa
original "Sistema de anonimização de dados".

## 1. Stack e arquitetura encontradas

| Camada | Anonimizador | Uso no Workbench |
|---|---|---|
| Execução | App Python local, `iniciar.bat` acha o Python (inclusive `py`), cria o venv em `%LOCALAPPDATA%`, fora do OneDrive | Mantido (só o nome da pasta do venv mudou) |
| Backend | FastAPI + Uvicorn em `127.0.0.1`, porta livre a partir de 8765, abre o navegador | Mantido |
| Frontend | Uma página `web/index.html` com HTML, CSS e JS puros, sem framework e sem build | Mantido, sem novas bibliotecas |
| Bancos | `anonimizador/bancos.py`: Oracle (oracledb thin/thick com detecção do Oracle Client), MySQL (PyMySQL), SQL Server (pyodbc/pymssql) | Separado em `drivers/base.py` + `mysql.py` + `oracle.py`; SQL Server removido |
| Exportação | `Conexao.extrair`: grava CSV BR, CSV e XLSX direto do cursor, em lotes de 5.000 (openpyxl write_only) | Reaproveitado em `exportacao.py` |
| Período de data | `Conexao.montar`: coluna de data + De/Até, `>=` início e `<` dia seguinte ao fim; Oracle com `TO_DATE(:n)`, MySQL com data validada literal | Reaproveitado em `Driver.montar_select` |
| Leitura | `validar_select`: só SELECT/WITH, sem `;`, lista de palavras proibidas | Reforçado em `seguranca.py` (ver item 5) |
| Jobs | Thread + barra de progresso + polling `/api/job/{id}` | Reaproveitado para exportações, com cancelamento |
| Criptografia | Fernet + PBKDF2 (`chave.py`) para a chave do projeto | Fernet reaproveitado como cofre fora do Windows; DPAPI no Windows |
| Conexões salvas | `config/conexoes.json` com nome, host e usuário; senha nunca salva | Mesmo arquivo e mesmos campos, agora com senha cifrada e edição/exclusão |

## 2. Componentes visuais reaproveitados

Tema inteiro (variáveis de cor, tipografia Segoe UI, raio de 8 px, azul `#1F4FD1`), botões (`.btn`, `.prim`, `.link`),
inputs, seletor segmentado do tipo de banco (`.seg`), formulário em grade (`.grade-form`), "Opções avançadas"
(`details.avancado`), lista de diagnóstico (`.diag`), lista de projetos (`.lista-proj` → conexões salvas), barra lateral
(`.trilho`, `.nav`, `.rodape-trilho`), tabelas (`.tabela-bloco`, `th`/`td`), etapas (`.etapas` → abas Dados/Estrutura),
cartões de opção (`.modo` → "Por período / Tabela inteira"), seletor de colunas (`.colunas-banco`), progresso (`.progresso`,
`.barra`), alertas, caixa de erro e toast. A busca com atraso de digitação da tela "Chave do projeto" virou a pesquisa de
tabelas.

Componentes novos, só onde não havia equivalente: árvore do explorador, grid com cabeçalho fixo e seleção de célula,
editor SQL com realce e numeração, modal e indicador de status da conexão.

## 3. Removido

- Toda a anonimização: `motor.py`, `processos.py`, `geradores.py`, `classificador.py`, `numeros.py`, `chave.py`
  (projetos/chaves), `fmt_xlsx.py`, `fmt_pptx.py`, `fmt_pbip.py`, `fmt_texto.py`, `ooxml.py`, telas Anonimizar,
  Reverter, Termos sensíveis e Chave do projeto, exemplos e pasta `trabalho/`.
- SQL Server: opção na tela, `pyodbc`/`pymssql`, conexão, catálogo, `TOP`, `DATEADD`, mensagens de erro e as palavras
  `XP_`/`SP_` da validação.

## 4. Conexão: o que foi preservado e o que mudou

Preservado: detecção do Oracle Client e modo thick, string de conexão `host:porta/SERVIÇO`/TNS/descritor, Service Name
ou SID, SSL do MySQL, `SET TRANSACTION READ ONLY`, botão Diagnosticar com as mesmas quatro etapas e as mensagens de erro
por código (ORA-, DPY-, códigos do MySQL). Mudou a disposição dos campos, agora em seções (Identificação, Servidor,
Autenticação), com campos específicos aparecendo conforme o banco e porta padrão automática (3306/1521).

## 5. Decisões técnicas

- **Uma conexão por operação**, como no Anonimizador: evita conexões presas e "conexão perdida" depois de inatividade.
  A senha fica só na memória do servidor durante a sessão.
- **Validação read-only reforçada**: a leitura do SQL agora ignora corretamente textos, identificadores entre aspas e
  comentários (antes um `--` dentro de um texto podia esconder um segundo comando da validação), bloqueia `WITH … DELETE/UPDATE`,
  `SELECT … INTO`, `FOR UPDATE`, `LOCK`, PL/SQL (`WITH FUNCTION`) e pacotes como `UTL_FILE`/`DBMS_SQL`. A palavra `COMMENT`
  saiu da lista para não bloquear colunas com esse nome. A validação roda antes de conectar.
- **Paginação sem reescrever a consulta**: o SQL do usuário é executado como foi escrito e o servidor lê só a página
  pedida (cursor contínuo). Isso funciona mesmo com colunas repetidas no resultado, que quebrariam um `SELECT * FROM (...)`.
- **Sem COUNT(\*) automático**: a quantidade vem das estatísticas (`table_rows` no MySQL, `num_rows` no Oracle) e a
  contagem exata é um botão.
- **Prévia "mais recentes"** só quando existe coluna de data/hora com nome claramente de inserção; nunca pelo maior ID.
- **Proteção do servidor local**: só aceita requisições de `127.0.0.1`/`localhost` (bloqueia páginas de outros sites e
  DNS rebinding).
- **Credenciais**: DPAPI do Windows por meio do `ctypes` (sem dependência nova); as mensagens e os logs passam por
  mascaramento de senha e de padrões `usuario/senha@`.

## 6. Testes realizados

Com MariaDB 10.11 (tabelas de 200 mil linhas, view, BLOB, NULLs, acentos) e Oracle 23 Free (usuário só com SELECT,
coluna IDENTITY, CLOB, comentários, view):

- Os dois fluxos de conclusão completos pela interface, nos dois bancos.
- Exportação por período conferida contra `COUNT(*)` do mesmo período (10.354 registros no MySQL, 3.168 no Oracle) e
  exportação de SELECT com JOIN de 200 mil linhas para Excel.
- Comandos de escrita bloqueados, erros de sintaxe, tabela inexistente, senha errada, host inexistente, porta fechada,
  serviço Oracle inexistente, database sem permissão e tempo esgotado (MySQL e Oracle), sempre sem expor a senha.

Não testado aqui: o modo thick com Oracle Client instalado e o DPAPI, que só existem no Windows. Os dois usam o mesmo
código que já funcionava (Oracle Client) ou a API padrão do Windows (DPAPI); vale validar no primeiro uso na empresa.
