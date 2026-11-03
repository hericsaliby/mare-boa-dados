"""Lê o segredo FIREBASE_SA (JSON da conta de serviço) aceitando os jeitos mais comuns de colar:
JSON normal, JSON com quebras de linha reais dentro da chave privada, entre aspas ou crases, ou em base64.
Se não conseguir, mostra só o diagnóstico (tamanho e formato), nunca o conteúdo."""
import base64
import json


def le_conta(s):
    s = (s or "").strip().strip("`").strip()
    tent = [s]
    if len(s) > 1 and s[0] == s[-1] and s[0] in "'\"":
        tent.append(s[1:-1])
    try:
        tent.append(base64.b64decode(s, validate=False).decode("utf-8", "ignore"))
    except Exception:
        pass
    for t in tent:
        for strict in (True, False):
            try:
                j = json.loads(t, strict=strict)
                if isinstance(j, str):
                    j = json.loads(j, strict=False)
                if isinstance(j, dict) and j.get("private_key") and j.get("client_email"):
                    j["private_key"] = j["private_key"].replace("\\n", "\n")
                    return j
            except Exception:
                continue
    raise SystemExit(f"FIREBASE_SA inválido: {len(s)} caracteres, começa com {s[:1]!r}, termina com {s[-1:]!r}, "
                     f"{s.count(chr(10)) + 1} linhas, contém 'private_key': {'private_key' in s}, contém 'client_email': {'client_email' in s}")
