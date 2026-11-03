"""Marés fora da EPAGRI: PR e SP.

- Marégrafos da rede SiMCosta (FURG), repassados ao vivo pela IOC/UNESCO: Paranaguá, Pontal do Sul,
  Antonina e Ilhabela. Guarda 60 dias de medição e calcula a maré astronômica por análise harmônica (UTide).
- Estações históricas da Universidade do Havaí (UHSLC): Cananéia e Ubatuba. Só a maré astronômica
  (constantes calculadas com anos de medição), recalculada uma vez por dia.

Saída: mare_<k>.json com linhas [t_ms, medido_cm, astronômica_cm, resíduo_cm] a cada 10 min,
de 3 dias atrás até 35 dias à frente, e "nmm" (nível médio, cm) — o mesmo formato que o app usa
para os marégrafos da EPAGRI.

Uso: python tools/mares.py <pasta anterior> <pasta nova>
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import urllib.request

import numpy as np
import utide

old, out = sys.argv[1], sys.argv[2]
EST = json.load(open(os.path.join(os.path.dirname(__file__), "estacoes.json"), encoding="utf-8"))
UA = {"User-Agent": "mare-boa-bot (github.com/hericsaliby/mare-boa-dados)"}
NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
HOJE = NOW.date().isoformat()
PASSO = np.timedelta64(10, "m")


def baixa(url, timeout=90):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def ler(nome):
    try:
        with open(os.path.join(old, nome), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def grava(nome, obj):
    with open(os.path.join(out, nome), "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))


def media10(t, h):
    """Médias de 10 min, sem picos (diferença > 1,5 m da mediana móvel do dia)."""
    k = t.astype("datetime64[m]").astype(np.int64) // 10
    u, inv = np.unique(k, return_inverse=True)
    hm = np.bincount(inv, h) / np.bincount(inv)
    tm = (u * 10).astype("datetime64[m]")
    ok = np.abs(hm - np.median(hm)) < 1.5
    return tm[ok], hm[ok]


def ioc(code, ini, fim):
    t, h = [], []
    a = ini
    while a < fim:  # a IOC entrega no máximo ~30 dias por pedido
        b = min(fim, a + dt.timedelta(days=28))
        url = (f"https://www.ioc-sealevelmonitoring.org/service.php?query=data&code={code}"
               f"&timestart={a:%Y-%m-%dT%H:%M}&timestop={b:%Y-%m-%dT%H:%M}&format=json")
        dados = json.loads(baixa(url))
        sens = {}
        for x in dados:
            if x.get("slevel") is not None:
                sens.setdefault(x.get("sensor"), []).append(x)
        if sens:
            principal = max(sens, key=lambda s: len(sens[s]))  # o sensor com mais dados (radar, em geral)
            for x in sens[principal]:
                t.append(np.datetime64(x["stime"].replace(" ", "T")))
                h.append(float(x["slevel"]))
        a = b
    return np.array(t, dtype="datetime64[m]"), np.array(h)


def linhas(tp, ast, obs_t=None, obs_h=None):
    """[t_ms, medido_cm, astronômica_cm, resíduo_cm]"""
    medido = {}
    if obs_t is not None:
        for t, h in zip(obs_t, obs_h):
            medido[int(t.astype("datetime64[ms]").astype(np.int64))] = h
    out = []
    for t, a in zip(tp, ast):
        ms = int(t.astype("datetime64[ms]").astype(np.int64))
        o = medido.get(ms)
        out.append([ms, None if o is None else round(o * 100, 1), round(float(a) * 100, 1),
                    None if o is None else round((o - a) * 100, 1)])
    return out


def previsao(coef):
    ini = np.datetime64(NOW - dt.timedelta(days=3), "m")
    ini = ini - (ini.astype(np.int64) % 10).astype("timedelta64[m]")
    tp = np.arange(ini, ini + np.timedelta64(38 * 24 * 60, "m"), PASSO)
    return tp, utide.reconstruct(tp, coef, verbose=False).h


hist = ler("mare_hist.json") or {}
novo_hist = {}
for e in EST["ioc"]:
    k = e["k"]
    try:
        h0 = hist.get(k) or {}
        T = np.array(h0.get("t", []), dtype="datetime64[m]")
        H = np.array(h0.get("h", []), dtype=float)
        ini = NOW - dt.timedelta(days=45 if len(T) < 2000 else 2)
        t, h = ioc(e["code"], ini, NOW + dt.timedelta(hours=1))
        if len(t):
            t, h = media10(t, h)
            junta = dict(zip(T.astype(np.int64), H))
            junta.update(zip(t.astype(np.int64), h))
            lim = np.datetime64(NOW - dt.timedelta(days=60), "m").astype(np.int64)
            ks = sorted(x for x in junta if x >= lim)
            T = np.array(ks, dtype="datetime64[m]")
            H = np.array([junta[x] for x in ks])
        novo_hist[k] = {"t": T.astype(np.int64).tolist(), "h": [round(float(x), 3) for x in H]}
        if len(T) < 14 * 144:
            print(k, "histórico curto:", len(T), "pontos")
            continue
        coef = utide.solve(T, H, lat=e["lat"], method="ols", conf_int="none", verbose=False, trend=False)
        tp, ast = previsao(coef)
        recentes = T >= np.datetime64(NOW - dt.timedelta(days=3), "m")
        grava(f"mare_{k}.json", {"n": e["n"], "fonte": "SiMCosta/FURG via IOC", "nmm": round(float(coef.mean) * 100, 1),
                                 "ultimo": int(T[-1].astype("datetime64[ms]").astype(np.int64)),
                                 "rows": linhas(tp, ast, T[recentes], H[recentes])})
        print(k, "ok:", len(T), "pontos no histórico; última medição", T[-1])
    except Exception as x:
        import traceback; traceback.print_exc()
        print(k, "falhou:", x)
        if k in hist:
            novo_hist[k] = hist[k]
        o = ler(f"mare_{k}.json")
        if o:
            grava(f"mare_{k}.json", o)
grava("mare_hist.json", novo_hist)

for e in EST["uhslc"]:
    k, nome = e["k"], f"mare_{e['k']}.json"
    o = ler(nome)
    if o and o.get("dia") == HOJE:
        grava(nome, o)
        continue
    try:
        txt = baixa(f"https://uhslc.soest.hawaii.edu/erddap/tabledap/global_hourly_rqds.csv?time,sea_level&uhslc_id={e['id']}&time%3E=2005-01-01", 180).decode()
        rows = [r for r in csv.reader(io.StringIO(txt))][2:]
        rows = [r for r in rows if len(r) == 2 and r[1] not in ("", "NaN")]
        T = np.array([np.datetime64(r[0].rstrip("Z"), "m") for r in rows])
        H = np.array([float(r[1]) / 1000 for r in rows])
        T, H = T[-5 * 8766:], H[-5 * 8766:]  # até 5 anos
        coef = utide.solve(T, H, lat=e["lat"], method="ols", conf_int="none", verbose=False, trend=False)
        tp, ast = previsao(coef)
        grava(nome, {"n": e["n"], "fonte": "maré astronômica (UHSLC, constantes harmônicas)", "nmm": round(float(coef.mean) * 100, 1),
                     "dia": HOJE, "rows": linhas(tp, ast)})
        print(k, "ok:", len(T), "horas de", T[0], "a", T[-1])
    except Exception as x:
        print(k, "falhou:", x)
        if o:
            grava(nome, o)
