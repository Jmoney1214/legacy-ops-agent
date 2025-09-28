from __future__ import annotations
import os, json, time, urllib.request, urllib.parse, sys
TOKENS_PATH = os.getenv("LS_TOKENS_PATH","tokens.json")
ENDPOINTS = [
  "https://cloud.merchantos.com/oauth/access_token.php",
  "https://cloud.lightspeedapp.com/oauth/access_token.php",
]
def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type","application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode())
def _load(): 
    try: return json.load(open(TOKENS_PATH))
    except: return {}
def _save(tok):
    now=int(time.time())
    if "expires_in" in tok and "expires_at" not in tok: tok["expires_at"]=now+int(tok["expires_in"])
    json.dump(tok, open(TOKENS_PATH,"w"), indent=2)
def refresh(refresh_token: str):
    cid=os.environ["LS_CLIENT_ID"]; sec=os.environ["LS_CLIENT_SECRET"]
    last=None
    for url in ENDPOINTS:
        try:
            tok=_post(url, {"client_id":cid,"client_secret":sec,"refresh_token":refresh_token,"grant_type":"refresh_token"})
            if "refresh_token" not in tok: tok["refresh_token"]=refresh_token
            _save(tok); return tok
        except Exception as e: last=e
    raise RuntimeError(f"refresh failed: {last}")
def ensure_access_token(min_ttl=120) -> str:
    tok=_load(); now=int(time.time())
    if tok and tok.get("access_token") and (int(tok.get("expires_at",0))-now>min_ttl): return tok["access_token"]
    rt = os.getenv("LS_REFRESH_TOKEN") or tok.get("refresh_token")
    if not rt: raise RuntimeError("No LS_REFRESH_TOKEN available")
    return refresh(rt)["access_token"]
