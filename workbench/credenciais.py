"""Cofre local das senhas das conexões salvas.

- Windows: usa o DPAPI do próprio Windows (CryptProtectData, escopo do usuário logado). A senha cifrada só
  pode ser aberta pelo mesmo usuário, no mesmo computador. É o mesmo mecanismo que o Windows usa para
  guardar senhas de rede e do navegador, e não depende de nenhuma biblioteca extra.
- Outros sistemas (desenvolvimento/testes): Fernet (biblioteca `cryptography`, já usada no Anonimizador)
  com uma chave aleatória gravada em config/.chave-local, legível só pelo dono do arquivo.

A senha em texto puro nunca é gravada em disco e nunca é enviada ao navegador.
"""
import base64
import os
import platform

PREFIXO_DPAPI = "dpapi:"
PREFIXO_FERNET = "fernet:"
_ENTROPIA = b"SQL-Workbench-v1"


# ----------------------------------------------------------------- DPAPI (Windows)

def _dpapi(dados: bytes, cifrar: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    entrada, _b1 = blob(dados)
    entropia, _b2 = blob(_ENTROPIA)
    saida = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if cifrar:
        ok = crypt32.CryptProtectData(ctypes.byref(entrada), "SQL Workbench", ctypes.byref(entropia), None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(saida))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(saida))
    if not ok:
        raise OSError("O Windows não conseguiu " + ("proteger" if cifrar else "abrir") + " a senha salva.")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


# ----------------------------------------------------------------- Fernet (fallback)

def _fernet(pasta_config):
    from cryptography.fernet import Fernet
    caminho = os.path.join(pasta_config, ".chave-local")
    if not os.path.exists(caminho):
        os.makedirs(pasta_config, exist_ok=True)
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    with open(caminho, "rb") as f:
        return Fernet(f.read().strip())


class Cofre:
    def __init__(self, pasta_config):
        self.pasta = pasta_config
        self.windows = platform.system() == "Windows"

    @property
    def mecanismo(self):
        return "DPAPI do Windows" if self.windows else "Fernet com chave local"

    def cifrar(self, senha: str) -> str:
        if not senha:
            return ""
        dados = senha.encode("utf-8")
        if self.windows:
            return PREFIXO_DPAPI + base64.b64encode(_dpapi(dados, True)).decode("ascii")
        return PREFIXO_FERNET + _fernet(self.pasta).encrypt(dados).decode("ascii")

    def abrir(self, cifrada: str) -> str:
        if not cifrada:
            return ""
        if cifrada.startswith(PREFIXO_DPAPI):
            if not self.windows:
                raise OSError("Esta senha foi salva no Windows e só pode ser aberta lá. Informe a senha de novo.")
            return _dpapi(base64.b64decode(cifrada[len(PREFIXO_DPAPI):]), False).decode("utf-8")
        if cifrada.startswith(PREFIXO_FERNET):
            from cryptography.fernet import InvalidToken
            try:
                return _fernet(self.pasta).decrypt(cifrada[len(PREFIXO_FERNET):].encode("ascii")).decode("utf-8")
            except InvalidToken:
                raise OSError("Não foi possível abrir a senha salva (a chave local mudou). Informe a senha de novo.")
        raise OSError("Formato de senha salva desconhecido. Informe a senha de novo.")
