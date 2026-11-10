#!/usr/bin/env bash
# SQL Workbench — inicialização no macOS/Linux
cd "$(dirname "$0")"
if [ ! -f .venv/pronto-wb1.txt ]; then
  echo "Preparando o SQL Workbench pela primeira vez..."
  [ -x .venv/bin/python ] || python3 -m venv .venv || { echo "Python 3.10+ não encontrado."; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || { echo "Falha ao instalar os pacotes."; exit 1; }
  for d in pymysql oracledb; do .venv/bin/python -m pip install "$d" || echo "Aviso: $d não instalado"; done
  echo ok > .venv/pronto-wb1.txt
fi
.venv/bin/python app.py
