"""Gravação do resultado em arquivo, em lotes, direto do cursor do banco (nada passa pelo navegador).

Mesma lógica e mesmos formatos da extração do Anonimizador: CSV padrão Brasil (; e vírgula decimal, abre
direto no Excel), CSV internacional (, e ponto) e Excel .xlsx (modo write_only do openpyxl, memória constante).
"""
import csv
import datetime as dt
import decimal
import os

from .drivers.base import LOTE_EXPORTACAO, _num_txt

FORMATOS = {"csv_br": ("CSV (; e vírgula decimal)", ".csv"), "csv": ("CSV (, e ponto decimal)", ".csv"),
            "xlsx": ("Excel (.xlsx)", ".xlsx")}
LIMITE_XLSX = 1_048_575  # linhas de dados numa planilha do Excel


def gravar(cur, destino, formato, progresso=None):
    nomes = [d[0] for d in cur.description]
    n, truncado = 0, False
    try:
        if formato == "xlsx":
            from openpyxl import Workbook
            wb = Workbook(write_only=True)
            ws = wb.create_sheet("Dados")
            ws.append(nomes)
            while True:
                lote = cur.fetchmany(LOTE_EXPORTACAO)
                if not lote:
                    break
                for l in lote:
                    if n >= LIMITE_XLSX:
                        truncado = True
                        break
                    ws.append([_para_xlsx(v) for v in l])
                    n += 1
                if progresso:
                    progresso(n)
                if truncado:
                    break
            wb.save(destino)
        else:
            br = formato == "csv_br"
            with open(destino, "w", newline="", encoding="utf-8-sig" if br else "utf-8") as f:
                w = csv.writer(f, delimiter=";" if br else ",", lineterminator="\r\n" if br else "\n")
                w.writerow(nomes)
                while True:
                    lote = cur.fetchmany(LOTE_EXPORTACAO)
                    if not lote:
                        break
                    w.writerows([_para_csv(v, br) for v in l] for l in lote)
                    n += len(lote)
                    if progresso:
                        progresso(n)
    except BaseException:
        try:
            os.remove(destino)  # não deixa arquivo pela metade
        except OSError:
            pass
        raise
    return {"linhas": n, "colunas": nomes, "truncado": truncado}


def _para_csv(v, br):
    if v is None:
        return ""
    if hasattr(v, "read"):
        v = v.read()
    if isinstance(v, dt.datetime):
        if v.hour == v.minute == v.second == 0 and not v.microsecond:
            return v.strftime("%d/%m/%Y") if br else v.strftime("%Y-%m-%d")
        return v.strftime("%d/%m/%Y %H:%M:%S") if br else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%d/%m/%Y") if br else v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, decimal.Decimal)):
        s = _num_txt(v)
        return s.replace(".", ",") if br else s
    if isinstance(v, (bytes, bytearray, memoryview)):
        return ""
    return str(v)


def _para_xlsx(v):
    if hasattr(v, "read"):
        v = v.read()
    if isinstance(v, decimal.Decimal):
        return float(v) if v != v.to_integral_value() else int(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, dt.datetime) and v.tzinfo:
        return v.replace(tzinfo=None)
    if isinstance(v, str):
        v = "".join(c for c in v if c in "\t\n\r" or ord(c) >= 32)  # caracteres que o Excel rejeita
        return v[:32767]
    return v
