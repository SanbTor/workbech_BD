"""Registro dos bancos suportados. Para um novo banco, crie a subclasse de Driver e inclua aqui."""
from .base import Cancelado, Driver, ErroBanco  # noqa: F401
from .mysql import MySQL
from .oracle import Oracle

DRIVERS = {d.tipo: d for d in (MySQL, Oracle)}
TIPOS = tuple(DRIVERS)


def porta_padrao(tipo):
    return DRIVERS[tipo].porta_padrao


def criar(cfg, timeout=60):
    cls = DRIVERS.get(cfg.get("tipo"))
    if not cls:
        raise ErroBanco("Tipo de banco não suportado. Use MySQL ou Oracle.", "", "validacao")
    return cls(cfg, timeout)


def descricao():
    return [{"tipo": d.tipo, "nome": d.nome, "porta": d.porta_padrao, "container": d.rotulo_container}
            for d in DRIVERS.values()]
