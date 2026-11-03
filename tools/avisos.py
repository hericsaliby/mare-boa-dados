"""Envia avisos para o celular (Firebase Cloud Messaging), duas vezes por dia (11h40 e 17h40):

- frente fria chegando na região (uma vez por frente, cerca de 1 a 2 dias antes);
- véspera do dia de pesca da agenda (no fim da tarde do dia anterior);
- respostas e menções no chat.

Precisa do segredo FIREBASE_SA (JSON da conta de serviço do Firebase) no GitHub. Sem ele, não faz nada.
Os pontos de pesca nunca chegam aqui: o aparelho guarda só o token, a região, os dias da agenda e quais avisos quer.
"""
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.request

SA = os.environ.get("FIREBASE_SA", "").strip()
if not SA:
    print("FIREBASE_SA não configurado; nada a fazer.")
    sys.exit(0)

from google.oauth2 import service_account  # noqa: E402
from google.auth.transport.requests import Request  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conta import le_conta  # noqa: E402
info = le_conta(SA)
PROJ = info["project_id"]
cred = service_account.Credentials.from_service_account_info(
    info, scopes=["https://www.googleapis.com/auth/datastore", "https://www.googleapis.com/auth/firebase.messaging"])
cred.refresh(Request())
FS = f"https://firestore.googleapis.com/v1/projects/{PROJ}/databases/(default)/documents"
NOW = dt.datetime.now(dt.timezone.utc)
BRT = dt.timezone(dt.timedelta(hours=-3))
MIRROR = "https://hericsaliby.github.io/mare-boa-dados/"
APP = "https://hericsaliby.github.io/mare-boa/"
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
REG_N = {"sc": "SC", "pr": "no PR", "sp": "em SP"}


def http(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + cred.token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read()
            return r.status, json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def val(v):
    """Valor do formato REST do Firestore para Python."""
    if "stringValue" in v: return v["stringValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return v["doubleValue"]
    if "booleanValue" in v: return v["booleanValue"]
    if "timestampValue" in v: return v["timestampValue"]
    if "nullValue" in v: return None
    if "arrayValue" in v: return [val(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: val(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def enc(x):
    if isinstance(x, bool): return {"booleanValue": x}
    if isinstance(x, int): return {"integerValue": str(x)}
    if isinstance(x, float): return {"doubleValue": x}
    if isinstance(x, str): return {"stringValue": x}
    if isinstance(x, dict): return {"mapValue": {"fields": {k: enc(v) for k, v in x.items()}}}
    if isinstance(x, list): return {"arrayValue": {"values": [enc(v) for v in x]}}
    return {"nullValue": None}


def lista(col):
    out, tok = [], None
    while True:
        st, j = http("GET", f"{FS}/{col}?pageSize=300" + (f"&pageToken={tok}" if tok else ""))
        if st != 200:
            print("lista", col, st, j)
            return out
        for d in j.get("documents", []):
            out.append((d["name"].rsplit("/", 1)[1], {k: val(v) for k, v in d.get("fields", {}).items()}))
        tok = j.get("nextPageToken")
        if not tok:
            return out


def envia(uid, token, titulo, texto, link=APP, tag=None):
    msg = {"message": {"token": token, "notification": {"title": titulo, "body": texto},
                       "data": {"link": link, **({"tag": tag} if tag else {})},
                       "webpush": {"fcm_options": {"link": link}}}}
    st, j = http("POST", f"https://fcm.googleapis.com/v1/projects/{PROJ}/messages:send", msg)
    if st == 200:
        print("enviado:", uid[:6], titulo)
        return True
    err = json.dumps(j)
    print("falhou", uid[:6], st, err[:200])
    if st in (400, 404) and ("UNREGISTERED" in err or "registration-token-not-registered" in err or "INVALID_ARGUMENT" in err):
        http("DELETE", f"{FS}/push/{uid}")  # token velho: o aparelho desativou
    return False


# ---------------- estado (o que já foi enviado) ----------------
st, j = http("GET", f"{FS}/sistema/avisos")
EST = {k: val(v) for k, v in j.get("fields", {}).items()} if st == 200 else {}
EST.setdefault("frentes", {})
EST.setdefault("agenda", {})
desde = EST.get("chatDesde") or (NOW - dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")

PUSH = dict(lista("push"))
print(len(PUSH), "aparelhos com aviso")


# ---------------- frente fria por região ----------------
def frente(reg):
    """Momento previsto da frente (vento virando para sul com força depois do mínimo de pressão), a partir do GFS."""
    try:
        with urllib.request.urlopen(MIRROR + f"modelos_{reg}.json", timeout=60) as r:
            m = json.load(r)
    except Exception as e:
        print("modelos", reg, e)
        return None
    g, s = m["grid"], m.get("gfs")
    if not s:
        return None
    n = (g["nla"] // 2) * g["nlo"] + g["nlo"] - 2  # no mar, na borda leste e no meio da região
    T, P, U, V = s["t"], s["v"]["p"], s["v"]["u"], s["v"]["v"]
    pv = lambda i: P[i][n] / 10 if P[i] and P[i][n] is not None else None
    agora = NOW.timestamp()
    for i in range(1, len(T) - 1):
        if T[i] < agora or T[i] > agora + 72 * 3600:
            continue
        p = pv(i)
        if p is None:
            continue
        janela = [pv(k) for k in range(len(T)) if abs(T[k] - T[i]) <= 12 * 3600 and pv(k) is not None]
        if p > min(janela):
            continue
        depois = [pv(k) for k in range(len(T)) if 18 * 3600 <= T[k] - T[i] <= 30 * 3600 and pv(k) is not None]
        if not depois or max(depois) - p < 4:
            continue
        for k in range(i, len(T)):
            if T[k] - T[i] > 15 * 3600:
                break
            if not U[k] or U[k][n] is None:
                continue
            u, v = U[k][n] / 10, V[k][n] / 10
            vel, dr = math.hypot(u, v), (math.degrees(math.atan2(-u, -v)) + 360) % 360
            if vel >= 15 and 135 <= dr <= 260:
                return T[k]
    return None


for reg in ("sc", "pr", "sp"):
    ft = frente(reg)
    if not ft:
        continue
    falta = (ft - NOW.timestamp()) / 3600
    ja = EST["frentes"].get(reg)
    if not (18 <= falta <= 48) or (ja and abs(ja - ft) < 24 * 3600):
        continue
    quando = dt.datetime.fromtimestamp(ft, BRT)
    titulo = f"Frente fria chegando {REG_N[reg]}"
    texto = f"Prevista para {DIAS[quando.weekday()]} por volta das {quando:%H}h. A véspera costuma ser boa: o peixe come antes da virada. Veja as melhores horas."
    for uid, p in PUSH.items():
        if p.get("reg") == reg and (p.get("avisos") or {}).get("frente", True):
            envia(uid, p["token"], titulo, texto, APP + "#resumo", "frente-" + reg)
    EST["frentes"][reg] = int(ft)

# ---------------- véspera do dia de pesca ----------------
local = NOW.astimezone(BRT)
if local.hour >= 17:  # fim da tarde/noite da véspera (o GitHub às vezes atrasa a execução)
    amanha = (local + dt.timedelta(days=1)).date()
    dow_js = (amanha.weekday() + 1) % 7  # 0 = domingo, como no app
    for uid, p in PUSH.items():
        if dow_js in (p.get("dias") or []) and (p.get("avisos") or {}).get("agenda", True) and EST["agenda"].get(uid) != amanha.isoformat():
            envia(uid, p["token"], "Amanhã é dia de pesca 🎣", f"Veja no Maré Boa o melhor ponto e a melhor hora para {DIAS[amanha.weekday()]}.", APP + "#resumo", "agenda")
            EST["agenda"][uid] = amanha.isoformat()

# ---------------- respostas e menções no chat ----------------
grupos = dict(lista("groups"))
novo_desde = NOW.isoformat().replace("+00:00", "Z")
for gid, g in grupos.items():
    q = {"structuredQuery": {"from": [{"collectionId": "posts"}],
                             "where": {"fieldFilter": {"field": {"fieldPath": "criadoEm"}, "op": "GREATER_THAN", "value": {"timestampValue": desde}}},
                             "limit": 100}}
    st, res = http("POST", f"{FS}/groups/{gid}:runQuery", q)
    if st != 200:
        print("posts", gid, st)
        continue
    for r in res:
        d = r.get("document")
        if not d:
            continue
        x = {k: val(v) for k, v in d.get("fields", {}).items()}
        autor, alvo = x.get("uid"), {}
        for u in x.get("mencoes") or []:
            alvo[u] = "mencionou você"
        resp = x.get("resp") or {}
        if resp.get("uid"):
            alvo[resp["uid"]] = "respondeu você"
        for u, acao in alvo.items():
            p = PUSH.get(u)
            if u == autor or not p or not (p.get("avisos") or {}).get("chat", True):
                continue
            envia(u, p["token"], f"{x.get('nome', 'Alguém')} {acao} em {g.get('nome', 'um grupo')}", (x.get("texto") or "")[:140], APP + "#grupos", "chat-" + gid)
EST["chatDesde"] = novo_desde

# guarda o estado
corpo = {"fields": {"frentes": enc(EST["frentes"]), "agenda": enc({k: v for k, v in EST["agenda"].items() if v >= (local.date() - dt.timedelta(days=3)).isoformat()}),
                    "chatDesde": {"timestampValue": EST["chatDesde"]}}}
st, _ = http("PATCH", f"{FS}/sistema/avisos", corpo)
print("estado salvo" if st == 200 else f"estado: {st}")
