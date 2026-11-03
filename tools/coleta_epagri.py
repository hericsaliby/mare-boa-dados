"""Copia os marégrafos (a cada rodada) e as tábuas anuais (uma vez por dia) da EPAGRI/CIRAM.

Uso: python tools/coleta_epagri.py <pasta anterior> <pasta nova>
Se a EPAGRI não responder, mantém a cópia anterior.
"""
import datetime as dt
import json
import os
import shutil
import sys
import urllib.request

old, out = sys.argv[1], sys.argv[2]
EST = json.load(open(os.path.join(os.path.dirname(__file__), "estacoes.json"), encoding="utf-8"))["epagri"]
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"}
HOJE = dt.date.today().isoformat()


def baixa(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def manter(nome):
    if os.path.exists(os.path.join(old, nome)):
        shutil.copy(os.path.join(old, nome), out)


dia_tabua = open(os.path.join(old, "tabua_dia.txt")).read().strip() if os.path.exists(os.path.join(old, "tabua_dia.txt")) else ""
for e in EST:
    if e["id"]:
        nome = e["k"] + ".json"
        try:
            b = baixa(f"https://ciram.epagri.sc.gov.br/graficos/getDataMare{e['id']}.php")
            json.loads(b.decode("utf-8-sig"))  # só grava se for JSON válido
            open(os.path.join(out, nome), "wb").write(b)
        except Exception as x:
            print("marégrafo", e["k"], "falhou:", x)
            manter(nome)
    nome = f"tabua_{e['csv']}.csv"
    if dia_tabua == HOJE and os.path.exists(os.path.join(old, nome)):
        manter(nome)
        continue
    try:
        b = baixa(f"https://ciram.epagri.sc.gov.br/ciram_arquivos/oceano/tabuamare/csv/Tabua_Mare_{e['csv']}.csv")
        if len(b) < 200 or b"<html" in b[:500].lower():
            raise ValueError("resposta não é CSV")
        open(os.path.join(out, nome), "wb").write(b)
    except Exception as x:
        print("tábua", e["csv"], "falhou:", x)
        manter(nome)
open(os.path.join(out, "tabua_dia.txt"), "w").write(HOJE)
