"""Oracle Database (python-oracledb). Reaproveita do Anonimizador a detecção automática do Oracle Client
(modo thick, igual ao init_oracle_client dos scripts da empresa) e a leitura da string de conexão."""
import glob
import os
import platform
import re
import threading

from .base import Driver, ErroBanco, categoria_tipo

DONOS_SISTEMA = ("SYS", "SYSTEM", "XDB", "MDSYS", "CTXSYS", "ORDSYS", "OUTLN", "DBSNMP", "WMSYS", "OLAPSYS",
                 "EXFSYS", "LBACSYS", "DVSYS", "DVF", "GSMADMIN_INTERNAL", "OJVMSYS", "AUDSYS", "ORDDATA",
                 "APPQOSSYS", "GGSYS", "DBSFWUSER", "REMOTE_SCHEDULER_AGENT", "SI_INFORMTN_SCHEMA", "ORACLE_OCM",
                 "XS$NULL", "MDDATA", "SPATIAL_CSW_ADMIN_USR", "SPATIAL_WFS_ADMIN_USR", "FLOWS_FILES",
                 "ORDPLUGINS", "DIP", "ANONYMOUS", "GSMCATUSER", "SYSBACKUP", "SYSDG", "SYSKM", "SYSRAC", "SYS$UMF",
                 "DGPDB_INT", "GSMUSER", "PDBADMIN", "OPS$ORACLE", "GSMROOTUSER", "DVSYS", "VECSYS", "BAASSYS",
                 "GGSHAREDCAP", "ORDS_METADATA", "ORDS_PUBLIC_USER", "MGMT_VIEW", "SYSMAN", "OWBSYS", "SCOTT_SYS")
_EXCL = ",".join(f"'{d}'" for d in DONOS_SISTEMA)
_FILTRO_DONOS = f"owner NOT IN ({_EXCL}) AND owner NOT LIKE 'APEX%' AND owner NOT LIKE 'FLOWS%'"

# Mesmos caminhos usados nos scripts que já funcionam na empresa, mais os padrões de instalação
CAMINHOS_CLIENT = [
    r"C:\Oracle\product\19c\dbhome_1\bin",
    r"C:\ORACLE19c\product\19.0.0\client_1\bin",
    r"C:\ORACLE\product\11.2.0\client_2\bin",
    r"C:\ORACLE\product\11.2.0\client_1\bin",
]
PADROES_CLIENT = [r"C:\Oracle*\product\*\*\bin", r"C:\app\*\product\*\*\bin", r"C:\app\product\*\*\bin",
                  r"C:\instantclient*", r"C:\Oracle*\instantclient*", r"C:\Program Files\Oracle\*\bin",
                  r"C:\Program Files\Oracle\instantclient*", r"D:\Oracle*\product\*\*\bin", r"D:\app\*\product\*\*\bin",
                  "/opt/oracle/instantclient*", "/usr/lib/oracle/*/client64/lib", "/opt/homebrew/lib/instantclient*"]

_ORA = {"pronto": False, "modo": None, "lib": None, "tentativas": [], "thin_usado": False}
_ORA_TRAVA = threading.Lock()


def _biblioteca():
    so = platform.system()
    return "oci.dll" if so == "Windows" else ("libclntsh.dylib" if so == "Darwin" else "libclntsh.so")


def candidatos_client(extra=None):
    vistos, out = set(), []
    lista = [extra] if extra else []
    for var in ("ORACLE_CLIENT_LIB", "ORACLE_HOME"):
        v = os.environ.get(var)
        if v:
            lista += [v, os.path.join(v, "bin")]
    lista += CAMINHOS_CLIENT
    for padrao in PADROES_CLIENT:
        lista += sorted(glob.glob(padrao), reverse=True)
    lista += os.environ.get("PATH", "").split(os.pathsep)
    lib = _biblioteca()
    for c in lista:
        c = (c or "").strip().strip('"')
        if not c or c.lower() in vistos:
            continue
        vistos.add(c.lower())
        if os.path.isfile(os.path.join(c, lib)):
            out.append(c)
    return out


def iniciar_client(extra=None):
    """Ativa o modo thick com o primeiro Oracle Client que funcionar; sem client, usa o modo leve (thin)."""
    import oracledb
    with _ORA_TRAVA:
        if _ORA["modo"] == "thick" or _ORA["thin_usado"]:
            return _ORA
        if os.environ.get("WORKBENCH_ORACLE_THIN") != "1":
            for c in candidatos_client(extra):
                try:
                    oracledb.init_oracle_client(lib_dir=c)
                    _ORA.update(pronto=True, modo="thick", lib=c)
                    return _ORA
                except Exception as e:  # noqa
                    _ORA["tentativas"].append(f"{c}: {str(e).splitlines()[0][:160]}")
        _ORA.update(pronto=True, modo="thin", lib=None)
        return _ORA


def dsn(c):
    """Aceita a string pronta (host:porta/SERVIÇO, nome TNS ou descritor) ou monta a partir dos campos."""
    texto = (c.get("dsn") or "").strip()
    host = (c.get("host") or "").strip()
    if not texto and re.search(r"[:/(]", host):
        texto = host  # colaram a string inteira no campo host
    if texto:
        return texto
    porta = int(c.get("porta") or 1521)
    servico = (c.get("servico") or "").strip()
    if c.get("modo_servico") == "sid":
        return f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={porta}))(CONNECT_DATA=(SID={servico})))"
    return f"{host}:{porta}/{servico}"


def host_porta(c):
    """(host, porta) para o diagnóstico de rede; (None, None) para nome TNS."""
    d = dsn(c)
    m = re.search(r"HOST\s*=\s*([^)\s]+).*?PORT\s*=\s*(\d+)", d, re.I | re.S)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"^(?:\w+://)?([^:/\s]+)(?::(\d+))?(?:/.*)?$", d)
    if m and ("/" in d or ":" in d):
        return m.group(1), int(m.group(2) or 1521)
    return None, None


class Oracle(Driver):
    tipo = "oracle"
    nome = "Oracle"
    porta_padrao = 1521
    rotulo_container = "Schema"
    dialeto = "oracle"

    def _abrir(self):
        import oracledb
        c = self.cfg
        estado = iniciar_client(c.get("instant_client"))
        if c.get("instant_client") and estado["modo"] != "thick" and estado["thin_usado"]:
            raise ErroBanco("Para usar o Oracle Client informado, encerre e abra o Workbench de novo.", "", "conexao")
        oracledb.defaults.fetch_lobs = False
        oracledb.defaults.fetch_decimals = True
        params = {"user": c.get("usuario", ""), "password": c.get("senha", ""), "dsn": dsn(c)}
        if estado["modo"] != "thick":
            params["tcp_connect_timeout"] = 15
        con = oracledb.connect(**params)
        if estado["modo"] != "thick":
            _ORA["thin_usado"] = True
        self.descricao_driver = (f"Oracle Client em {estado['lib']}" if estado["modo"] == "thick"
                                 else f"oracledb {oracledb.__version__} (modo leve)")
        if self.timeout:
            con.call_timeout = int(self.timeout * 1000)
        try:
            con.cursor().execute("SET TRANSACTION READ ONLY")
        except Exception:  # noqa
            pass
        return con

    def _cursor_continuo(self):
        cur = self.con.cursor()
        cur.arraysize = 1000
        cur.prefetchrows = 1001
        return cur

    def sql_limite(self, sql, n):
        # ROWNUM funciona em todas as versões (11g inclusive) e respeita o ORDER BY interno
        return f"SELECT * FROM ({sql}) WHERE ROWNUM <= {int(n)}"

    def expr_data(self, params, valor, fim):
        params.append(valor)
        return f"TO_DATE(:{len(params)}, 'YYYY-MM-DD')" + (" + 1" if fim else "")

    def nome_tipo_cursor(self, d):
        t = d[1]
        return (getattr(t, "name", "") or "").replace("DB_TYPE_", "")

    # ---------------------------------------------------- catálogo
    def versao(self):
        return "Oracle " + str(getattr(self.con, "version", ""))

    def container_atual(self):
        return self.todas("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM dual")[0][0]

    def usar_container(self, container):
        if container:
            self.exec(f"ALTER SESSION SET CURRENT_SCHEMA = {self.q(container)}").close()

    def containers(self):
        linhas = self.todas(f"SELECT owner, COUNT(*) FROM all_objects WHERE object_type IN ('TABLE', 'VIEW') "
                            f"AND {_FILTRO_DONOS} GROUP BY owner ORDER BY owner")
        return [{"nome": o, "objetos": n} for o, n in linhas]

    def objetos(self, container):
        sql = ("SELECT t.table_name, 'TABLE', t.num_rows, c.comments FROM all_tables t "
               "LEFT JOIN all_tab_comments c ON c.owner = t.owner AND c.table_name = t.table_name "
               "WHERE t.owner = :1 AND t.table_name NOT LIKE 'BIN$%' AND t.nested = 'NO' "
               "AND (t.iot_type IS NULL OR t.iot_type = 'IOT') "
               "UNION ALL SELECT v.view_name, 'VIEW', NULL, c.comments FROM all_views v "
               "LEFT JOIN all_tab_comments c ON c.owner = v.owner AND c.table_name = v.view_name "
               "WHERE v.owner = :2 ORDER BY 1")
        return [{"nome": n, "tipo": t, "linhas_estimadas": r, "comentario": com or ""}
                for n, t, r, com in self.todas(sql, [container, container])]

    def buscar(self, termo, container=None, limite=200):
        b = f"%{(termo or '').strip().upper()}%"
        filtro = "owner = :2" if container else _FILTRO_DONOS
        params = [b] + ([container] if container else [])
        sql = (f"SELECT * FROM (SELECT owner, object_name, object_type FROM all_objects WHERE object_type IN "
               f"('TABLE', 'VIEW') AND UPPER(object_name) LIKE :1 AND {filtro} AND object_name NOT LIKE 'BIN$%' "
               f"ORDER BY CASE WHEN owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') THEN 0 ELSE 1 END, owner, "
               f"object_name) WHERE ROWNUM <= {int(limite)}")
        return [{"container": o, "nome": n, "tipo": t} for o, n, t in self.todas(sql, params)]

    def detalhes(self, container, objeto):
        tipo = self.todas("SELECT object_type FROM all_objects WHERE owner = :1 AND object_name = :2 "
                          "AND object_type IN ('TABLE', 'VIEW')", [container, objeto])
        if not tipo:
            raise ErroBanco(f"Tabela ou view '{container}.{objeto}' não encontrada, ou sem permissão de acesso.",
                            "", "permissao")
        view = tipo[0][0] == "VIEW"
        cols = self.todas(
            "SELECT c.column_name, c.data_type, c.data_length, c.char_length, c.char_used, c.data_precision, "
            "c.data_scale, c.nullable, c.data_default, cc.comments FROM all_tab_columns c "
            "LEFT JOIN all_col_comments cc ON cc.owner = c.owner AND cc.table_name = c.table_name "
            "AND cc.column_name = c.column_name WHERE c.owner = :1 AND c.table_name = :2 ORDER BY c.column_id",
            [container, objeto])
        pk = [l[0] for l in self.todas(
            "SELECT cc.column_name FROM all_constraints k JOIN all_cons_columns cc ON cc.owner = k.owner "
            "AND cc.constraint_name = k.constraint_name AND cc.table_name = k.table_name "
            "WHERE k.owner = :1 AND k.table_name = :2 AND k.constraint_type = 'P' ORDER BY cc.position",
            [container, objeto])] if not view else []
        try:
            identidade = {l[0] for l in self.todas(
                "SELECT column_name FROM all_tab_identity_cols WHERE owner = :1 AND table_name = :2",
                [container, objeto])}
        except ErroBanco:
            identidade = set()  # Oracle 11g não tem colunas identity
        colunas = []
        for i, (nome, dtype, dlen, clen, cused, prec, esc, nulo, padrao, com) in enumerate(cols, 1):
            dtype = dtype or ""
            texto = any(k in dtype for k in ("CHAR", "VARCHAR")) and "LOB" not in dtype
            tamanho = (clen if texto and clen else dlen) if (texto or "RAW" in dtype) else None
            if texto:
                tipo_txt = f"{dtype}({tamanho}{' CHAR' if cused == 'C' and 'N' not in dtype[:1] else ''})"
            elif dtype == "NUMBER" and prec is not None:
                tipo_txt = f"NUMBER({prec}{',' + str(esc) if esc else ''})"
            elif dtype == "RAW":
                tipo_txt = f"RAW({dlen})"
            else:
                tipo_txt = dtype
            colunas.append({
                "posicao": i, "nome": nome, "tipo": tipo_txt, "tipo_base": dtype, "tamanho": tamanho,
                "precisao": prec, "escala": esc, "nulo": nulo == "Y", "pk": nome in pk,
                "padrao": (padrao.strip() if isinstance(padrao, str) else padrao) or None,
                "extra": "IDENTITY" if nome in identidade else "", "comentario": com or "",
                "categoria": categoria_tipo(dtype)})
        obj = {"container": container, "nome": objeto, "tipo": "VIEW" if view else "TABLE", "linhas_estimadas": None,
               "estimativa_fonte": "all_tables.num_rows (estatísticas)", "comentario": "", "extras": {}}
        com = self.todas("SELECT comments FROM all_tab_comments WHERE owner = :1 AND table_name = :2",
                         [container, objeto])
        obj["comentario"] = (com[0][0] if com else "") or ""
        if not view:
            t = self.todas("SELECT num_rows, last_analyzed, tablespace_name, partitioned, temporary FROM all_tables "
                           "WHERE owner = :1 AND table_name = :2", [container, objeto])
            if t:
                rows, analisado, ts, part, temp = t[0]
                obj["linhas_estimadas"] = rows
                obj["extras"] = {"Estatísticas de": analisado, "Tablespace": ts,
                                 "Particionada": "Sim" if part == "YES" else "Não",
                                 "Temporária": "Sim" if temp == "Y" else None}
        return {"objeto": obj, "colunas": colunas, "pk": pk}

    def dicas_erro(self):
        client = ("O servidor exige o Oracle Client (modo thick). Informe a pasta 'bin' do Oracle Client nas opções "
                  "avançadas (a mesma do seu script) e reabra o Workbench.")
        return [
            ("DPY-3001", client, "conexao"), ("DPY-3010", client, "conexao"), ("DPY-3015", client, "conexao"),
            ("DPI-1047", "Oracle Client encontrado, mas incompatível (32 x 64 bits) ou incompleto. Informe outra pasta.",
             "conexao"),
            ("DPY-6001", "Serviço não encontrado. Confira o Service Name (ou escolha SID).", "conexao"),
            ("DPY-6003", "SID não encontrado. Confira o SID (ou escolha Service Name).", "conexao"),
            ("DPY-4027", "Nome TNS não encontrado. Use host:porta/SERVIÇO ou confira o tnsnames.ora.", "conexao"),
            ("ORA-12154", "Nome TNS não encontrado. Use host:porta/SERVIÇO ou confira o tnsnames.ora.", "conexao"),
            ("ORA-01017", "Usuário ou senha incorretos.", "conexao"),
            ("ORA-28000", "Usuário bloqueado no Oracle.", "conexao"),
            ("ORA-28001", "A senha do usuário expirou. Troque a senha no Oracle e atualize a conexão.", "conexao"),
            ("ORA-12514", "Serviço não encontrado. Confira o Service Name (ou escolha SID).", "conexao"),
            ("ORA-12505", "SID não encontrado. Confira o SID (ou escolha Service Name).", "conexao"),
            ("ORA-12541", "Nenhum listener no host/porta informados.", "conexao"),
            ("ORA-12170", "Tempo esgotado ao conectar. Confira host, porta e VPN.", "conexao"),
            ("ORA-12545", "Host não encontrado. Confira o endereço do servidor.", "conexao"),
            ("ORA-03113", "A conexão com o servidor foi perdida. Tente de novo.", "conexao"),
            ("ORA-03114", "A conexão com o servidor foi perdida. Tente de novo.", "conexao"),
            ("DPY-4011", "A conexão com o servidor foi perdida. Tente de novo.", "conexao"),
            ("DPY-1001", "A conexão com o servidor foi perdida. Tente de novo.", "conexao"),
            ("ORA-01013", "Tempo esgotado: a consulta passou do limite de execução e foi cancelada.", "timeout"),
            ("DPY-4024", "Tempo esgotado: a consulta passou do limite de execução e foi cancelada.", "timeout"),
            ("DPI-1067", "Tempo esgotado: a consulta passou do limite de execução e foi cancelada.", "timeout"),
            ("ORA-00942", "Tabela ou view não existe, ou o usuário não tem permissão de leitura.", "permissao"),
            ("ORA-01031", "Privilégios insuficientes para essa consulta.", "permissao"),
            ("ORA-01435", "Schema (usuário) não existe.", "consulta"),
            ("ORA-00904", "Identificador inválido: coluna ou função desconhecida. No Oracle, nomes sem aspas são "
                          "maiúsculos.", "consulta"),
            ("ORA-00933", "Erro de sintaxe no SQL (comando não terminado corretamente).", "consulta"),
            ("ORA-00936", "Erro de sintaxe no SQL (expressão ausente).", "consulta"),
            ("ORA-00907", "Erro de sintaxe no SQL (parêntese direito ausente).", "consulta"),
            ("ORA-00923", "Erro de sintaxe no SQL (FROM não encontrado onde era esperado).", "consulta"),
            ("ORA-00918", "Coluna ambígua: existe em mais de uma tabela. Use o prefixo da tabela.", "consulta"),
            ("ORA-01722", "Número inválido: algum valor não pôde ser convertido para número.", "consulta"),
            ("ORA-01861", "Formato de data não corresponde ao texto informado.", "consulta"),
            ("ORA-01456", "Operação bloqueada: a transação é somente leitura.", "bloqueado"),
            # genérico do modo leve: fica por último para as causas específicas acima (serviço, SID) aparecerem antes
            ("DPY-6005", "Não foi possível alcançar o servidor. Confira host, porta e se está na rede/VPN da empresa.",
             "conexao"),
            ("ORA-00911", "Caractere inválido no SQL (no Oracle, não use ';' no fim).", "consulta"),
        ]
