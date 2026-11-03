"""Temperatura da água medida nos marégrafos da EPAGRI (Litoral On-Line), de hora em hora.

Algumas estações (hoje Itapoá, Balneário Camboriú, Florianópolis Sul da Ilha e Imbituba) têm sensor de
temperatura. A EPAGRI também publica uma previsão de 5 dias por estação. O app usa a medição para
corrigir o modelo da Open-Meteo perto da costa e nas baías.

Saída: temp_mar.json = {"at": epoch, "est": [{"c", "n", "lat", "lon", "obs": [[t, °C]], "prev": [[t, °C]]}]}
(t em segundos; "obs" guarda até 10 dias somando as rodadas anteriores).

Uso: python tools/temp_mar.py <pasta anterior> <pasta nova>
"""
import json
import os
import sys
import time
import urllib.request

old, out = sys.argv[1], sys.argv[2]
B = "https://ciram.epagri.sc.gov.br/api/litoral-online-server"
W = B + "/webresources/monitoramentolitoral/"
HDR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Referer": "https://ciram.epagri.sc.gov.br/litoral-online/", "Origin": "https://ciram.epagri.sc.gov.br"}
NOME = "temp_mar.json"
AGORA = int(time.time())


def pede(url, corpo=None):
    data = None if corpo is None else json.dumps(corpo).encode()
    h = dict(HDR, **({"Content-Type": "application/json"} if data is not None else {}))
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET"), timeout=45) as r:
        return json.loads(r.read().decode("utf-8-sig") or "null")


def serie(c, v):
    try:
        a = pede(f"{B}/cache/{c}{v}semana.txt?v={AGORA // 600}")
    except Exception:
        return []
    return [[int(t) // 1000, round(float(x), 2)] for t, x in (a or []) if x is not None and 10 <= float(x) <= 34]


def antigo():
    try:
        with open(os.path.join(old, NOME), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    ant = antigo()
    if ant and AGORA - ant.get("at", 0) < 40 * 60:  # os dados são de hora em hora
        json.dump(ant, open(os.path.join(out, NOME), "w"), separators=(",", ":"))
        return
    try:
        mapa = pede(W + "estacoesMapa", {})
        est = [e for e in mapa.get("estacoes", []) if 2900 <= int(e.get("estacao", 0)) < 3000]
        if not est:
            raise ValueError("lista de estações vazia")
        velhas = {x["c"]: x for x in (ant or {}).get("est", [])}
        res = []
        for e in est:
            c = int(e["estacao"])
            try:
                pede(W + "variaveisGrafSemana", {"cdEstac": c, "variaveis": [381, 382]})  # prepara os arquivos
            except Exception as x:
                print(c, "pedido falhou:", x)
            obs, prev = serie(c, 381), serie(c, 382)
            junta = {t: v for t, v in velhas.get(c, {}).get("obs", [])}
            junta.update({t: v for t, v in obs})
            obs = sorted([t, v] for t, v in junta.items() if t >= AGORA - 10 * 86400)
            if not obs and not prev:
                continue
            res.append({"c": c, "n": e.get("nome", str(c)).replace("Florianopolis", "Florianópolis").replace("Bal. ", "Balneário "), "lat": float(e["lat"]), "lon": float(e["lon"]), "obs": obs, "prev": prev})
            print(c, e.get("nome"), "medidas:", len(obs), "previsão:", len(prev), "última:", obs[-1] if obs else None)
        if not any(x["obs"] for x in res):
            raise ValueError("nenhuma medição")
        json.dump({"at": AGORA, "fonte": "EPAGRI/CIRAM Litoral On-Line", "est": res}, open(os.path.join(out, NOME), "w"), separators=(",", ":"))
    except Exception as x:
        print("temperatura da água falhou:", x)
        if ant:
            json.dump(ant, open(os.path.join(out, NOME), "w"), separators=(",", ":"))


main()
