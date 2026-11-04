"""Robô semanal: aprende com as pescarias da comunidade e mede quanto a nota acerta.

Lê (com a conta de serviço do Firebase, segredo FIREBASE_SA) as pescarias:
- "capturas": enviadas anonimamente por quem marcou "Ajudar a comunidade" (sem ponto, sem nome);
- "atividades": publicadas no feed.
Nenhum dado pessoal sai daqui: os arquivos gerados só têm contagens e pesos.

Gera, na pasta indicada:
- placar.json: para cada faixa de nota, em quantas saídas a pessoa pegou peixe / teve saída boa;
- pesos.json: pesos da nota por tipo de lugar (baía, costão...), ajustados com cuidado a partir dos
  pesos originais. Só muda quando há pescarias suficientes; com poucos dados, quase não mexe.

Uso: python tools/aprende.py <pasta de saída>
"""
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.request

OUT = sys.argv[1]
SA = os.environ.get("FIREBASE_SA", "").strip()
if not SA:
    print("FIREBASE_SA não configurado; nada a fazer.")
    sys.exit(0)

from google.oauth2 import service_account  # noqa: E402
from google.auth.transport.requests import Request  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conta import le_conta  # noqa: E402
info = le_conta(SA)
cred = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/datastore"])
cred.refresh(Request())
FS = f"https://firestore.googleapis.com/v1/projects/{info['project_id']}/databases/(default)/documents"

# pesos originais do app (index.html, WEIGHTS) — ponto de partida
BASE = {
    "costao": {"vento": .2, "mar": .23, "mare": .16, "sol": .12, "pres": .09, "chuva": .08, "agua": .12},
    "praia": {"vento": .2, "mar": .2, "mare": .2, "sol": .12, "pres": .09, "chuva": .07, "agua": .12},
    "baia": {"vento": .24, "mar": .07, "mare": .29, "sol": .12, "pres": .09, "chuva": .09, "agua": .1},
    "canal": {"vento": .16, "mar": .09, "mare": .35, "sol": .14, "pres": .07, "chuva": .09, "agua": .1},
    "lagoa": {"vento": .3, "mar": .04, "mare": .15, "sol": .24, "pres": .11, "chuva": .09, "agua": .07},
    "rio": {"vento": .16, "mar": .02, "mare": .32, "sol": .16, "pres": .1, "chuva": .14, "agua": .1},
}
MIN_TIPO = 30      # pescarias com fatores para ajustar um tipo de lugar
FORCA = 120        # quanto maior, mais devagar os pesos saem do original


def val(v):
    if "stringValue" in v: return v["stringValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return v["doubleValue"]
    if "booleanValue" in v: return v["booleanValue"]
    if "timestampValue" in v: return v["timestampValue"]
    if "nullValue" in v: return None
    if "arrayValue" in v: return [val(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: val(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def lista(col):
    out, tok = [], None
    while True:
        url = f"{FS}/{col}?pageSize=300" + (f"&pageToken={tok}" if tok else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cred.token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            print("lista", col, e.code)
            return out
        for d in j.get("documents", []):
            out.append({k: val(x) for k, x in d.get("fields", {}).items()})
        tok = j.get("nextPageToken")
        if not tok:
            return out


caps = lista("capturas")
ativs = lista("atividades")
print(len(caps), "capturas anônimas;", len(ativs), "pescarias no feed")

# ---------- junta as duas fontes (a mesma pescaria pode estar nas duas) ----------
amostras, vistos = [], set()
for c in caps:
    if c.get("res") is None:
        continue
    k = (c.get("t"), c.get("sp"), c.get("res"), c.get("qty"))
    vistos.add(k)
    amostras.append({"nota": c.get("nota"), "res": c["res"], "qty": c.get("qty"), "tipo": c.get("tipo"), "c": c.get("c"), "reg": c.get("reg"),
                     "v": c.get("v") or {}, "sp": c.get("sp"), "t": c.get("t"), "isca": c.get("isca")})
for a in ativs:
    k = (a.get("t"), a.get("sp"), a.get("res"), a.get("qty"))
    if k in vistos or a.get("res") is None:
        continue
    nota = (a.get("cond") or {}).get("nota")
    cond = a.get("cond") or {}
    v = {}
    if cond.get("mare") in ("enchendo", "vazando"):
        v["enchendo"] = 1 if cond["mare"] == "enchendo" else 0
    if cond.get("frente"):
        v["pre"] = 1
    amostras.append({"nota": nota, "res": a["res"], "qty": a.get("qty"), "tipo": None, "c": None, "reg": None,
                     "v": v, "sp": a.get("sp"), "t": a.get("t"), "isca": a.get("isca"), "pe": a.get("pe")})

boa = lambda s: s["res"] >= 2
peixe = lambda s: (s.get("qty") or 0) > 0 or s["res"] >= 1

# ---------- placar de acerto ----------
FAIXAS = [(0, 49, "até 49"), (50, 64, "50 a 64"), (65, 74, "65 a 74"), (75, 100, "75 ou mais")]
com_nota = [s for s in amostras if isinstance(s.get("nota"), (int, float))]
faixas = []
for a, b, nome in FAIXAS:
    g = [s for s in com_nota if a <= s["nota"] <= b]
    faixas.append({"de": a, "ate": b, "nome": nome, "n": len(g),
                   "boa": round(100 * sum(map(boa, g)) / len(g)) if g else None,
                   "peixe": round(100 * sum(map(peixe, g)) / len(g)) if g else None})
placar = {"at": int(dt.datetime.now(dt.timezone.utc).timestamp()), "n": len(com_nota), "total": len(amostras), "faixas": faixas}
json.dump(placar, open(os.path.join(OUT, "placar.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print("placar:", json.dumps(faixas, ensure_ascii=False))

# ---------- ajuste dos pesos ----------
# Para cada fator (0 a 1 na hora da pescaria), compara a média nas saídas boas com a média nas fracas.
# Fator bem mais alto nas boas -> ganha peso; fator igual ou mais alto nas fracas -> perde peso.
# A mudança é limitada (no máximo o dobro ou a metade) e cresce com o número de pescarias.
pesos = {"at": placar["at"], "minimo": MIN_TIPO, "tipos": {}}
com_fatores = [s for s in amostras if isinstance(s.get("c"), dict) and s.get("tipo") in BASE]
for tipo, w0 in BASE.items():
    g = [s for s in com_fatores if s["tipo"] == tipo]
    meia = False
    if len(g) < MIN_TIPO:   # poucas pescarias neste tipo de lugar: mantém os pesos originais
        pesos["tipos"][tipo] = {"n": len(g), "ajustado": False}
        continue
    bons, ruins = [s for s in g if boa(s)], [s for s in g if not boa(s)]
    if len(bons) < 8 or len(ruins) < 8:
        pesos["tipos"][tipo] = {"n": len([s for s in com_fatores if s["tipo"] == tipo]), "ajustado": False}
        continue
    conf = len(g) / (len(g) + FORCA) * (.5 if meia else 1)
    novo = {}
    for k, w in w0.items():
        mb = [s["c"][k] for s in bons if isinstance(s["c"].get(k), (int, float))]
        mr = [s["c"][k] for s in ruins if isinstance(s["c"].get(k), (int, float))]
        if len(mb) < 5 or len(mr) < 5:
            novo[k] = w
            continue
        lift = sum(mb) / len(mb) - sum(mr) / len(mr)        # de -1 a 1
        fator = math.exp(max(-.69, min(.69, 3 * lift)) * conf)  # entre 0,5x e 2x, suavizado pela confiança
        novo[k] = w * fator
    tot = sum(novo.values())
    novo = {k: round(v / tot, 4) for k, v in novo.items()}
    pesos["tipos"][tipo] = {"n": len(g), "ajustado": True, "conf": round(conf, 2), "w": novo}
    print(tipo, "n=", len(g), "conf=", round(conf, 2), novo)
# ---------- véspera de frente fria: dá mais peixe? ----------
# O app começa com um bônus pequeno (+4) porque isso ainda é hipótese. Com pelo menos 30 saídas na véspera e 30 fora dela,
# mede a diferença na taxa de saída boa e transforma em bônus (de -5 a +9 pontos), misturado com o +4 inicial
# conforme o número de pescarias (com 30 saídas na véspera, metade de cada).
MIN_FRENTE = 30
na_vespera = lambda s: isinstance((s.get("v") or {}).get("pre"), (int, float)) and s["v"]["pre"] > .3
pre = [s for s in amostras if na_vespera(s)]
fora = [s for s in amostras if not na_vespera(s)]
if len(pre) >= MIN_FRENTE and len(fora) >= MIN_FRENTE:
    tx_pre, tx_fora = sum(map(boa, pre)) / len(pre), sum(map(boa, fora)) / len(fora)
    conf = len(pre) / (len(pre) + MIN_FRENTE)
    medido = 2 * (tx_pre - tx_fora)              # 10 pontos de diferença na taxa de saída boa = +2 na nota
    bonus = max(-.5, min(.9, conf * medido + (1 - conf) * .4))  # parte do +4 do app e vai para o medido
    pesos["frente"] = {"n": len(pre), "n_fora": len(fora), "boa_vespera": round(100 * tx_pre), "boa_fora": round(100 * tx_fora),
                       "ajustado": True, "bonus": round(bonus, 2)}
else:
    pesos["frente"] = {"n": len(pre), "n_fora": len(fora), "ajustado": False, "minimo": MIN_FRENTE}
print("frente:", pesos["frente"])
json.dump(pesos, open(os.path.join(OUT, "pesos.json"), "w"), ensure_ascii=False, separators=(",", ":"))


# ---------- o que a comunidade está mostrando (para o painel do administrador) ----------
# Só contagens. Cada grupo mostra quantas saídas e em quantas % foi saída boa; com menos de 5 saídas, não tira conclusão.
def grupo(nome, filtro):
    g = [x for x in amostras if filtro(x)]
    return {"nome": nome, "n": len(g), "boa": round(100 * sum(map(boa, g)) / len(g)) if g else None}


def hora(x):
    t = x.get("t")
    return None if not isinstance(t, (int, float)) else int(((t / 1000 if t > 1e11 else t) - 3 * 3600) % 86400 // 3600)


V = lambda x, k: (x.get("v") or {}).get(k)
cond = [
    ("Maré", [grupo("enchendo", lambda x: V(x, "enchendo") == 1), grupo("vazando", lambda x: V(x, "enchendo") == 0)]),
    ("Movimento da maré", [grupo("correndo bem", lambda x: isinstance(V(x, "mov"), (int, float)) and V(x, "mov") >= .5),
                           grupo("perto da virada", lambda x: isinstance(V(x, "mov"), (int, float)) and V(x, "mov") < .5)]),
    ("Tamanho da maré", [grupo("grande (lua cheia/nova)", lambda x: isinstance(V(x, "rf"), (int, float)) and V(x, "rf") >= .6),
                         grupo("pequena/média", lambda x: isinstance(V(x, "rf"), (int, float)) and V(x, "rf") < .6)]),
    ("Período", [grupo("madrugada (0–6h)", lambda x: hora(x) is not None and hora(x) < 6), grupo("manhã (6–12h)", lambda x: hora(x) is not None and 6 <= hora(x) < 12),
                 grupo("tarde (12–18h)", lambda x: hora(x) is not None and 12 <= hora(x) < 18), grupo("noite (18–24h)", lambda x: hora(x) is not None and hora(x) >= 18)]),
    ("Frente fria", [grupo("véspera de frente", lambda x: isinstance(V(x, "pre"), (int, float)) and V(x, "pre") > .3),
                     grupo("pós-frente", lambda x: V(x, "post") == 1)]),
    ("Pressão em 3 h", [grupo("caindo", lambda x: isinstance(V(x, "dp3"), (int, float)) and V(x, "dp3") < -.15),
                        grupo("estável", lambda x: isinstance(V(x, "dp3"), (int, float)) and -.15 <= V(x, "dp3") <= .15),
                        grupo("subindo", lambda x: isinstance(V(x, "dp3"), (int, float)) and V(x, "dp3") > .15)]),
]
# espécies e iscas mais registradas
from collections import Counter
esp = Counter()
for x in amostras:
    for p in (x.get("pe") or [{"sp": x.get("sp"), "qty": x.get("qty")}]):
        if p.get("sp") and (p.get("qty") or 0) > 0:
            esp[p["sp"]] += p.get("qty") or 0
isc = Counter()
for x in amostras:
    if x.get("isca") and boa(x):
        isc[x["isca"].strip().lower()] += 1
# o que separa saída boa de fraca, por fator da nota (todas as pescarias com fatores)
fat = []
bons, ruins = [s for s in com_fatores if boa(s)], [s for s in com_fatores if not boa(s)]
for k in ["mare", "vento", "sol", "pres", "agua", "mar", "chuva"]:
    mb = [s["c"][k] for s in bons if isinstance(s["c"].get(k), (int, float))]
    mr = [s["c"][k] for s in ruins if isinstance(s["c"].get(k), (int, float))]
    fat.append({"k": k, "nb": len(mb), "nr": len(mr), "boa": round(sum(mb) / len(mb), 2) if mb else None, "fraca": round(sum(mr) / len(mr), 2) if mr else None})
analise = {"at": placar["at"], "total": len(amostras), "anonimas": len(caps), "feed": len(ativs), "com_nota": placar["n"], "com_fatores": len(com_fatores),
           "boas": sum(map(boa, amostras)), "cond": [{"t": t, "g": g} for t, g in cond], "especies": esp.most_common(10), "iscas": isc.most_common(8),
           "fatores": fat, "min_tipo": MIN_TIPO, "tipos": {k: v.get("n", 0) for k, v in pesos["tipos"].items()}}
json.dump(analise, open(os.path.join(OUT, "aprende.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print("análise:", analise["total"], "pescarias")
