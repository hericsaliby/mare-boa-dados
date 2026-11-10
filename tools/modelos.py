"""Baixa os modelos para o Maré Boa e grava no branch "dados".

- GFS 0,25° (NOAA/NOMADS): vento, rajada, pressão, temperatura, umidade, nuvens, chuva.
- ECMWF IFS 0,25° (dados abertos oficiais): as mesmas variáveis.
- GFS-Wave / WaveWatch III 0,25° (NOAA): altura, período e direção das ondas.
- Open-Meteo (só 3 vezes por semana): temperatura da água e correntes.

O app lê tudo pronto daqui; nenhum celular consulta essas fontes, e os pontos pessoais
nunca saem do aparelho (a previsão de cada ponto é interpolada da grade no próprio app).

Uso: python tools/modelos.py <pasta do branch dados>
"""
import datetime as dt
import json
import math
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pygrib

D = sys.argv[1]
# Área única baixada (de Torres a Ubatuba); depois é separada por região para o app baixar só a sua
G = {"la0": -29.75, "lo0": -50.25, "st": 0.25, "nla": 28, "nlo": 24}
LATS = [G["la0"] + i * G["st"] for i in range(G["nla"])]
LONS = [G["lo0"] + j * G["st"] for j in range(G["nlo"])]
BOX = dict(toplat=-23.0, bottomlat=-29.75, leftlon=360 - 50.25, rightlon=360 - 44.5)
# Regiões: grade do modelo (0,25°) e grade da água/correntes (~0,15°)
REGIOES = {
    "sc": {"la": (-29.75, -25.75), "lo": (-50.25, -47.5), "mar": {"la0": -29.6, "la1": -25.85, "lo0": -50.0, "lo1": -47.6, "nla": 26, "nlo": 17}},
    "pr": {"la": (-26.25, -24.75), "lo": (-49.0, -47.75), "mar": {"la0": -26.1, "la1": -24.9, "lo0": -48.95, "lo1": -47.75, "nla": 9, "nlo": 9}},
    "sp": {"la": (-25.5, -23.0), "lo": (-48.25, -44.5), "mar": {"la0": -25.4, "la1": -23.2, "lo0": -48.2, "lo1": -44.6, "nla": 15, "nlo": 25}},
}
SC = {"u": 10, "v": 10, "g": 10, "p": 10, "t": 10, "r": 100, "c": 1, "h": 1, "hs": 100, "tp": 10, "dp": 1}
UA = {"User-Agent": "mare-boa-bot (github.com/hericsaliby/mare-boa-dados)"}
NOW = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
KEEP = NOW - dt.timedelta(days=8)
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def get(url, rng=None, tries=4):
    for k in range(tries):
        try:
            h = dict(UA)
            if rng:
                h["Range"] = rng
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            time.sleep(6 * (k + 1))
        except Exception:
            time.sleep(6 * (k + 1))
    raise RuntimeError("falhou: " + url)


def exists(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=30) as r:
            return r.status in (200, 206)
    except Exception:
        return False


_IDX = {}


def crop(g):
    """Valores do campo nos nós da grade G (None onde não há dado, ex.: terra nas ondas)."""
    key = (g.Ni, g.Nj, g.latitudeOfFirstGridPointInDegrees, g.longitudeOfFirstGridPointInDegrees)
    if key not in _IDX:
        lats, lons = g.latlons()
        la1, lo1 = lats[:, 0], ((lons[0, :] + 180) % 360) - 180
        _IDX[key] = [(int(np.argmin(np.abs(la1 - la))), int(np.argmin(np.abs(lo1 - lo)))) for la in LATS for lo in LONS]
    vals = g.values
    m = np.ma.getmaskarray(vals)
    return [None if m[i, j] or not np.isfinite(vals[i, j]) else float(vals[i, j]) for i, j in _IDX[key]]


def messages(blob):
    with tempfile.NamedTemporaryFile(suffix=".grib2") as f:
        f.write(blob)
        f.flush()
        grbs = pygrib.open(f.name)
        out = list(grbs)
        grbs.close()
        return out


def ts(d):
    return int(d.timestamp())


def cycle_list(hours, back):
    c = NOW.replace(hour=NOW.hour // 6 * 6)
    out = []
    for k in range(back):
        x = c - dt.timedelta(hours=6 * k)
        if x.hour in hours:
            out.append(x)
    return out


def rh(t, td):
    es = lambda x: 6.112 * math.exp(17.62 * x / (243.12 + x))
    return max(0.0, min(100.0, 100 * es(td) / es(t)))


def rates(acc, steps):
    """acc[(ini, fim)] = campo acumulado (mm) -> taxa em mm/h no intervalo (passo anterior, passo]."""
    out = {}
    for a, b in zip(steps, steps[1:]):
        f = None
        if (a, b) in acc:
            f = [None if v is None else v / (b - a) for v in acc[(a, b)]]
        else:
            for (s, e), v in acc.items():
                if e == b and (s, a) in acc:
                    w = acc[(s, a)]
                    f = [None if x is None or y is None else max(0.0, (x - y) / (b - a)) for x, y in zip(v, w)]
                    break
        if f is not None:
            out[b] = f
    return out


# ---------------- GFS (NOMADS) ----------------
GFS_VARS = ["UGRD", "VGRD", "GUST", "PRMSL", "TMP", "RH", "TCDC", "APCP"]
GFS_LEVS = ["10_m_above_ground", "2_m_above_ground", "surface", "mean_sea_level", "entire_atmosphere"]


def gfs_url(c, f):
    q = {"dir": f"/gfs.{c:%Y%m%d}/{c:%H}/atmos", "file": f"gfs.t{c:%H}z.pgrb2.0p25.f{f:03d}", "subregion": ""}
    q.update({"var_" + v: "on" for v in GFS_VARS})
    q.update({"lev_" + v: "on" for v in GFS_LEVS})
    q.update({k: str(v) for k, v in BOX.items()})
    return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?" + urllib.parse.urlencode(q)


def gfs_kind(g):
    sn, tl, lv = g.shortName, g.typeOfLevel, g.level
    if sn in ("10u", "u") and tl == "heightAboveGround" and lv == 10:
        return "u"
    if sn in ("10v", "v") and tl == "heightAboveGround" and lv == 10:
        return "v"
    if sn in ("2t", "t") and tl == "heightAboveGround" and lv == 2:
        return "t"
    if sn in ("2r", "r") and tl == "heightAboveGround" and lv == 2:
        return "h"
    if sn in ("gust", "i10fg", "10fg"):
        return "g"
    if sn in ("prmsl", "msl"):
        return "p"
    if sn == "tcc" and tl in ("atmosphere", "entireAtmosphere"):
        return "c"
    if sn == "tp":
        return "acc"
    return None


def gfs_fields(c, f, acc):
    fl = {}
    for g in messages(get(gfs_url(c, f))):
        k = gfs_kind(g)
        if not k:
            continue
        v = crop(g)
        if k == "acc":
            acc[(g.startStep, g.endStep)] = v
            continue
        if k in ("u", "v", "g"):
            v = [None if x is None else x * 3.6 for x in v]
        elif k == "p":
            v = [None if x is None else x / 100 for x in v]
        elif k == "t":
            v = [None if x is None else x - 273.15 for x in v]
        fl[k] = v
    time.sleep(0.7)  # NOMADS bloqueia quem passa de ~120 pedidos por minuto
    return fl


# ---------------- vento amplo (só para o mapa, estilo Windy) ----------------
# Grade larga e mais grossa (0,5°, de 3 em 3 h): do RS ao RJ e ~500 km mar adentro. Só vento a 10 m (GFS).
GW = {"la0": -34.0, "lo0": -57.0, "st": 0.5, "nla": 27, "nlo": 35}
_IDXW = {}


def cropw(g):
    key = (g.Ni, g.Nj, g.latitudeOfFirstGridPointInDegrees, g.longitudeOfFirstGridPointInDegrees)
    if key not in _IDXW:
        lats, lons = g.latlons()
        la1, lo1 = lats[:, 0], ((lons[0, :] + 180) % 360) - 180
        _IDXW[key] = [(int(np.argmin(np.abs(la1 - (GW["la0"] + i * GW["st"])))), int(np.argmin(np.abs(lo1 - (GW["lo0"] + j * GW["st"])))))
                      for i in range(GW["nla"]) for j in range(GW["nlo"])]
    vals = g.values
    return [None if not np.isfinite(vals[i, j]) else float(vals[i, j]) for i, j in _IDXW[key]]


def vento_amplo(run, old):
    if not run:
        return old
    if old and old.get("run") == run and old.get("grid") == GW:
        return old
    c = dt.datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    box = dict(toplat=GW["la0"] + (GW["nla"] - 1) * GW["st"], bottomlat=GW["la0"], leftlon=360 + GW["lo0"],
               rightlon=360 + GW["lo0"] + (GW["nlo"] - 1) * GW["st"])
    tt, U, V = [], [], []
    for f in range(0, 169, 3):
        q = {"dir": f"/gfs.{c:%Y%m%d}/{c:%H}/atmos", "file": f"gfs.t{c:%H}z.pgrb2.0p25.f{f:03d}", "subregion": "",
             "var_UGRD": "on", "var_VGRD": "on", "lev_10_m_above_ground": "on"}
        q.update({k: str(v) for k, v in box.items()})
        try:
            u = v = None
            for g in messages(get("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?" + urllib.parse.urlencode(q))):
                k = gfs_kind(g)
                if k == "u":
                    u = cropw(g)
                elif k == "v":
                    v = cropw(g)
            if u and v:
                tt.append(ts(c + dt.timedelta(hours=f)))
                U.append([None if x is None else int(round(x * 36)) for x in u])  # km/h × 10
                V.append([None if x is None else int(round(x * 36)) for x in v])
        except Exception as e:
            log("Vento amplo f%03d" % f, "erro", e)
        time.sleep(0.7)
    if len(tt) < 20:
        log("Vento amplo: poucos horários", len(tt))
        return old
    # guarda as últimas 24 h da rodada anterior (o mapa também mostra o passado recente)
    if old and old.get("grid") == GW:
        for i, t in enumerate(old["t"]):
            if t < tt[0] and t >= tt[0] - 86400:
                tt.insert(0, t), U.insert(0, old["u"][i]), V.insert(0, old["v"][i])
        o = sorted(range(len(tt)), key=lambda i: tt[i])
        tt, U, V = [tt[i] for i in o], [U[i] for i in o], [V[i] for i in o]
    log("Vento amplo:", len(tt), "horários,", GW["nla"] * GW["nlo"], "pontos")
    return {"run": run, "grid": GW, "t": tt, "u": U, "v": V}


def gfs_run(old):
    cyc = next((c for c in cycle_list((0, 6, 12, 18), 6)
                if exists(f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{c:%Y%m%d}/{c:%H}/atmos/gfs.t{c:%H}z.pgrb2.0p25.f168")), None)
    if not cyc:
        log("GFS: nenhuma rodada completa encontrada")
        return None
    if old and old.get("run") == f"{cyc:%Y%m%d%H}":
        log("GFS: rodada", f"{cyc:%Y%m%d%H}", "já processada")
        return old
    steps = list(range(0, 121)) + list(range(123, 169, 3))
    T, acc = {}, {}
    for f in steps:
        try:
            T[f] = gfs_fields(cyc, f, acc)
        except Exception as e:
            log("GFS f%03d" % f, "erro", e)
    rr = rates(acc, [s for s in steps if s in T])
    out = {ts(cyc + dt.timedelta(hours=f)): dict(fl, **({"r": rr[f]} if f in rr else {})) for f, fl in T.items()}
    hist = from_old(old, ts(cyc))
    if not hist:  # primeira vez: monta os últimos 7 dias com o início das rodadas anteriores
        for c in [cyc - dt.timedelta(hours=6 * k) for k in range(1, 29)]:
            try:
                a = {}
                f3, f6 = gfs_fields(c, 3, a), gfs_fields(c, 6, a)
                r = rates(a, [0, 3, 6])
                hist[ts(c + dt.timedelta(hours=3))] = dict(f3, **({"r": r[3]} if 3 in r else {}))
                hist[ts(c + dt.timedelta(hours=6))] = dict(f6, **({"r": r[6]} if 6 in r else {}))
            except Exception as e:
                log("GFS histórico", f"{c:%Y%m%d%H}", e)
    hist.update(out)
    log("GFS: rodada", f"{cyc:%Y%m%d%H}", len(out), "horários +", len(hist) - len(out), "do passado")
    return pack(f"{cyc:%Y%m%d%H}", hist)


# ---------------- ECMWF (dados abertos) ----------------
EC_PARAMS = ["10u", "10v", "msl", "2t", "2d", "tp", "tcc", "10fg"]
EC_BASES = ["https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com", "https://data.ecmwf.int/forecasts"]


def ec_file(base, c, step):
    return f"{base}/{c:%Y%m%d}/{c:%H}z/ifs/0p25/oper/{c:%Y%m%d%H}0000-{step}h-oper-fc"


def ec_fields(base, c, step):
    idx = [json.loads(l) for l in get(ec_file(base, c, step) + ".index").decode().splitlines() if l.strip()]
    raw = {}
    for e in idx:
        if e.get("param") in EC_PARAMS and e.get("levtype") == "sfc" and e["param"] not in raw:
            o, n = int(e["_offset"]), int(e["_length"])
            for g in messages(get(ec_file(base, c, step) + ".grib2", f"bytes={o}-{o + n - 1}")):
                raw[e["param"]] = crop(g)
    fl = {}
    k = lambda p, fn: [None if x is None else fn(x) for x in raw[p]] if p in raw else None
    for name, p, fn in (("u", "10u", lambda x: x * 3.6), ("v", "10v", lambda x: x * 3.6), ("g", "10fg", lambda x: x * 3.6),
                        ("p", "msl", lambda x: x / 100), ("t", "2t", lambda x: x - 273.15), ("c", "tcc", lambda x: x * 100)):
        v = k(p, fn)
        if v is not None:
            fl[name] = v
    if "2t" in raw and "2d" in raw:
        fl["h"] = [None if a is None or b is None else rh(a - 273.15, b - 273.15) for a, b in zip(raw["2t"], raw["2d"])]
    tp = k("tp", lambda x: x * 1000)
    return fl, tp


def ec_run(old):
    steps = list(range(0, 145, 3)) + list(range(150, 169, 6))
    base, cyc = None, None
    for c in cycle_list((0, 12), 8):
        for b in EC_BASES:
            if exists(ec_file(b, c, 168) + ".index"):
                base, cyc = b, c
                break
        if cyc:
            break
    if not cyc:
        log("ECMWF: nenhuma rodada completa encontrada")
        return None
    if old and old.get("run") == f"{cyc:%Y%m%d%H}":
        log("ECMWF: rodada", f"{cyc:%Y%m%d%H}", "já processada")
        return old
    out, acc = {}, {}

    def um(s):
        for k in range(3):
            try:
                return s, ec_fields(base, cyc, s)
            except Exception as e:
                err = e
                time.sleep(10 * (k + 1))
        log("ECMWF", s, "erro", err)
        return s, None
    with ThreadPoolExecutor(8) as ex:
        for s, r in ex.map(um, steps):
            if r:
                out[s] = r[0]
                if r[1] is not None:
                    acc[(0, s)] = r[1]
    rr = rates(acc, [s for s in steps if (0, s) in acc])
    res = {ts(cyc + dt.timedelta(hours=s)): dict(fl, **({"r": rr[s]} if s in rr else {})) for s, fl in out.items()}
    hist = from_old(old, ts(cyc))
    if not hist:
        def ciclo(c, tent=3):
            try:
                a, tmp = {}, {}
                for s in (0, 3, 6, 9, 12):
                    fl, tp = ec_fields(base, c, s)
                    if tp is not None:
                        a[(0, s)] = tp
                    tmp[s] = fl
                r = rates(a, [0, 3, 6, 9, 12])
                return {ts(c + dt.timedelta(hours=s)): dict(tmp[s], **({"r": r[s]} if s in r else {})) for s in (3, 6, 9, 12)}
            except Exception as e:
                if tent > 1:
                    time.sleep(10)
                    return ciclo(c, tent - 1)
                log("ECMWF histórico", f"{c:%Y%m%d%H}", e)
                return {}
        with ThreadPoolExecutor(7) as ex:
            for h in ex.map(ciclo, [cyc - dt.timedelta(hours=12 * k) for k in range(1, 15)]):
                hist.update(h)
    hist.update(res)
    log("ECMWF: rodada", f"{cyc:%Y%m%d%H}", len(res), "horários +", len(hist) - len(res), "do passado")
    return pack(f"{cyc:%Y%m%d%H}", hist)


# ---------------- Ondas (GFS-Wave / WaveWatch III) ----------------
def wave_url(c, f):
    q = {"dir": f"/gfs.{c:%Y%m%d}/{c:%H}/wave/gridded", "file": f"gfswave.t{c:%H}z.global.0p25.f{f:03d}.grib2",
         "var_HTSGW": "on", "var_PERPW": "on", "var_DIRPW": "on", "lev_surface": "on", "subregion": ""}
    q.update({k: str(v) for k, v in BOX.items()})
    return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?" + urllib.parse.urlencode(q)


def wave_fields(c, f):
    fl = {}
    for g in messages(get(wave_url(c, f))):
        n = (g.shortName + " " + g.name).lower()
        if "swh" in n or "htsgw" in n or "significant height of combined" in n:
            fl["hs"] = crop(g)
        elif "perpw" in n or "primary wave mean period" in n:
            fl["tp"] = crop(g)
        elif "dirpw" in n or "primary wave direction" in n:
            fl["dp"] = crop(g)
    time.sleep(0.7)
    return fl


def wave_run(old):
    cyc = next((c for c in cycle_list((0, 6, 12, 18), 6)
                if exists(f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{c:%Y%m%d}/{c:%H}/wave/gridded/gfswave.t{c:%H}z.global.0p25.f168.grib2")), None)
    if not cyc:
        log("Ondas: nenhuma rodada completa encontrada")
        return None
    if old and old.get("run") == f"{cyc:%Y%m%d%H}":
        log("Ondas: rodada", f"{cyc:%Y%m%d%H}", "já processada")
        return old
    out = {}
    for f in range(0, 169, 3):
        try:
            out[ts(cyc + dt.timedelta(hours=f))] = wave_fields(cyc, f)
        except Exception as e:
            log("Ondas f%03d" % f, "erro", e)
    hist = from_old(old, ts(cyc))
    if not hist:
        for c in [cyc - dt.timedelta(hours=6 * k) for k in range(1, 29)]:
            for f in (3, 6):
                try:
                    hist[ts(c + dt.timedelta(hours=f))] = wave_fields(c, f)
                except Exception as e:
                    log("Ondas histórico", f"{c:%Y%m%d%H}", e)
    hist.update(out)
    log("Ondas: rodada", f"{cyc:%Y%m%d%H}", len(out), "horários +", len(hist) - len(out), "do passado")
    return pack(f"{cyc:%Y%m%d%H}", hist)


# ---------------- formato compacto ----------------
def pack(run, by_t):
    tt = sorted(by_t)
    keys = sorted({k for fl in by_t.values() for k in fl})
    v = {k: [[None if x is None else int(round(x * SC[k])) for x in by_t[t][k]] if k in by_t[t] else None for t in tt] for k in keys}
    return {"run": run, "t": tt, "v": v}


def from_old(old, before):
    """Horários do arquivo anterior antes da rodada nova (o passado), dos últimos 8 dias;
    mais de 24 h atrás fica só de 3 em 3 h para o arquivo não crescer."""
    if not old:
        return {}
    out = {}
    for i, t in enumerate(old["t"]):
        if t >= before or t < ts(KEEP):
            continue
        if t < before - 86400 and (t // 3600) % 3:
            continue
        out[t] = {k: [None if x is None else x / SC[k] for x in arr[i]] for k, arr in old["v"].items() if arr[i] is not None}
    return out


# ---------------- Água e correntes (Open-Meteo, 3x por semana) ----------------
def cmems_mar(OVG):
    """Água e correntes do Copernicus Marine (modelo global 1/12°, com dados de satélite assimilados; uso comercial
    permitido com crédito). Precisa de COPERNICUSMARINE_SERVICE_USERNAME e _PASSWORD. Sem eles, devolve None."""
    if not (os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")):
        return None
    try:
        import copernicusmarine
        import numpy as np
        import xarray as xr
    except Exception as e:
        log("Copernicus: biblioteca ausente", e)
        return None
    m = 0.2
    ini = (NOW - dt.timedelta(days=3)).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    fim = (NOW + dt.timedelta(days=7)).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    arq = os.path.join(D, "_cmems_tmp.nc")
    try:
        copernicusmarine.subset(dataset_id="cmems_mod_glo_phy_anfc_0.083deg_PT1H-m", variables=["thetao", "uo", "vo"],
                                minimum_longitude=min(OVG["lo0"], OVG["lo1"]) - m, maximum_longitude=max(OVG["lo0"], OVG["lo1"]) + m,
                                minimum_latitude=min(OVG["la0"], OVG["la1"]) - m, maximum_latitude=max(OVG["la0"], OVG["la1"]) + m,
                                start_datetime=ini.strftime("%Y-%m-%dT%H:%M:%S"), end_datetime=fim.strftime("%Y-%m-%dT%H:%M:%S"),
                                minimum_depth=0, maximum_depth=1, output_filename=os.path.basename(arq), output_directory=D,
                                overwrite=True, disable_progress_bar=True)
        ds = xr.open_dataset(arq)
    except Exception as e:
        log("Copernicus: erro", e)
        return None
    try:
        if "depth" in ds.dims:
            ds = ds.isel(depth=0)
        lat, lon = ds["latitude"].values, ds["longitude"].values
        tt = [int(x) for x in (ds["time"].values.astype("datetime64[s]").astype("int64"))]
        TH, U, V = ds["thetao"].values, ds["uo"].values, ds["vo"].values  # (tempo, lat, lon)
        mar0 = ~np.isnan(TH[0])
        out = {"at": int(time.time()), "grid": OVG, "t": tt, "land": [], "sst": [], "cv": [], "cd": [], "fonte": "Copernicus Marine", "v": 2}
        r1 = lambda a, d: [None if (v is None or v != v) else round(float(v), d) for v in a]
        for i in range(OVG["nla"]):
            for j in range(OVG["nlo"]):
                la = OVG["la0"] + (OVG["la1"] - OVG["la0"]) * i / (OVG["nla"] - 1)
                lo = OVG["lo0"] + (OVG["lo1"] - OVG["lo0"]) * j / (OVG["nlo"] - 1)
                # célula de mar mais perto (busca até ~2 células); só vira "terra" se a mais perto passar de ~13 km.
                # A grade do Copernicus (~9 km) trata como terra muita água de baía; assim as baías continuam cobertas.
                ii, jj = np.abs(lat - la).argmin(), np.abs(lon - lo).argmin()
                best = None
                for di in range(-2, 3):
                    for dj in range(-2, 3):
                        a, b = ii + di, jj + dj
                        if 0 <= a < len(lat) and 0 <= b < len(lon) and mar0[a, b]:
                            d = math.hypot(lat[a] - la, (lon[b] - lo) * math.cos(math.radians(la)))
                            if best is None or d < best[0]:
                                best = (d, a, b)
                if best is None:
                    out["land"].append(True)
                    for k in ("sst", "cv", "cd"):
                        out[k].append([None] * len(tt))
                    continue
                _, a, b = best
                u, v = U[:, a, b], V[:, a, b]
                out["land"].append(bool(best[0] > 0.12))
                out["sst"].append(r1(TH[:, a, b], 1))
                out["cv"].append(r1(np.hypot(u, v) * 3.6, 2))  # m/s → km/h, como a Open-Meteo
                out["cd"].append(r1((np.degrees(np.arctan2(u, v)) + 360) % 360, 0))  # para onde a corrente vai
        log("Copernicus: água e correntes", len(tt), "horários,", sum(not x for x in out["land"]), "pontos de mar")
        return out
    finally:
        try:
            ds.close()
            os.remove(arq)
        except Exception:
            pass


def mar_run(old, OVG):
    novo = None
    if not (old and old.get("fonte") == "Copernicus Marine" and old.get("v") == 2 and old.get("grid") == OVG and time.time() - old.get("at", 0) < 20 * 3600):
        novo = cmems_mar(OVG)  # o Copernicus atualiza todo dia
    if novo:
        return novo
    if old and old.get("fonte") == "Copernicus Marine" and old.get("grid") == OVG and time.time() - old.get("at", 0) < 3 * 86400:
        return old
    if old and old.get("grid") == OVG:
        idade = time.time() - old.get("at", 0)
        if idade < 4 * 86400 and not (NOW.weekday() in (0, 2, 4) and idade > 30 * 3600):
            return old
    la, lo = [], []
    for i in range(OVG["nla"]):
        for j in range(OVG["nlo"]):
            la.append(round(OVG["la0"] + (OVG["la1"] - OVG["la0"]) * i / (OVG["nla"] - 1), 3))
            lo.append(round(OVG["lo0"] + (OVG["lo1"] - OVG["lo0"]) * j / (OVG["nlo"] - 1), 3))
    js = []
    for a in range(0, len(la), 120):  # em lotes, com pausa: a Open-Meteo limita consultas por minuto
        q = {"latitude": ",".join(map(str, la[a:a + 120])), "longitude": ",".join(map(str, lo[a:a + 120])),
             "hourly": "ocean_current_velocity,ocean_current_direction,sea_surface_temperature",
             "cell_selection": "sea", "timeformat": "unixtime", "past_days": "3", "forecast_days": "7"}
        try:
            r = json.loads(get("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(q)))
        except Exception as e:
            log("Água/correntes: erro", e)
            return old
        js += r if isinstance(r, list) else [r]
        time.sleep(30)
    t = js[0]["hourly"]["time"]
    land = [x.get("latitude") is not None and math.hypot(x["latitude"] - la[n], x["longitude"] - lo[n]) > 0.06 for n, x in enumerate(js)]
    rnd = lambda a, d: [None if v is None else round(v, d) for v in a]
    out = {"at": int(time.time()), "grid": OVG, "t": t, "land": land,
           "sst": [rnd(x["hourly"]["sea_surface_temperature"], 1) for x in js],
           "cv": [rnd(x["hourly"]["ocean_current_velocity"], 2) for x in js],
           "cd": [rnd(x["hourly"]["ocean_current_direction"], 0) for x in js]}
    return out


def recorte(sec, r):
    """Só os nós da região (grade própria, mesmo formato)."""
    i0, i1 = round((r["la"][0] - G["la0"]) / G["st"]), round((r["la"][1] - G["la0"]) / G["st"])
    j0, j1 = round((r["lo"][0] - G["lo0"]) / G["st"]), round((r["lo"][1] - G["lo0"]) / G["st"])
    idx = [i * G["nlo"] + j for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
    v = {k: [None if row is None else [row[n] for n in idx] for row in arr] for k, arr in sec["v"].items()}
    grid = {"la0": G["la0"] + i0 * G["st"], "lo0": G["lo0"] + j0 * G["st"], "st": G["st"], "nla": i1 - i0 + 1, "nlo": j1 - j0 + 1}
    return grid, {"run": sec["run"], "t": sec["t"], "v": v}


# Arquivos que o app baixa ficam mais leves sem mudar o que ele mostra (medido em 10/11/2026: modelos 441 -> ~410 KB,
# água/correntes 190 -> 126 KB comprimidos; notas iguais, água/correntes no máximo 1 ponto em 0,4% das horas): valores arredondados a passos menores que os da tela e sem o passado que o
# app não usa (ele olha 7 dias para trás). O formato não muda, então versões antigas do app continuam funcionando.
PASSO_MODELOS = {"c": 5, "h": 5}   # nuvem e umidade de 5 em 5%. O vento fica exato: arredondar até 0,2 km/h mudava
                                   # notas em até 9 pontos (a nota tem limites bruscos de vento); validado em 10/11/2026
PASSADO_S = 7.5 * 86400


def enxuga_modelos(out):
    corte = NOW.timestamp() - PASSADO_S
    for k in ("gfs", "ecmwf", "ondas"):
        sec = out.get(k)
        if not sec or not sec.get("t"):
            continue
        idx = [i for i, t in enumerate(sec["t"]) if t >= corte]
        sec["t"] = [sec["t"][i] for i in idx]
        for var, arr in sec["v"].items():
            arr = [arr[i] for i in idx]
            q = PASSO_MODELOS.get(var) if k != "ondas" else None
            if q:
                arr = [None if row is None else [None if x is None else int(round(x / q)) * q for x in row] for row in arr]
            sec["v"][var] = arr
    return out


def enxuga_mar(mar):
    """Direção da corrente de 5 em 5 graus (a tela mostra 8 direções) e velocidade de 0,02 em 0,02 km/h."""
    q = {"cd": 5, "cv": 0.02}
    for var, passo in q.items():
        if var in mar:
            mar[var] = [None if p is None else [None if x is None else round(round(x / passo) * passo, 2) for x in p] for p in mar[var]]
    if "cd" in mar:
        mar["cd"] = [None if p is None else [None if x is None else (0 if x >= 360 else int(x)) for x in p] for p in mar["cd"]]
    return mar


def load(name):
    try:
        with open(os.path.join(D, name)) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    import gzip
    try:
        with gzip.open(os.path.join(D, "modelos_base.json.gz"), "rt") as f:
            old = json.load(f)
        if old.get("grid") != G:
            old = {}
    except Exception:
        old = {}
    res = {"grid": G, "gerado": NOW.isoformat()}
    for k, fn in (("gfs", gfs_run), ("ecmwf", ec_run), ("ondas", wave_run)):
        try:
            res[k] = fn(old.get(k))
        except Exception as e:
            log(k, "falhou:", e)
            res[k] = old.get(k)
        if res[k] is None:
            res.pop(k)
    with gzip.open(os.path.join(D, "modelos_base.json.gz"), "wt") as f:
        json.dump(res, f, separators=(",", ":"))
    try:
        va = vento_amplo((res.get("gfs") or {}).get("run"), load("vento_amplo.json"))
        if va:
            with open(os.path.join(D, "vento_amplo.json"), "w") as f:
                json.dump(va, f, separators=(",", ":"))
    except Exception as e:
        log("Vento amplo falhou:", e)
    for nome, r in REGIOES.items():
        out = {"gerado": res["gerado"], "regiao": nome}
        for k in ("gfs", "ecmwf", "ondas"):
            if k in res:
                out["grid"], out[k] = recorte(res[k], r)
        with open(os.path.join(D, f"modelos_{nome}.json"), "w") as f:
            json.dump(enxuga_modelos(out), f, separators=(",", ":"))
        mar = mar_run(load(f"mar_{nome}.json"), r["mar"])
        if mar:
            with open(os.path.join(D, f"mar_{nome}.json"), "w") as f:
                json.dump(enxuga_mar(json.loads(json.dumps(mar))), f, separators=(",", ":"))
        log("Região", nome, "ok", "(água/correntes de", time.strftime("%d/%m %H:%M", time.gmtime(mar["at"])) + ")" if mar else "(sem água/correntes)")
    # nomes antigos (versões do app ainda em cache): SC
    import shutil
    shutil.copy(os.path.join(D, "modelos_sc.json"), os.path.join(D, "modelos.json"))
    if os.path.exists(os.path.join(D, "mar_sc.json")):
        shutil.copy(os.path.join(D, "mar_sc.json"), os.path.join(D, "mar.json"))
    with open(os.path.join(D, "modelos_log.txt"), "w") as f:
        f.write(NOW.isoformat() + "\n" + "\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
