"""Diagnóstico por etapas (driver → endereço → porta → login), o mesmo do Anonimizador, sem SQL Server."""
import socket

from .drivers import ErroBanco, criar
from .drivers import oracle as ora


def disponibilidade():
    out = {"oracle": False, "mysql": False, "detalhes": {}, "oracle_client": []}
    try:
        import oracledb
        out["oracle"] = True
        cand = ora.candidatos_client()
        out["oracle_client"] = cand
        out["detalhes"]["oracle"] = (f"oracledb {oracledb.__version__} — Oracle Client: "
                                     + (cand[0] if cand else "não encontrado (usará o modo leve)"))
    except Exception as e:  # noqa
        out["detalhes"]["oracle"] = f"Pacote 'oracledb' não instalado ({e.__class__.__name__}). Rode o iniciar.bat de novo."
    try:
        import pymysql
        out["mysql"] = True
        out["detalhes"]["mysql"] = f"PyMySQL {pymysql.__version__}"
    except Exception as e:  # noqa
        out["detalhes"]["mysql"] = f"Pacote 'pymysql' não instalado ({e.__class__.__name__}). Rode o iniciar.bat de novo."
    return out


def diagnosticar(cfg):
    tipo = cfg.get("tipo")
    etapas = []

    def add(nome, ok, detalhe):
        etapas.append({"etapa": nome, "ok": ok, "detalhe": detalhe})
        return ok

    # 1. driver
    try:
        if tipo == "oracle":
            import oracledb
            est = ora.iniciar_client(cfg.get("instant_client"))
            if est["modo"] == "thick":
                det = f"oracledb {oracledb.__version__} usando o Oracle Client em {est['lib']}"
            else:
                det = (f"oracledb {oracledb.__version__} em modo leve: nenhum Oracle Client encontrado. "
                       "Se o seu script usa init_oracle_client, informe a pasta 'bin' do client nas opções avançadas.")
                if est["tentativas"]:
                    det += " Tentativas: " + " | ".join(est["tentativas"][-3:])
            add("Driver", True, det)
        else:
            import pymysql
            add("Driver", True, f"PyMySQL {pymysql.__version__}")
    except ImportError as e:
        add("Driver", False, f"Pacote não instalado ({e}). Rode o iniciar.bat de novo.")
        return etapas

    # 2 e 3. endereço e porta
    if tipo == "oracle":
        host, porta = ora.host_porta(cfg)
    else:
        host, porta = (cfg.get("host") or "").strip() or None, int(cfg.get("porta") or 3306)
    if tipo == "oracle" and not host:
        add("Endereço", True, f"'{ora.dsn(cfg)}' é um nome TNS: o Oracle Client resolve pelo tnsnames.ora.")
    elif host:
        try:
            ip = socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)[0][4][0]
            add("Endereço", True, f"{host} → {ip}")
        except socket.gaierror:
            add("Endereço", False, f"O nome '{host}' não foi encontrado. Confira o host e se está na rede/VPN da empresa.")
            return etapas
        try:
            with socket.create_connection((host, porta), timeout=6):
                pass
            add("Porta", True, f"Porta {porta} aberta")
        except OSError as e:
            add("Porta", False, f"Não foi possível abrir a porta {porta} em {host} ({e.__class__.__name__}). "
                                "Confira a porta, a VPN ou se há firewall entre sua máquina e o servidor.")
            return etapas
    else:
        add("Endereço", False, "Informe o host.")
        return etapas

    # 4. login
    try:
        with criar(cfg, 30) as d:
            add("Login", True, f"{d.versao()} — {d.descricao_driver}")
    except ErroBanco as e:
        add("Login", False, e.msg)
    return etapas
