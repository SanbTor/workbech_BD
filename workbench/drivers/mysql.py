"""MySQL / MariaDB (PyMySQL, só Python, instala fácil na rede corporativa). Database = schema."""
from .base import Driver, ErroBanco, categoria_tipo

SISTEMA = ("mysql", "information_schema", "performance_schema", "sys")


class MySQL(Driver):
    tipo = "mysql"
    nome = "MySQL"
    porta_padrao = 3306
    rotulo_container = "Database"
    dialeto = "mysql"

    def _abrir(self):
        import pymysql
        c = self.cfg
        kw = dict(host=(c.get("host") or "").strip(), port=int(c.get("porta") or 3306), user=c.get("usuario", ""),
                  password=c.get("senha", ""), database=(c.get("database") or "").strip() or None,
                  connect_timeout=15, charset="utf8mb4", autocommit=False)
        if self.timeout:
            kw["read_timeout"] = self.timeout + 5
        if c.get("ssl"):
            kw["ssl"] = {"fake_flag_to_enable_tls": True}
            kw["ssl_verify_cert"] = False
            kw["ssl_verify_identity"] = False
        con = pymysql.connect(**kw)
        self.descricao_driver = f"PyMySQL {pymysql.__version__}"
        with con.cursor() as cur:
            for cmd in ("SET SESSION TRANSACTION READ ONLY",
                        # limite por consulta: MySQL usa milissegundos, MariaDB usa segundos
                        f"SET SESSION MAX_EXECUTION_TIME = {int(self.timeout * 1000)}" if self.timeout else None,
                        f"SET SESSION max_statement_time = {int(self.timeout)}" if self.timeout else None):
                if cmd:
                    try:
                        cur.execute(cmd)
                    except Exception:  # noqa — variável não existe nesta versão
                        pass
        return con

    def _cursor_continuo(self):
        import pymysql
        return self.con.cursor(pymysql.cursors.SSCursor)

    def _executar(self, cur, sql, params):
        # sem parâmetros, o PyMySQL não interpreta '%' (ex.: LIKE 'A%') como marcador
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

    def abandonar(self, cur=None):
        """Fecha o socket sem baixar o restante de um resultado grande (SSCursor)."""
        try:
            res = getattr(self.con, "_result", None)
            if res is not None:
                res.unbuffered_active = False
            if cur is not None:
                cur._result = None
            self.con._force_close()
        except Exception:  # noqa
            pass
        self.con = None

    def q(self, ident):
        return "`" + str(ident).replace("`", "``") + "`"

    def sql_limite(self, sql, n):
        return f"{sql} LIMIT {int(n)}"

    def expr_data(self, params, valor, fim):
        # data já validada (AAAA-MM-DD): vai literal para não conflitar com '%' de um filtro digitado
        return f"DATE_ADD('{valor}', INTERVAL 1 DAY)" if fim else f"'{valor}'"

    def nome_tipo_cursor(self, d):
        from pymysql.constants import FIELD_TYPE
        nomes = {v: k for k, v in vars(FIELD_TYPE).items() if isinstance(v, int)}
        return nomes.get(d[1], "")

    # ---------------------------------------------------- catálogo
    def versao(self):
        return "MySQL " + str(self.todas("SELECT VERSION()")[0][0])

    def container_atual(self):
        return self.todas("SELECT DATABASE()")[0][0]

    def usar_container(self, container):
        if container:
            self.exec(f"USE {self.q(container)}").close()

    def containers(self):
        excl = ",".join(f"'{s}'" for s in SISTEMA)
        linhas = self.todas(f"SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ({excl}) "
                            "ORDER BY schema_name")
        return [{"nome": l[0]} for l in linhas]

    def objetos(self, container):
        linhas = self.todas(
            "SELECT table_name, table_type, table_rows, table_comment FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name", (container,))
        return [{"nome": n, "tipo": "VIEW" if t == "VIEW" else "TABLE",
                 "linhas_estimadas": None if t == "VIEW" else r, "comentario": (com or "") if t != "VIEW" else ""}
                for n, t, r, com in linhas]

    def buscar(self, termo, container=None, limite=200):
        b = f"%{(termo or '').strip().lower()}%"
        excl = ",".join(f"'{s}'" for s in SISTEMA)
        sql = ("SELECT table_schema, table_name, table_type FROM information_schema.tables "
               f"WHERE table_schema NOT IN ({excl}) AND LOWER(table_name) LIKE %s")
        params = [b]
        if container:
            sql += " AND table_schema = %s"
            params.append(container)
        sql += f" ORDER BY (table_schema = DATABASE()) DESC, table_schema, table_name LIMIT {int(limite)}"
        return [{"container": s, "nome": n, "tipo": "VIEW" if t == "VIEW" else "TABLE"}
                for s, n, t in self.todas(sql, params)]

    def detalhes(self, container, objeto):
        info = self.todas(
            "SELECT table_type, engine, table_rows, create_time, update_time, table_comment, "
            "data_length + index_length, table_collation FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s", (container, objeto))
        if not info:
            raise ErroBanco(f"Tabela ou view '{container}.{objeto}' não encontrada, ou sem permissão de acesso.",
                            "", "permissao")
        tt, engine, rows, criado, alterado, com, tam, coll = info[0]
        view = tt == "VIEW"
        cols = self.todas(
            "SELECT column_name, data_type, column_type, character_maximum_length, numeric_precision, "
            "numeric_scale, datetime_precision, is_nullable, column_default, column_key, extra, column_comment "
            "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (container, objeto))
        pk = [l[0] for l in self.todas(
            "SELECT column_name FROM information_schema.key_column_usage WHERE table_schema = %s "
            "AND table_name = %s AND constraint_name = 'PRIMARY' ORDER BY ordinal_position", (container, objeto))]
        colunas = []
        for i, (nome, dtype, ctype, tam_c, prec, esc, dprec, nulo, padrao, chave, extra, comc) in enumerate(cols, 1):
            colunas.append({
                "posicao": i, "nome": nome, "tipo": ctype or dtype, "tipo_base": (dtype or "").upper(),
                "tamanho": tam_c, "precisao": prec if prec is not None else dprec, "escala": esc,
                "nulo": nulo == "YES", "pk": nome in pk,
                "padrao": None if padrao is None or (padrao == "NULL" and nulo == "YES") else padrao,  # MariaDB devolve 'NULL' em texto
                "extra": " ".join(x for x in [extra or "", "UNIQUE" if chave == "UNI" else "",
                                              "INDEX" if chave == "MUL" else ""] if x).strip(),
                "comentario": comc or "", "categoria": categoria_tipo(dtype)})
        obj = {"container": container, "nome": objeto, "tipo": "VIEW" if view else "TABLE",
               "linhas_estimadas": None if view else rows, "estimativa_fonte": "information_schema.tables.table_rows",
               "comentario": "" if view else (com or ""), "extras": {}}
        if not view:
            obj["extras"] = {"Engine": engine, "Criada em": criado, "Alterada em": alterado,
                             "Tamanho (dados+índices)": _bytes(tam), "Collation": coll}
        return {"objeto": obj, "colunas": colunas, "pk": pk}

    def dicas_erro(self):
        return [
            ("1045 ", "Usuário ou senha incorretos (ou usuário sem acesso a partir deste computador).", "conexao"),
            ("1049 ", "Database não encontrado. Confira o nome ou deixe o campo em branco.", "conexao"),
            ("1044 ", "Usuário sem permissão para esse database (ou o database não existe).", "permissao"),
            ("1142 ", "Usuário sem permissão de leitura nessa tabela.", "permissao"),
            ("1143 ", "Usuário sem permissão em uma das colunas.", "permissao"),
            ("1146 ", "Tabela ou view não existe (ou sem permissão).", "consulta"),
            ("1054 ", "Coluna desconhecida na consulta. Confira os nomes das colunas.", "consulta"),
            ("1064 ", "Erro de sintaxe no SQL. Confira a consulta perto do trecho indicado nos detalhes.", "consulta"),
            ("1052 ", "Coluna ambígua: existe em mais de uma tabela. Use o prefixo da tabela.", "consulta"),
            ("1060 ", "Há colunas com o mesmo nome no resultado. Use apelidos (AS).", "consulta"),
            ("3024 ", "Tempo esgotado: a consulta passou do limite de execução.", "timeout"),
            ("1969 ", "Tempo esgotado: a consulta passou do limite de execução.", "timeout"),
            ("1792 ", "Operação bloqueada: a sessão é somente leitura.", "bloqueado"),
            ("3159 ", "O servidor MySQL exige conexão segura: marque 'Usar SSL'.", "conexao"),
            ("insecure transport", "O servidor MySQL exige conexão segura: marque 'Usar SSL'.", "conexao"),
            ("Name or service not known", "Host não encontrado. Confira o nome do servidor e se está na rede/VPN.", "conexao"),
            ("getaddrinfo failed", "Host não encontrado. Confira o nome do servidor e se está na rede/VPN.", "conexao"),
            ("2003 ", "Não foi possível alcançar o servidor MySQL. Confira host, porta (3306) e VPN.", "conexao"),
            ("2006 ", "A conexão com o servidor foi perdida. Tente de novo.", "conexao"),
            ("2013 ", "A conexão com o servidor foi perdida durante a consulta (ou o tempo esgotou).", "conexao"),
            ("2014 ", "A conexão está ocupada com outra consulta. Tente de novo.", "conexao"),
            ("Can't connect", "Não foi possível alcançar o servidor. Confira host, porta e VPN.", "conexao"),
        ]


def _bytes(n):
    if n is None:
        return None
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} PB"
