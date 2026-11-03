"""Relevo e contorno do mar da Grande Florianópolis a partir do Copernicus DEM (30 m, AWS, gratuito).

Gera, uma vez só:
- agua.png: máscara da água em projeção do mapa (Web Mercator), ~80 m por pixel, com borda suave.
  O app recorta a temperatura da água com ela, então a cor fica só no mar, com a costa nítida.
- relevo.json: altitude média em células de ~850 m, para o abrigo de um pin novo e o teste "é água?".

Uso: python tools/relevo.py <pasta de saída>
"""
import json
import math
import os
import sys

import numpy as np
import rasterio
from PIL import Image

from rasterio.enums import Resampling

OUT = sys.argv[1]
VERSAO = 3
ARC = 1200                                # lê o Copernicus reduzido para ~90 m (memória do robô)
# região: (oeste, leste, sul, norte, pixel da máscara em graus, célula do relevo em graus)
REGIOES = {
    "sc": (-50.0, -48.2, -29.6, -25.85, 0.0015, 0.01),
    "pr": (-48.95, -47.75, -26.1, -24.9, 0.001, 0.008),
    "sp": (-48.2, -44.6, -25.4, -23.2, 0.0015, 0.01),
}


def ja_feito(r):
    try:
        with open(os.path.join(OUT, f"relevo_{r}.json")) as f:
            return json.load(f).get("v") == VERSAO and os.path.exists(os.path.join(OUT, f"agua_{r}.png"))
    except Exception:
        return False


def tile_url(lat0, lon0):
    ns = f"{'S' if lat0 < 0 else 'N'}{abs(lat0):02d}_00"
    ew = f"{'W' if lon0 < 0 else 'E'}{abs(lon0):03d}_00"
    n = f"Copernicus_DSM_COG_10_{ns}_{ew}_DEM"
    return f"https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"


def mosaico(W, E, S, N):
    """Altitudes em grade de 1" cobrindo a área; onde não há tile (mar aberto), 0."""
    la_hi, la_lo = math.ceil(N), math.floor(S)
    lo_lo, lo_hi = math.floor(W), math.ceil(E)
    rows, cols = (la_hi - la_lo) * ARC, (lo_hi - lo_lo) * ARC
    dem = np.zeros((rows, cols), dtype=np.float32)
    for lat_top in range(la_hi, la_lo, -1):
        for lon_left in range(lo_lo, lo_hi):
            url = tile_url(lat_top - 1, lon_left)
            try:
                with rasterio.open(url) as r:
                    a = r.read(1, out_shape=(ARC, ARC), resampling=Resampling.average)
            except Exception as e:
                print("sem tile (mar aberto?)", url.rsplit("/", 1)[-1], type(e).__name__)
                continue
            a = np.where(np.isfinite(a), a, 0)[:ARC, :ARC]
            r0, c0 = (la_hi - lat_top) * ARC, (lon_left - lo_lo) * ARC
            dem[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a
            print("tile", url.rsplit("/", 1)[-1], a.shape)
    return dem, la_hi, lo_lo


def media_em_blocos(integral, r0, r1, c0, c1):
    return (integral[r1, c1] - integral[r0, c1] - integral[r1, c0] + integral[r0, c0]) / np.maximum(1, (r1 - r0) * (c1 - c0))


def integral_de(a):
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    out[1:, 1:] = a.astype(np.float64).cumsum(0).cumsum(1)
    return out


def regiao(nome, W, E, S, N, PX, ST):
    if ja_feito(nome):
        print(nome, "já pronto")
        return
    dem, la_hi, lo_lo = mosaico(W, E, S, N)
    row = lambda lat: np.clip(np.round((la_hi - lat) * ARC).astype(int), 0, dem.shape[0])
    col = lambda lon: np.clip(np.round((lon - lo_lo) * ARC).astype(int), 0, dem.shape[1])

    # máscara da água em Web Mercator: fração de água em cada pixel vira transparência
    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    nx = int(round((E - W) / PX))
    ym_n, ym_s = merc(N), merc(S)
    ny = int(round((ym_n - ym_s) / math.radians(PX)))
    ys = ym_n - (np.arange(ny + 1)) * (ym_n - ym_s) / ny
    lat_edges = np.degrees(2 * np.arctan(np.exp(ys)) - math.pi / 2)
    lon_edges = W + np.arange(nx + 1) * (E - W) / nx
    agua = integral_de((dem <= 0.5).astype(np.float32))
    R, C = row(lat_edges), col(lon_edges)
    frac = media_em_blocos(agua, R[:-1, None], R[1:, None], C[None, :-1], C[None, 1:])
    alpha = np.clip(np.round(frac * 255), 0, 255).astype(np.uint8)
    img = Image.new("LA", (nx, ny))
    img.putdata(list(zip([255] * (nx * ny), alpha.ravel().tolist())))
    img.save(os.path.join(OUT, f"agua_{nome}.png"), optimize=True)

    # relevo médio em células de ~850 m
    nla, nlo = int(round((N - S) / ST)) + 1, int(round((E - W) / ST)) + 1
    alt = integral_de(np.maximum(dem, 0))
    la_c = S + np.arange(nla) * ST
    lo_c = W + np.arange(nlo) * ST
    R0, R1 = row(la_c + ST / 2), row(la_c - ST / 2)
    C0, C1 = col(lo_c - ST / 2), col(lo_c + ST / 2)
    h = media_em_blocos(alt, R0[:, None], R1[:, None], C0[None, :], C1[None, :])
    with open(os.path.join(OUT, f"relevo_{nome}.json"), "w") as f:
        json.dump({"v": VERSAO, "la0": S, "lo0": W, "st": ST, "nla": nla, "nlo": nlo,
                   "h": [int(round(x)) for x in h.ravel()],
                   "agua": {"n": N, "s": S, "w": W, "e": E}}, f, separators=(",", ":"))
    print(nome, "agua", nx, "x", ny, os.path.getsize(os.path.join(OUT, f"agua_{nome}.png")) // 1024, "KB; relevo", nla, "x", nlo)


def main():
    import shutil
    for nome, p in REGIOES.items():
        try:
            regiao(nome, *p)
        except Exception as e:
            print(nome, "falhou:", e)
    for a, b in (("relevo_sc.json", "relevo.json"), ("agua_sc.png", "agua.png")):  # nomes antigos
        if os.path.exists(os.path.join(OUT, a)):
            shutil.copy(os.path.join(OUT, a), os.path.join(OUT, b))


if __name__ == "__main__":
    main()
