"""Modo somente leitura e mascaramento de informações sensíveis.

A validação do SQL acontece sempre no backend (o frontend só repete a checagem para avisar mais cedo).
Além dela, cada conexão é aberta com a transação/sessão em modo READ ONLY quando o banco permite.
"""
import re


class ComandoBloqueado(Exception):
    """O SQL enviado não é uma consulta de leitura."""


# Palavras que só aparecem em comandos que alteram dados, estrutura, permissões ou configuração.
# Funções comuns de leitura (REPLACE(), DBMS_LOB.SUBSTR, etc.) não entram na lista.
PROIBIDAS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|RENAME|"
    r"EXEC|EXECUTE|CALL|BEGIN|DECLARE|COMMIT|ROLLBACK|SAVEPOINT|LOCK|UNLOCK|SHUTDOWN|KILL|"
    r"HANDLER|INTO|OUTFILE|DUMPFILE|LOAD_FILE|PURGE|FLASHBACK|ANALYZE|OPTIMIZE|REPAIR|"
    r"FUNCTION|PROCEDURE|TRIGGER|"
    r"DBMS_SQL|DBMS_SCHEDULER|DBMS_JOB|DBMS_PIPE|DBMS_LOCK|DBMS_SESSION|DBMS_SYSTEM|"
    r"UTL_FILE|UTL_HTTP|UTL_TCP|UTL_SMTP|UTL_MAIL|UTL_INADDR)\b",
    re.I,
)
INICIO_OK = re.compile(r"^\(*\s*(SELECT|WITH)\b", re.I)


def _limpar(sql: str, mysql: bool = True) -> str:
    """Troca comentários por espaço e esvazia textos/identificadores entre aspas, numa única leitura
    da esquerda para a direita (assim um '#' ou '--' dentro de um texto não engana a validação)."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i) or (c == "#" and mysql):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif c == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c in "'\"`":
            j = i + 1
            while j < n:
                if mysql and sql[j] == "\\" and c == "'":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # aspas duplicadas = escape
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise ComandoBloqueado("Há um texto ou identificador entre aspas que não foi fechado.")
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def validar_select(sql: str, dialeto: str = "mysql") -> str:
    """Aceita só uma consulta de leitura (SELECT ou WITH ... SELECT). Retorna o SQL sem o ';' final."""
    sql = (sql or "").strip()
    if not sql:
        raise ComandoBloqueado("Escreva uma consulta SELECT.")
    corpo = _limpar(sql, dialeto == "mysql").strip().rstrip(";").strip()
    if not corpo:
        raise ComandoBloqueado("Escreva uma consulta SELECT.")
    if ";" in corpo:
        raise ComandoBloqueado("Execute uma consulta por vez (há mais de um comando separado por ';').")
    if not INICIO_OK.match(corpo):
        primeira = (re.findall(r"[A-Za-z_]+", corpo) or ["?"])[0].upper()
        raise ComandoBloqueado(f"Comando '{primeira}' bloqueado: o Workbench é somente leitura e aceita apenas "
                               "consultas SELECT ou WITH.")
    m = PROIBIDAS.search(corpo)
    if m:
        raise ComandoBloqueado(f"Comando bloqueado: a consulta contém '{m.group(1).upper()}'. O Workbench é "
                               "somente leitura (SELECT/WITH) para evitar alterações acidentais no banco.")
    # devolve o SQL original (com comentários e textos intactos), só sem o ';' do fim
    return re.sub(r"[;\s]+$", "", sql)


def validar_condicao(where: str, dialeto: str = "mysql") -> str:
    """Valida um trecho de WHERE digitado na tela (mesmas regras de leitura)."""
    where = (where or "").strip()
    if not where:
        return ""
    validar_select("SELECT 1 FROM x WHERE " + where, dialeto)
    return where


# ----------------------------------------------------------------- mascaramento

_PADROES = [
    (re.compile(r"(?i)(password|passwd|pwd|senha)\s*[=:]\s*[^;,\s)]+"), r"\1=****"),
    (re.compile(r"(?i)\b([\w.$#-]+)/([^@\s/]+)@"), r"\1/****@"),       # usuario/senha@dsn (Oracle)
    (re.compile(r"(?i)(\w+://[^:/\s]+):([^@\s]+)@"), r"\1:****@"),       # url://usuario:senha@host
]


def mascarar(texto, segredos=()) -> str:
    """Esconde senhas conhecidas e padrões comuns de credenciais em mensagens e logs."""
    s = str(texto or "")
    for seg in segredos:
        if seg and len(str(seg)) >= 2:
            s = s.replace(str(seg), "****")
    for rx, rep in _PADROES:
        s = rx.sub(rep, s)
    return s
