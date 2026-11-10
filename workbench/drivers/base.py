"""Camada comum dos bancos suportados.

Cada banco (mysql.py, oracle.py) implementa só o que é realmente diferente: como conectar, o catálogo
(databases/schemas, tabelas, views, colunas, chaves) e alguns detalhes de SQL (aspas, limite, datas).
Execução de consultas, paginação, prévia, exportação e mensagens de erro ficam aqui, uma vez só.
Para adicionar um terceiro banco: criar um arquivo com uma subclasse de Driver e registrá-la em __init__.py.
"""
import datetime as dt
import decimal
import re
import time

from ..seguranca import ComandoBloqueado, mascarar, validar_condicao, validar_select

LIMITE_TEXTO_TELA = 2000          # caracteres de uma célula enviados ao navegador
LOTE_EXPORTACAO = 5000


class ErroBanco(Exception):
    """Erro com mensagem amigável (msg), detalhe técnico já mascarado e categoria para a interface."""

    def __init__(self, msg, detalhe="", categoria="consulta"):
        super().__init__(msg)
        self.msg, self.detalhe, self.categoria = msg, detalhe, categoria


class Cancelado(Exception):
    pass


# ----------------------------------------------------------------- coluna de ordem de inserção

NOMES_INSERCAO = [
    "createdat", "createdon", "createdate", "createddate", "creationdate", "datecreated", "dtcreated",
    "createdtimestamp", "createtime", "createdtime", "insertdate", "insertedat", "inserttimestamp", "dtinsert",
    "datacriacao", "dtcriacao", "dhcriacao", "datcriacao", "tscriacao", "timestampcriacao", "criadoem",
    "datacadastro", "dtcadastro", "dhcadastro", "cadastradoem", "datainclusao", "dtinclusao", "dhinclusao",
    "incluidoem", "datainsercao", "dtinsercao", "dhinsercao", "dataregistro", "dtregistro", "dhregistro",
]
_EVENTO = {"criacao", "creation", "created", "create", "cadastro", "inclusao", "insercao", "insert", "inserted",
           "criado", "incluido", "cadastrado"}
_MOMENTO = {"data", "dt", "dh", "dat", "date", "at", "on", "em", "timestamp", "ts", "hora", "time"}


def _norm(nome):
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", str(nome)) if not unicodedata.combining(c))
    return s.lower()


def coluna_insercao(colunas):
    """Procura uma coluna de data/hora que registre claramente o momento da inserção.
    Nunca usa o maior ID: sem uma coluna assim, a prévia fica sem ordem (e a tela avisa)."""
    datas = [c for c in colunas if c.get("categoria") == "data"]
    por_nome = {re.sub(r"[^a-z0-9]", "", _norm(c["nome"])): c for c in datas}
    for n in NOMES_INSERCAO:
        if n in por_nome:
            return por_nome[n]["nome"]
    for c in datas:
        partes = set(re.split(r"[^a-z0-9]+", _norm(c["nome"]))) - {""}
        if partes & _EVENTO and partes & _MOMENTO:
            return c["nome"]
    return None


# ----------------------------------------------------------------- conversões de valores

def para_tela(v):
    """Valor de uma célula para JSON. None continua None (a tela mostra NULL)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else str(v)
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else str(v)
    if isinstance(v, decimal.Decimal):
        return _num_txt(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"bin": len(bytes(v))}
    if hasattr(v, "read"):  # LOB
        try:
            v = v.read()
        except Exception:  # noqa
            return "<LOB>"
        return para_tela(v)
    s = str(v)
    return s if len(s) <= LIMITE_TEXTO_TELA else {"txt": s[:LIMITE_TEXTO_TELA], "total": len(s)}


def _num_txt(v):
    if isinstance(v, decimal.Decimal):
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(v, float):
        return repr(v) if v != int(v) else str(int(v))
    return str(v)


def categoria_tipo(tipo):
    t = (tipo or "").upper()
    if "INTERVAL" in t:
        return "outro"
    if t.startswith(("DATE", "DATETIME", "TIMESTAMP", "SMALLDATETIME")):
        return "data"
    if any(k in t for k in ("BLOB", "BINARY", "RAW", "BFILE", "IMAGE")) and "CLOB" not in t:
        return "binario"
    if any(k in t for k in ("NUMBER", "INT", "DEC", "NUMERIC", "FLOAT", "REAL", "DOUBLE", "BIT", "BOOL")):
        return "numero"
    if any(k in t for k in ("CHAR", "TEXT", "CLOB", "ENUM", "SET", "JSON", "XML", "LONG")):
        return "texto"
    return "outro"


# ----------------------------------------------------------------- driver base

class Driver:
    tipo = ""
    nome = ""
    porta_padrao = 0
    rotulo_container = "Schema"       # MySQL: Database; Oracle: Schema
    dialeto = ""

    def __init__(self, cfg, timeout=60):
        self.cfg = cfg
        self.timeout = timeout          # segundos por consulta (0 = sem limite)
        self.con = None
        self.descricao_driver = ""

    # ---------------------------------------------------- implementar em cada banco
    def _abrir(self):
        raise NotImplementedError

    def versao(self):
        raise NotImplementedError

    def container_atual(self):
        raise NotImplementedError

    def containers(self):
        raise NotImplementedError

    def objetos(self, container):
        raise NotImplementedError

    def buscar(self, termo, container=None, limite=200):
        raise NotImplementedError

    def detalhes(self, container, objeto):
        raise NotImplementedError

    def usar_container(self, container):
        raise NotImplementedError

    def q(self, ident):
        return '"' + str(ident).replace('"', '""') + '"'

    def sql_limite(self, sql, n):
        raise NotImplementedError

    def expr_data(self, params, valor, fim):
        """Expressão SQL para uma data AAAA-MM-DD já validada (fim=True: dia seguinte, comparação com '<')."""
        raise NotImplementedError

    def dicas_erro(self):
        return []

    def _cursor_continuo(self):
        return self.con.cursor()

    def _executar(self, cur, sql, params):
        cur.execute(sql, params or ())

    def abandonar(self, cur=None):
        """Fecha a conexão sem ler o restante de um resultado grande."""
        self.fechar()

    # ---------------------------------------------------- ciclo de vida
    def conectar(self):
        try:
            self.con = self._abrir()
        except ErroBanco:
            raise
        except ImportError as e:
            raise ErroBanco(f"Driver do {self.nome} não instalado. Rode o iniciar.bat de novo.", str(e), "conexao")
        except Exception as e:  # noqa
            raise self.erro(e, conexao=True)
        return self

    def fechar(self):
        if self.con is not None:
            try:
                self.con.rollback()
            except Exception:  # noqa
                pass
            try:
                self.con.close()
            except Exception:  # noqa
                pass
        self.con = None

    def __enter__(self):
        return self.conectar()

    def __exit__(self, *a):
        self.fechar()

    # ---------------------------------------------------- execução
    def exec(self, sql, params=(), continuo=False):
        cur = self._cursor_continuo() if continuo else self.con.cursor()
        try:
            self._executar(cur, sql, params)
        except Exception as e:  # noqa
            try:
                cur.close()
            except Exception:  # noqa
                pass
            raise self.erro(e)
        return cur

    def todas(self, sql, params=()):
        cur = self.exec(sql, params)
        try:
            return [tuple(_txt(x) for x in l) for l in cur.fetchall()]
        finally:
            cur.close()

    def consultar(self, sql, params=(), offset=0, limite=500, validar=True):
        """Executa um SELECT e devolve uma página do resultado. Não lê o restante do resultado."""
        if validar:
            sql = self.validar(sql)
        t0 = time.perf_counter()
        cur = self.exec(sql, params, continuo=True)
        try:
            colunas = [d[0] for d in cur.description]
            tipos = [self.nome_tipo_cursor(d) for d in cur.description]
            pulou = 0
            while pulou < offset:
                lote = cur.fetchmany(min(LOTE_EXPORTACAO, offset - pulou))
                if not lote:
                    break
                pulou += len(lote)
            linhas = cur.fetchmany(limite + 1)
        except ErroBanco:
            raise
        except Exception as e:  # noqa
            raise self.erro(e)
        mais = len(linhas) > limite
        linhas = linhas[:limite]
        if mais:
            self.abandonar(cur)  # não baixa o resto de um resultado enorme só para fechar o cursor
        else:
            try:
                cur.close()
            except Exception:  # noqa
                pass
        ms = round((time.perf_counter() - t0) * 1000)
        return {"colunas": colunas, "tipos": tipos, "linhas": [[para_tela(v) for v in l] for l in linhas],
                "offset": offset, "mais": mais, "tempo_ms": ms}

    def nome_tipo_cursor(self, d):
        t = d[1]
        return getattr(t, "name", None) or (t if isinstance(t, str) else "")

    def contar(self, sql, params=()):
        sql = self.validar(sql)
        alias = "" if self.tipo == "oracle" else " _wb"
        cur = self.exec(f"SELECT COUNT(*) FROM ({sql}){alias}", params)
        try:
            return int(cur.fetchone()[0])
        finally:
            cur.close()

    def validar(self, sql):
        try:
            return validar_select(sql, self.dialeto)
        except ComandoBloqueado as e:
            raise ErroBanco(str(e), "", "bloqueado")

    # ---------------------------------------------------- SQL gerado pela aplicação
    def origem(self, container, objeto):
        return (self.q(container) + "." if container else "") + self.q(objeto)

    def montar_select(self, container, objeto, colunas=None, filtro_data=None, where="", ordem=None, limite=0):
        """SELECT de uma tabela/view a partir das escolhas da tela. Nomes vão entre aspas do banco e
        valores digitados (datas) vão como parâmetro ou já validados. Retorna (sql, params)."""
        cols = ", ".join(self.q(c) for c in colunas) if colunas else "*"
        conds, params = [], []
        fd = filtro_data or {}
        if fd.get("coluna") and (fd.get("de") or fd.get("ate")):
            col = self.q(fd["coluna"])
            de, ate = fd.get("de"), fd.get("ate")
            try:
                de = dt.date.fromisoformat(de).isoformat() if de else None
                ate = dt.date.fromisoformat(ate).isoformat() if ate else None
            except ValueError:
                raise ErroBanco("Data inválida no período. Use o seletor de data.", "", "validacao")
            if de and ate and de > ate:
                raise ErroBanco("A data inicial é maior que a data final.", "", "validacao")
            if de:
                conds.append(f"{col} >= {self.expr_data(params, de, False)}")
            if ate:
                conds.append(f"{col} < {self.expr_data(params, ate, True)}")
        if where and where.strip():
            try:
                conds.append("(" + validar_condicao(where, self.dialeto) + ")")
            except ComandoBloqueado as e:
                raise ErroBanco(str(e), "", "bloqueado")
        sql = f"SELECT {cols} FROM {self.origem(container, objeto)}"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if ordem and ordem.get("coluna"):
            sql += f" ORDER BY {self.q(ordem['coluna'])} {'DESC' if ordem.get('desc') else 'ASC'}"
        if limite:
            sql = self.sql_limite(sql, int(limite))
        return sql, params

    def previa(self, container, objeto, colunas_meta, limite=100, where="", ordem_usuario=None):
        col_ins = coluna_insercao(colunas_meta)
        if ordem_usuario and ordem_usuario.get("coluna"):
            ordem, info = ordem_usuario, {"tipo": "usuario", "coluna": ordem_usuario["coluna"],
                                          "desc": bool(ordem_usuario.get("desc"))}
        elif col_ins:
            ordem, info = {"coluna": col_ins, "desc": True}, {"tipo": "recentes", "coluna": col_ins}
        else:
            ordem, info = None, {"tipo": "sem_ordem"}
        sql, params = self.montar_select(container, objeto, where=where, ordem=ordem, limite=limite)
        r = self.consultar(sql, params, 0, limite, validar=False)
        r["ordem"] = info
        r["sql"] = sql
        return r

    # ---------------------------------------------------- exportação (streaming)
    def exportar(self, sql, params, destino, formato, progresso=None):
        from ..exportacao import gravar
        sql = self.validar(sql)
        cur = self.exec(sql, params, continuo=True)
        try:
            return gravar(cur, destino, formato, progresso)
        except (Cancelado, ErroBanco, OSError):
            self.abandonar(cur)
            raise
        except Exception as e:  # noqa
            self.abandonar(cur)
            raise self.erro(e)

    # ---------------------------------------------------- mensagens
    DICAS_COMUNS = [
        ("timed out", "Tempo esgotado. A consulta ou a conexão demorou além do limite configurado.", "timeout"),
        ("timeout", "Tempo esgotado. A consulta ou a conexão demorou além do limite configurado.", "timeout"),
        ("Name or service not known", "Host não encontrado. Confira o nome do servidor e se está na rede/VPN.", "conexao"),
        ("getaddrinfo failed", "Host não encontrado. Confira o nome do servidor e se está na rede/VPN.", "conexao"),
        ("Connection refused", "O servidor recusou a conexão. Confira a porta e se o serviço está no ar.", "conexao"),
    ]

    def erro(self, e, conexao=False):
        if isinstance(e, ErroBanco):
            return e
        partes = []
        for a in getattr(e, "args", ()) or ():
            if isinstance(a, bytes):
                a = a.decode("utf-8", "replace")
            if isinstance(a, (str, int)) and str(a).strip():
                partes.append(" ".join(str(a).split()))
        bruto = " ".join(partes) or str(e).strip() or e.__class__.__name__
        bruto = " ".join(re.sub(r"Help: https?://\S+", "", bruto).split())  # remove o link de ajuda do Oracle
        detalhe = mascarar(f"{e.__class__.__name__}: {bruto}", [self.cfg.get("senha")])[:600]
        for chave, dica, cat in list(self.dicas_erro()) + self.DICAS_COMUNS:
            if chave.lower() in bruto.lower():
                return ErroBanco(dica, detalhe, "conexao" if conexao and cat == "consulta" else cat)
        return ErroBanco(("Não foi possível conectar: " if conexao else "O banco retornou um erro: ")
                         + mascarar(bruto, [self.cfg.get("senha")])[:300], detalhe,
                         "conexao" if conexao else "consulta")


def _txt(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return v
