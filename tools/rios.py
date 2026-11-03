"""Rios do litoral de SC (OpenStreetMap) com a distância de cada trecho até a foz, seguindo o rio.

O app usa para estimar quanto a maré atrasa e diminui num ponto de rio (sem enviar o ponto a ninguém:
o cálculo é feito no aparelho com este arquivo).

Saída: rios_sc.json = {"v":1, "rios":[{"n": nome, "foz": [lat,lon], "p": [[lat,lon,km_ate_foz], ...]}, ...]}
Uso: python tools/rios.py <pasta de saída>
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request

OUT = sys.argv[1]
NOME = "rios_sc.json"
BBOX = (-29.45, -49.95, -25.9, -48.3)  # sul, oeste, norte, leste: faixa litorânea de SC
SERVIDORES = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
Q = f'[out:json][timeout:240];way["waterway"~"^(river|canal)$"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});out geom tags;'


def km(a, b):
    la = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 110.57, (a[1] - b[1]) * 111.32 * math.cos(la))


def baixa():
    for u in SERVIDORES:
        try:
            req = urllib.request.Request(u, data=urllib.parse.urlencode({"data": Q}).encode(),
                                         headers={"User-Agent": "mare-boa-bot (github.com/hericsaliby/mare-boa-dados)"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except Exception as e:
            print("overpass falhou", u, e)
    return None


def simplifica(pts, tol=0.03):
    """Douglas-Peucker em km, mantendo a terceira coordenada (km até a foz)."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dmax, im = 0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        # distância do ponto ao segmento a-b (aproximação plana)
        ax, ay = 0, 0
        bx, by = (b[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])), (b[0] - a[0]) * 110.57
        px, py = (p[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])), (p[0] - a[0]) * 110.57
        L = bx * bx + by * by
        t = 0 if L == 0 else max(0, min(1, (px * bx + py * by) / L))
        d = math.hypot(px - t * bx, py - t * by)
        if d > dmax:
            dmax, im = d, i
    if dmax <= tol:
        return [a, b]
    return simplifica(pts[:im + 1], tol)[:-1] + simplifica(pts[im:], tol)


def main():
    j = baixa()
    if not j:
        sys.exit("sem dados do OpenStreetMap")
    ways = [e for e in j.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    print(len(ways), "trechos de rio")
    # grafo no sentido da correnteza (no OSM o rio é desenhado da nascente para a foz).
    # Um afluente costuma terminar no MEIO de um trecho do rio principal: por isso o índice tem todos os nós.
    no = lambda g: (round(g["lat"], 6), round(g["lon"], 6))
    pos = {}
    for w in ways:
        g = w["geometry"]
        w["_km"] = [km((g[i]["lat"], g[i]["lon"]), (g[i + 1]["lat"], g[i + 1]["lon"])) for i in range(len(g) - 1)]
        for i, p in enumerate(g[:-1]):
            pos.setdefault(no(p), []).append((w, i))
    memo = {}

    def ate_foz(n, vis):
        """km do nó n até a foz, seguindo rio abaixo, e o nó da foz."""
        if n in memo:
            return memo[n]
        if n in vis:
            return (0.0, n)
        vis.add(n)
        cand = pos.get(n, [])
        if not cand:
            memo[n] = (0.0, n)
            return memo[n]
        w, i = max(cand, key=lambda x: (x[0].get("tags", {}).get("waterway") == "river", len(x[0]["geometry"])))
        resto = sum(w["_km"][i:])
        d, f = ate_foz(no(w["geometry"][-1]), vis)
        memo[n] = (resto + d, f)
        return memo[n]

    sys.setrecursionlimit(100000)
    rios = []
    for w in ways:
        g = w["geometry"]
        d_fim, foz = ate_foz(no(g[-1]), set())
        pts, acc = [], 0.0
        seg = w["_km"]
        tot = sum(seg)
        for i, p in enumerate(g):
            pts.append([round(p["lat"], 5), round(p["lon"], 5), round(d_fim + tot - acc, 2)])
            if i < len(seg):
                acc += seg[i]
        nome = w.get("tags", {}).get("name", "")
        r = {"n": nome, "foz": [round(foz[0], 4), round(foz[1], 4)], "p": simplifica(pts)}
        if w.get("tags", {}).get("waterway") == "canal":
            r["k"] = "c"  # canal de drenagem: o app prefere o rio de verdade quando os dois estão perto
        rios.append(r)
    rios = [r for r in rios if r["p"] and min(x[2] for x in r["p"]) <= 80]  # só até 80 km da foz (a maré não vai além)
    with open(os.path.join(OUT, NOME), "w", encoding="utf-8") as f:
        json.dump({"v": 1, "rios": rios}, f, ensure_ascii=False, separators=(",", ":"))
    print(len(rios), "trechos gravados,", os.path.getsize(os.path.join(OUT, NOME)) // 1024, "KB")


if __name__ == "__main__":
    main()
