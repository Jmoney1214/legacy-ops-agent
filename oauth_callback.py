from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

async def cb(req):
    code  = (req.query_params.get("code") or "").strip()
    state = (req.query_params.get("state") or "").strip()
    Path("oauth_code.txt").write_text(code)
    Path("oauth_state.txt").write_text(state)
    return PlainTextResponse("✅ Authorized. You can close this tab.")
app = Starlette(routes=[Route("/callback", cb)])
