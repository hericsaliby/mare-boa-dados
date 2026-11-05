"""Avisos meteorológicos do INMET (tempestade, ventos costeiros, chuva forte...) que tocam o litoral do app.

Fonte: https://apiprevmet3.inmet.gov.br/avisos/ativos (avisos de hoje e dos próximos dias, com o desenho da área).
O INMET permite reproduzir o conteúdo citando a fonte.
Saída: alertas_inmet.json = {"at", "fonte", "alertas": [{id, evento, sev, nivel, cor, ini, fim, riscos, instr, estados, mun, areas}]}
  mun: lista oficial de municípios atingidos (só SC, PR e SP), a referência principal do app
  nivel: 1 = Perigo Potencial (amarelo), 2 = Perigo (laranja), 3 = Grande Perigo (vermelho)
  areas: lista de polígonos [[lat, lon], ...] (simplificados)
Se o INMET não responder, mantém o arquivo anterior.
Uso: python tools/inmet.py <pasta antiga> <pasta nova>
"""
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.request

OLD, OUT = sys.argv[1], sys.argv[2]
NOME = "alertas_inmet.json"
URL = "https://apiprevmet3.inmet.gov.br/avisos/ativos"
# litoral coberto pelo app (SC, PR e SP), com folga
BOX = {"la0": -30.0, "la1": -22.5, "lo0": -51.0, "lo1": -44.0}
NIVEL = {"Perigo Potencial": 1, "Perigo": 2, "Grande Perigo": 3}
BRT = dt.timezone(dt.timedelta(hours=-3))


def baixa():
    for k in range(4):
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": "mare-boa-bot (github.com/hericsaliby/mare-boa-dados)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            print("INMET tentativa", k + 1, e)
            time.sleep(5 * (k + 1))
    return None


def aneis(geo):
    """Anéis externos do GeoJSON como [[lat, lon], ...]."""
    try:
        g = json.loads(geo) if isinstance(geo, str) else geo
    except Exception:
        return []
    if not g:
        return []
    if g.get("type") == "Polygon":
        polys = [g["coordinates"]]
    elif g.get("type") == "MultiPolygon":
        polys = g["coordinates"]
    else:
        return []
    return [[[round(p[1], 3), round(p[0], 3)] for p in poly[0]] for poly in polys if poly and poly[0]]


def simplifica(anel, maxp=160):
    if len(anel) <= maxp:
        return anel
    passo = len(anel) / maxp
    out = [anel[int(i * passo)] for i in range(maxp)]
    return out + [anel[0]]


def toca(anel):
    la = [p[0] for p in anel]
    lo = [p[1] for p in anel]
    return not (max(la) < BOX["la0"] or min(la) > BOX["la1"] or max(lo) < BOX["lo0"] or min(lo) > BOX["lo1"])


def quando(s):
    try:
        return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=BRT).timestamp() * 1000)
    except Exception:
        return None


def lista(x):
    if isinstance(x, list):
        return [str(v) for v in x]
    s = str(x or "").strip()
    if s.startswith("["):
        try:
            import ast
            return [str(v) for v in ast.literal_eval(s)]
        except Exception:
            pass
    return [s] if s else []


# municípios dos pontos do app (o app liga cada ponto ao seu município; os pontos criados pelo usuário usam o desenho da área)
COSTA = {"Itapoá - SC", "São Francisco do Sul - SC", "Balneário Barra do Sul - SC", "Barra Velha - SC", "Itajaí - SC",
         "Navegantes - SC", "Balneário Camboriú - SC", "Bombinhas - SC", "Porto Belo - SC", "Governador Celso Ramos - SC",
         "Florianópolis - SC", "São José - SC", "Biguaçu - SC", "Palhoça - SC", "Garopaba - SC", "Imbituba - SC",
         "Laguna - SC", "Balneário Rincão - SC", "Passo de Torres - SC", "Pontal do Paraná - PR", "Paranaguá - PR",
         "Antonina - PR", "Matinhos - PR", "Guaratuba - PR", "Ubatuba - SP", "Caraguatatuba - SP", "São Sebastião - SP",
         "Ilhabela - SP", "Bertioga - SP", "Guarujá - SP", "Santos - SP", "São Vicente - SP", "Praia Grande - SP",
         "Mongaguá - SP", "Itanhaém - SP", "Peruíbe - SP", "Iguape - SP", "Ilha Comprida - SP", "Cananéia - SP"}


def municipios(txt):
    """Lista oficial de municípios do aviso ("Florianópolis - SC (4205407)"), só dos estados do app."""
    out = []
    for parte in str(txt or "").split(","):
        nome = parte.split("(")[0].strip()
        if nome in COSTA:
            out.append(nome)
    return out


def main():
    j = baixa()
    if j is None:
        velho = os.path.join(OLD, NOME)
        if os.path.exists(velho):
            shutil.copy(velho, os.path.join(OUT, NOME))
        print("INMET sem resposta; mantido o arquivo anterior")
        return
    out, vistos = [], set()
    for grupo in ("hoje", "futuro"):
        for a in j.get(grupo) or []:
            areas = [simplifica(r) for r in aneis(a.get("poligono")) if len(r) >= 3]
            areas = [r for r in areas if toca(r)]
            if not areas:
                continue
            cid = a.get("codigo") or a.get("id")
            if cid in vistos:
                continue
            vistos.add(cid)
            sev = a.get("severidade") or ""
            out.append({"id": str(cid), "evento": a.get("descricao") or "Aviso", "sev": sev, "nivel": NIVEL.get(sev, 1),
                        "cor": a.get("aviso_cor"), "ini": quando(a.get("inicio") or ""), "fim": quando(a.get("fim") or ""),
                        "riscos": lista(a.get("riscos"))[:4], "instr": lista(a.get("instrucoes"))[:6],
                        "estados": a.get("estados") or "", "mun": municipios(a.get("municipios")), "areas": areas})
    out.sort(key=lambda x: (x["ini"] or 0, -x["nivel"]))
    with open(os.path.join(OUT, NOME), "w", encoding="utf-8") as f:
        json.dump({"at": int(time.time() * 1000), "fonte": "INMET (avisos.inmet.gov.br)", "alertas": out}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(len(out), "avisos do INMET no litoral do app")


if __name__ == "__main__":
    main()
