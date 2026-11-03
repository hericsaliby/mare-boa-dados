"""Junta as leituras novas da EPAGRI ao histórico (últimos 45 dias) para a análise harmônica da maré."""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

old_dir, out_dir = sys.argv[1], sys.argv[2]
BRT = timezone(timedelta(hours=-3))
now = datetime.now(BRT)


def quando(s):
    m = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", str(s).strip())
    if not m:
        return None
    d, mo, h, mi = map(int, m.groups())
    y = now.year
    if mo == 12 and now.month == 1:
        y -= 1
    if mo == 1 and now.month == 12:
        y += 1
    return int(datetime(y, mo, d, h, mi, tzinfo=BRT).timestamp() * 1000)


def num(v):
    try:
        return float(str(v).strip())
    except ValueError:
        return None


try:
    hist = json.load(open(os.path.join(old_dir, "historico.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    hist = {}
EST = json.load(open(os.path.join(os.path.dirname(__file__), "estacoes.json"), encoding="utf-8"))["epagri"]
for k in [e["k"] for e in EST if e["id"]]:
    h = {int(t): v for t, v in hist.get(k, {}).get("rows", {}).items()} if isinstance(hist.get(k), dict) else {}
    try:
        dt = json.load(open(os.path.join(out_dir, f"{k}.json"), encoding="utf-8-sig"))
        nmm = None
        for r in dt.get("rows", []):
            c = [x.get("v") if isinstance(x, dict) else x for x in r.get("c", [])]
            t = quando(c[0])
            if t is None:
                continue
            obs, ast = num(c[1]), num(c[2])
            nmm = num(c[6]) if num(c[6]) is not None else nmm
            prev = h.get(t, [None, None])
            h[t] = [obs if obs is not None else prev[0], ast if ast is not None else prev[1]]
        if nmm is not None:
            hist.setdefault(k, {})["nmm"] = nmm
    except Exception as e:  # noqa: BLE001
        print(k, "erro", e)
    limite = (time.time() - 45 * 86400) * 1000
    hist.setdefault(k, {})["rows"] = {str(t): v for t, v in sorted(h.items()) if t >= limite}
    ts = sorted(int(t) for t in hist[k]["rows"])
    if ts:
        print(k, len(ts), "linhas,", round((ts[-1] - ts[0]) / 86400000, 1), "dias")
with open(os.path.join(out_dir, "historico.json"), "w", encoding="utf-8") as f:
    json.dump(hist, f, separators=(",", ":"))
