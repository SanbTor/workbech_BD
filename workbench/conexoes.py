"""Conexões salvas: config/conexoes.json (sem senha em texto puro; a senha vai cifrada pelo Cofre)."""
import json
import os
import threading
import uuid
from datetime import datetime

from .credenciais import Cofre
from .drivers import TIPOS, porta_padrao

# Campos que podem ser gravados e mostrados na tela (a senha nunca está aqui)
CAMPOS = ("nome", "tipo", "host", "porta", "database", "servico", "modo_servico", "dsn", "usuario", "ssl",
          "instant_client", "salvar_senha")


class ErroConexao(Exception):
    pass


class RepositorioConexoes:
    def __init__(self, pasta_config):
        self.arquivo = os.path.join(pasta_config, "conexoes.json")
        self.cofre = Cofre(pasta_config)
        self._trava = threading.Lock()

    # ---------------------------------------------------------- arquivo
    def _ler(self):
        try:
            with open(self.arquivo, encoding="utf-8") as f:
                return json.load(f).get("conexoes", [])
        except (OSError, ValueError):
            return []

    def _gravar(self, itens):
        os.makedirs(os.path.dirname(self.arquivo), exist_ok=True)
        tmp = self.arquivo + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"versao": 1, "conexoes": itens}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.arquivo)

    # ---------------------------------------------------------- públicos (sem senha)
    @staticmethod
    def publico(c):
        out = {k: c.get(k) for k in CAMPOS if k in c}
        out.update(id=c["id"], tem_senha=bool(c.get("senha_cifrada")), criado=c.get("criado"),
                   atualizado=c.get("atualizado"), ultimo_uso=c.get("ultimo_uso"))
        return out

    def listar(self):
        itens = self._ler()
        itens.sort(key=lambda c: (c.get("ultimo_uso") or "", c.get("atualizado") or ""), reverse=True)
        return [self.publico(c) for c in itens]

    def obter(self, cid):
        for c in self._ler():
            if c["id"] == cid:
                return c
        raise ErroConexao("Conexão salva não encontrada. Ela pode ter sido excluída.")

    # ---------------------------------------------------------- validação
    @staticmethod
    def normalizar(cfg):
        tipo = cfg.get("tipo")
        if tipo not in TIPOS:
            raise ErroConexao("Escolha MySQL ou Oracle.")
        limpo = {}
        for k in CAMPOS:
            v = cfg.get(k)
            if isinstance(v, str):
                v = v.strip()
            if v not in (None, ""):
                limpo[k] = v
        limpo["tipo"] = tipo
        limpo["salvar_senha"] = bool(cfg.get("salvar_senha", True))
        if not limpo.get("nome"):
            raise ErroConexao("Informe o nome da conexão.")
        if len(limpo["nome"]) > 80:
            raise ErroConexao("O nome da conexão pode ter até 80 caracteres.")
        if tipo == "oracle" and limpo.get("dsn"):
            pass  # string de conexão completa substitui host/porta/serviço
        else:
            if not limpo.get("host"):
                raise ErroConexao("Informe o host ou IP do servidor.")
            if tipo == "oracle" and not limpo.get("servico"):
                raise ErroConexao("Informe o Service Name ou o SID.")
        porta = str(limpo.get("porta") or porta_padrao(tipo))
        if not porta.isdigit() or not (0 < int(porta) < 65536):
            raise ErroConexao("Porta inválida.")
        limpo["porta"] = int(porta)
        if not limpo.get("usuario"):
            raise ErroConexao("Informe o usuário.")
        if tipo != "oracle":
            for k in ("servico", "modo_servico", "dsn", "instant_client"):
                limpo.pop(k, None)
        else:
            limpo.pop("database", None)
            limpo.pop("ssl", None)
            limpo["modo_servico"] = "sid" if cfg.get("modo_servico") == "sid" else "service"
        return limpo

    # ---------------------------------------------------------- escrita
    def salvar(self, cfg, senha=None, cid=None):
        """Cria (cid=None) ou atualiza. senha=None mantém a senha atual; '' apaga."""
        limpo = self.normalizar(cfg)
        agora = datetime.now().isoformat(timespec="seconds")
        with self._trava:
            itens = self._ler()
            if any(c["nome"].lower() == limpo["nome"].lower() and c["id"] != cid for c in itens):
                raise ErroConexao("Já existe uma conexão com esse nome.")
            atual = next((c for c in itens if c["id"] == cid), None) if cid else None
            if cid and not atual:
                raise ErroConexao("Conexão salva não encontrada.")
            reg = {"id": cid or uuid.uuid4().hex[:12], "criado": (atual or {}).get("criado", agora),
                   "ultimo_uso": (atual or {}).get("ultimo_uso")}
            reg.update(limpo)
            reg["atualizado"] = agora
            if not limpo["salvar_senha"]:
                reg["senha_cifrada"] = ""
            elif senha is not None and senha != "":
                try:
                    reg["senha_cifrada"] = self.cofre.cifrar(senha)
                except OSError as e:
                    raise ErroConexao(f"Não foi possível proteger a senha neste computador: {e}")
            else:
                reg["senha_cifrada"] = (atual or {}).get("senha_cifrada", "")
            itens = [c for c in itens if c["id"] != reg["id"]] + [reg]
            self._gravar(itens)
        return self.publico(reg)

    def excluir(self, cid):
        with self._trava:
            itens = self._ler()
            novos = [c for c in itens if c["id"] != cid]
            if len(novos) == len(itens):
                raise ErroConexao("Conexão salva não encontrada.")
            self._gravar(novos)

    def marcar_uso(self, cid):
        with self._trava:
            itens = self._ler()
            for c in itens:
                if c["id"] == cid:
                    c["ultimo_uso"] = datetime.now().isoformat(timespec="seconds")
            self._gravar(itens)

    def cfg_completa(self, cid, senha_informada=None):
        """Configuração com a senha aberta (só em memória, no backend)."""
        c = self.obter(cid)
        cfg = {k: c.get(k) for k in CAMPOS if k in c}
        if senha_informada:
            cfg["senha"] = senha_informada
        else:
            try:
                cfg["senha"] = self.cofre.abrir(c.get("senha_cifrada", ""))
            except OSError as e:
                raise ErroConexao(str(e))
        return cfg
