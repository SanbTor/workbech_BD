"""SQL Database Workbench — servidor local (evolução do Anonimizador). Execute com:  python app.py"""
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from workbench import drivers
from workbench.conexoes import ErroConexao, RepositorioConexoes
from workbench.diagnostico import diagnosticar, disponibilidade
from workbench.drivers import Cancelado, ErroBanco
from workbench.drivers.base import para_tela
from workbench.exportacao import FORMATOS
from workbench.seguranca import mascarar

VERSAO = "1.0"
BASE = os.path.dirname(os.path.abspath(__file__))
PASTA_CONFIG = os.path.join(BASE, "config")
PASTA_SAIDA = os.path.join(BASE, "saida")

PADRAO_CONFIG = {
    "timeout_consulta_s": 120,     # Editor SQL e prévia
    "timeout_metadados_s": 60,     # navegação (databases, schemas, tabelas, colunas)
    "timeout_exportacao_s": 0,     # 0 = sem limite (exportações grandes rodam no backend)
    "linhas_previa": 100,
    "linhas_pagina_sql": 500,
    "max_exportacoes_simultaneas": 2,
}


def ler_config():
    cfg = dict(PADRAO_CONFIG)
    try:
        with open(os.path.join(PASTA_CONFIG, "workbench.json"), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in PADRAO_CONFIG})
    except (OSError, ValueError):
        pass
    return cfg


CONFIG = ler_config()
REPO = RepositorioConexoes(PASTA_CONFIG)
app = FastAPI(title="SQL Database Workbench", docs_url=None, redoc_url=None, openapi_url=None)
ESTADO = {"sessao": None, "jobs": {}, "porta": 8765}
VAGAS_EXPORTACAO = threading.Semaphore(int(CONFIG["max_exportacoes_simultaneas"]))


def log(msg):
    """Log no console sempre mascarado (nunca senha, nunca string de conexão com credencial)."""
    s = ESTADO["sessao"]
    print(time.strftime("%H:%M:%S"), mascarar(msg, [s["cfg"].get("senha")] if s else []), flush=True)


# ------------------------------------------------------------------ erros e proteção local

class ErroApp(Exception):
    def __init__(self, msg, status=400, categoria="validacao", detalhe=""):
        super().__init__(msg)
        self.msg, self.status, self.categoria, self.detalhe = msg, status, categoria, detalhe


@app.exception_handler(ErroBanco)
def _erro_banco(_req, e: ErroBanco):
    return JSONResponse({"detail": e.msg, "categoria": e.categoria, "detalhe": e.detalhe}, status_code=400)


@app.exception_handler(ErroApp)
def _erro_app(_req, e: ErroApp):
    return JSONResponse({"detail": e.msg, "categoria": e.categoria, "detalhe": e.detalhe}, status_code=e.status)


@app.exception_handler(ErroConexao)
def _erro_conexao(_req, e: ErroConexao):
    return JSONResponse({"detail": str(e), "categoria": "validacao"}, status_code=400)


@app.exception_handler(Exception)
def _erro_geral(_req, e: Exception):
    log(f"Erro inesperado: {e.__class__.__name__}: {e}")
    return JSONResponse({"detail": "Erro inesperado no Workbench. Veja os detalhes técnicos.", "categoria": "interno",
                         "detalhe": mascarar(f"{e.__class__.__name__}: {e}")[:400]}, status_code=500)


HOSTS_OK = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")


@app.middleware("http")
async def so_local(request: Request, call_next):
    """Só atende o próprio computador: bloqueia páginas de outros sites e DNS rebinding."""
    host = request.headers.get("host", "")
    origem = request.headers.get("origin")
    if not HOSTS_OK.match(host) or (origem and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origem)):
        return JSONResponse({"detail": "Acesso permitido apenas a partir deste computador."}, status_code=403)
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ------------------------------------------------------------------ sessão ativa

def sessao():
    s = ESTADO["sessao"]
    if not s:
        raise ErroApp("Nenhuma conexão ativa. Conecte-se a um banco primeiro.", 409, "sem_sessao")
    return s


class Conectado:
    """Abre uma conexão para a operação (como no Anonimizador) e fecha ao terminar."""

    def __init__(self, timeout_chave="timeout_consulta_s", container=None):
        self.timeout = CONFIG[timeout_chave]
        self.container = container
        self.d = None

    def __enter__(self):
        s = sessao()
        self.d = drivers.criar(s["cfg"], self.timeout).conectar()
        try:
            if self.container:
                self.d.usar_container(self.container)
        except ErroBanco as e:
            self.d.fechar()
            raise ErroBanco(f"Não foi possível usar o {self.d.rotulo_container.lower()} '{self.container}': {e.msg}",
                            e.detalhe, "permissao")
        return self.d

    def __exit__(self, *a):
        if self.d:
            self.d.fechar()


def resumo_sessao(s):
    if not s:
        return None
    c = s["cfg"]
    alvo = (c.get("dsn") or f"{c.get('host')}:{c.get('porta')}" +
            (f"/{c.get('servico')}" if c.get("tipo") == "oracle" else ""))
    return {"id": s["id"], "nome": c.get("nome"), "tipo": c["tipo"], "banco": drivers.DRIVERS[c["tipo"]].nome,
            "rotulo_container": drivers.DRIVERS[c["tipo"]].rotulo_container, "alvo": mascarar(alvo),
            "usuario": c.get("usuario"), "versao": s["versao"], "driver": s["driver"],
            "container": s["container"], "container_inicial": s["container_inicial"], "desde": s["desde"]}


# ------------------------------------------------------------------ página e estado

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE, "web", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/estado")
def estado():
    return {"versao": VERSAO, "bancos": drivers.descricao(), "drivers": disponibilidade(),
            "conexoes": REPO.listar(), "sessao": resumo_sessao(ESTADO["sessao"]), "cofre": REPO.cofre.mecanismo,
            "formatos": [{"id": k, "nome": v[0]} for k, v in FORMATOS.items()], "config": CONFIG,
            "pasta_saida": PASTA_SAIDA}


# ------------------------------------------------------------------ conexões salvas

class ConexaoReq(BaseModel):
    cfg: dict = {}
    senha: str | None = None   # None = manter a salva
    id: str | None = None


def cfg_para_teste(req: ConexaoReq):
    """Monta a configuração com senha só no backend: a digitada agora ou, se vazia, a salva."""
    cfg = REPO.normalizar(req.cfg) if req.cfg else REPO.cfg_completa(req.id)
    if req.senha:
        cfg["senha"] = req.senha
    elif req.id:
        try:
            cfg["senha"] = REPO.cfg_completa(req.id)["senha"]
        except ErroConexao:
            cfg["senha"] = ""
    else:
        cfg["senha"] = ""
    return cfg


@app.post("/api/conexoes")
def criar_conexao(req: ConexaoReq):
    return {"conexao": REPO.salvar(req.cfg, req.senha), "conexoes": REPO.listar()}


@app.put("/api/conexoes/{cid}")
def editar_conexao(cid: str, req: ConexaoReq):
    con = REPO.salvar(req.cfg, req.senha, cid)
    s = ESTADO["sessao"]
    if s and s["id"] == cid:
        log("Conexão ativa editada: a sessão foi encerrada para usar os novos dados.")
        ESTADO["sessao"] = None
    return {"conexao": con, "conexoes": REPO.listar()}


@app.delete("/api/conexoes/{cid}")
def excluir_conexao(cid: str):
    REPO.excluir(cid)
    if ESTADO["sessao"] and ESTADO["sessao"]["id"] == cid:
        ESTADO["sessao"] = None
    return {"conexoes": REPO.listar()}


@app.post("/api/conexoes/testar")
def testar_conexao(req: ConexaoReq):
    cfg = cfg_para_teste(req)
    t0 = time.perf_counter()
    with drivers.criar(cfg, 30) as d:
        versao = d.versao()
        atual = d.container_atual()
        driver = d.descricao_driver
    return {"ok": True, "versao": versao, "driver": driver, "container": atual,
            "ms": round((time.perf_counter() - t0) * 1000)}


@app.post("/api/conexoes/diagnosticar")
def diagnosticar_conexao(req: ConexaoReq):
    return {"etapas": diagnosticar(cfg_para_teste(req))}


# ------------------------------------------------------------------ sessão

class ConectarReq(BaseModel):
    id: str
    senha: str | None = None


@app.post("/api/sessao/conectar")
def conectar(req: ConectarReq):
    cfg = REPO.cfg_completa(req.id, req.senha)
    if not cfg.get("senha"):
        raise ErroApp("Informe a senha para conectar (ela não foi salva nesta conexão).", 400, "pedir_senha")
    with drivers.criar(cfg, 30) as d:
        versao, driver = d.versao(), d.descricao_driver
        atual = d.container_atual()
    inicial = cfg.get("database") if cfg["tipo"] == "mysql" and cfg.get("database") else atual
    ESTADO["sessao"] = {"id": req.id, "cfg": cfg, "versao": versao, "driver": driver, "container": inicial,
                        "container_inicial": inicial, "desde": datetime.now().isoformat(timespec="seconds")}
    REPO.marcar_uso(req.id)
    log(f"Conectado: {cfg.get('nome')} ({cfg['tipo']})")
    return {"sessao": resumo_sessao(ESTADO["sessao"])}


@app.post("/api/sessao/desconectar")
def desconectar():
    ESTADO["sessao"] = None
    return {"ok": True}


@app.post("/api/sessao/ping")
def ping():
    t0 = time.perf_counter()
    with Conectado("timeout_metadados_s") as d:
        d.versao()
    return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000)}


class ContainerReq(BaseModel):
    container: str = ""


@app.post("/api/sessao/container")
def trocar_container(req: ContainerReq):
    s = sessao()
    s["container"] = req.container or s["container_inicial"]
    return {"sessao": resumo_sessao(s)}


# ------------------------------------------------------------------ metadados (sob demanda)

@app.get("/api/meta/containers")
def meta_containers():
    t0 = time.perf_counter()
    with Conectado("timeout_metadados_s") as d:
        itens = d.containers()
    return {"itens": itens, "ms": round((time.perf_counter() - t0) * 1000)}


@app.get("/api/meta/objetos")
def meta_objetos(container: str):
    with Conectado("timeout_metadados_s") as d:
        itens = d.objetos(container)
    for i in itens:
        i["linhas_estimadas"] = para_tela(i.get("linhas_estimadas"))
    return {"container": container, "itens": itens}


@app.get("/api/meta/buscar")
def meta_buscar(termo: str, container: str = ""):
    termo = termo.strip()
    if len(termo) < 2:
        return {"itens": []}
    with Conectado("timeout_metadados_s") as d:
        return {"itens": d.buscar(termo, container or None)}


@app.get("/api/meta/detalhes")
def meta_detalhes(container: str, objeto: str):
    with Conectado("timeout_metadados_s") as d:
        r = d.detalhes(container, objeto)
    r["objeto"]["linhas_estimadas"] = para_tela(r["objeto"].get("linhas_estimadas"))
    r["objeto"]["extras"] = {k: para_tela(v) for k, v in r["objeto"]["extras"].items() if v not in (None, "")}
    for c in r["colunas"]:
        for k in ("tamanho", "precisao", "escala", "padrao"):
            c[k] = para_tela(c.get(k))
    from workbench.drivers.base import coluna_insercao
    r["coluna_insercao"] = coluna_insercao(r["colunas"])
    return r


# ------------------------------------------------------------------ dados da tabela

class DadosReq(BaseModel):
    container: str
    objeto: str
    limite: int = 100
    where: str = ""
    ordem: dict = {}


@app.post("/api/dados/previa")
def dados_previa(req: DadosReq):
    limite = max(1, min(int(req.limite or CONFIG["linhas_previa"]), 1000))
    with Conectado() as d:
        meta = d.detalhes(req.container, req.objeto)
        r = d.previa(req.container, req.objeto, meta["colunas"], limite, req.where, req.ordem or None)
    return r


@app.post("/api/dados/contar")
def dados_contar(req: DadosReq):
    """COUNT(*) só quando o usuário pede (nunca automático)."""
    t0 = time.perf_counter()
    with Conectado() as d:
        sql, params = d.montar_select(req.container, req.objeto, where=req.where)
        total = d.contar(sql, params)
    return {"total": total, "ms": round((time.perf_counter() - t0) * 1000)}


# ------------------------------------------------------------------ editor SQL

class SqlReq(BaseModel):
    sql: str
    container: str = ""
    offset: int = 0
    limite: int = 0


@app.post("/api/sql/executar")
def sql_executar(req: SqlReq):
    s = sessao()
    validador = drivers.criar(s["cfg"])
    sql = validador.validar(req.sql)  # bloqueia escrita antes mesmo de conectar
    limite = max(1, min(int(req.limite or CONFIG["linhas_pagina_sql"]), 5000))
    with Conectado(container=req.container or s["container"]) as d:
        r = d.consultar(sql, (), max(0, int(req.offset)), limite, validar=False)
    log(f"SQL executado ({r['tempo_ms']} ms, {len(r['linhas'])} linhas)")
    r["container"] = req.container or s["container"]
    return r


@app.post("/api/sql/contar")
def sql_contar(req: SqlReq):
    s = sessao()
    t0 = time.perf_counter()
    with Conectado(container=req.container or s["container"]) as d:
        total = d.contar(req.sql)
    return {"total": total, "ms": round((time.perf_counter() - t0) * 1000)}


# ------------------------------------------------------------------ exportação (job no backend)

class ExportReq(BaseModel):
    origem: str = "tabela"          # tabela | sql
    container: str = ""
    objeto: str = ""
    colunas: list = []
    filtro_data: dict = {}
    where: str = ""
    sql: str = ""
    formato: str = "csv_br"


def nome_arquivo(base, ext):
    base = re.sub(r'[<>:"/\\|?*\s]+', "_", base or "consulta").strip("._") or "consulta"
    return f"{base[:80]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"


@app.post("/api/exportar")
def exportar(req: ExportReq):
    s = sessao()
    if req.formato not in FORMATOS:
        raise ErroApp("Formato de exportação inválido.")
    validador = drivers.criar(s["cfg"])
    if req.origem == "sql":
        sql, params = validador.validar(req.sql), []
        base, container = "consulta", req.container or s["container"]
    else:
        if not req.objeto:
            raise ErroApp("Escolha a tabela a exportar.")
        sql, params = validador.montar_select(req.container, req.objeto, req.colunas or None, req.filtro_data, req.where)
        base, container = req.objeto, None
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    destino = os.path.join(PASTA_SAIDA, nome_arquivo(base, FORMATOS[req.formato][1]))
    cfg = dict(s["cfg"])

    def tarefa(job):
        def progresso(n):
            if job["cancelar"]:
                raise Cancelado()
            job["linhas"] = n
            job["detalhe"] = f"{n:,} linhas gravadas".replace(",", ".")
        d = drivers.criar(cfg, CONFIG["timeout_exportacao_s"]).conectar()
        try:
            if container:
                d.usar_container(container)
            job["detalhe"] = "Consultando o banco…"
            r = d.exportar(sql, params, destino, req.formato, progresso)
        finally:
            d.fechar()
        return {"arquivo": os.path.basename(destino), "pasta": PASTA_SAIDA, "linhas": r["linhas"],
                "truncado": r["truncado"], "bytes": os.path.getsize(destino)}

    jid = iniciar_job(tarefa, {"sql": sql, "params": params, "formato": req.formato, "destino": destino})
    return {"job": jid, "sql": sql, "params": params}


def iniciar_job(tarefa, info):
    if not VAGAS_EXPORTACAO.acquire(blocking=False):
        raise ErroApp("Já existem exportações em andamento. Aguarde uma terminar.", 409, "ocupado")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "status": "rodando", "detalhe": "Conectando…", "linhas": 0, "resultado": None, "erro": None,
           "categoria": None, "detalhe_tecnico": "", "inicio": time.time(), "cancelar": False,
           "destino": info["destino"]}
    ESTADO["jobs"][jid] = job

    def rodar():
        try:
            job["resultado"] = tarefa(job)
            job["status"] = "ok"
        except Cancelado:
            job["status"], job["erro"], job["categoria"] = "cancelado", "Exportação cancelada. Nenhum arquivo foi gerado.", "cancelado"
        except ErroBanco as e:
            job["status"], job["erro"], job["categoria"], job["detalhe_tecnico"] = "erro", "A exportação foi interrompida: " + e.msg, e.categoria, e.detalhe
        except OSError as e:
            job["status"], job["erro"], job["categoria"] = "erro", "Falha ao gravar o arquivo de exportação (disco cheio, pasta sem permissão ou arquivo aberto).", "arquivo"
            job["detalhe_tecnico"] = mascarar(f"{e.__class__.__name__}: {e}")
        except Exception as e:  # noqa
            log(f"Exportação falhou: {e.__class__.__name__}: {e}")
            job["status"], job["erro"], job["categoria"] = "erro", "Falha inesperada ao gerar o arquivo.", "interno"
            job["detalhe_tecnico"] = mascarar(f"{e.__class__.__name__}: {e}", [ESTADO['sessao']['cfg'].get('senha')] if ESTADO['sessao'] else [])
        finally:
            job["duracao"] = round(time.time() - job["inicio"], 1)
            VAGAS_EXPORTACAO.release()
            if job["status"] != "ok":
                try:
                    os.remove(job["destino"])
                except OSError:
                    pass

    threading.Thread(target=rodar, daemon=True).start()
    return jid


@app.get("/api/job/{jid}")
def ver_job(jid: str):
    j = ESTADO["jobs"].get(jid)
    if not j:
        raise ErroApp("Exportação não encontrada.", 404)
    out = {k: v for k, v in j.items() if k not in ("inicio", "destino", "cancelar")}
    out["decorrido"] = round(time.time() - j["inicio"], 1)
    return out


@app.post("/api/job/{jid}/cancelar")
def cancelar_job(jid: str):
    j = ESTADO["jobs"].get(jid)
    if not j:
        raise ErroApp("Exportação não encontrada.", 404)
    j["cancelar"] = True
    return {"ok": True}


@app.get("/api/baixar/{jid}")
def baixar(jid: str):
    j = ESTADO["jobs"].get(jid)
    if not j or j["status"] != "ok":
        raise ErroApp("Arquivo não encontrado.", 404)
    caminho = os.path.join(PASTA_SAIDA, os.path.basename(j["resultado"]["arquivo"]))
    if not os.path.isfile(caminho):
        raise ErroApp("O arquivo não está mais na pasta de saída.", 404)
    return FileResponse(caminho, filename=os.path.basename(caminho))


class PastaReq(BaseModel):
    job: str = ""


@app.post("/api/abrir_pasta")
def abrir_pasta(_req: PastaReq):
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    try:
        if platform.system() == "Windows":
            os.startfile(PASTA_SAIDA)  # noqa
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", PASTA_SAIDA])
        else:
            subprocess.Popen(["xdg-open", PASTA_SAIDA])
    except Exception as e:  # noqa
        raise ErroApp(f"Não foi possível abrir a pasta: {e}", 500)
    return {"ok": True, "pasta": PASTA_SAIDA}


@app.post("/api/sair")
def sair():
    def fim():
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=fim, daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------------ inicialização

def porta_livre(inicio=8765):
    for p in range(inicio, inicio + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return inicio


def main():
    import uvicorn
    for p in (PASTA_CONFIG, PASTA_SAIDA):
        os.makedirs(p, exist_ok=True)
    porta = int(os.environ.get("WORKBENCH_PORTA") or porta_livre())
    url = f"http://127.0.0.1:{porta}"
    print("\n  SQL Database Workbench rodando em", url)
    print("  Modo somente leitura. Feche esta janela (ou use o botão Encerrar) para parar.\n")
    if "--sem-navegador" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
