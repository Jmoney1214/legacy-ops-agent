import os, asyncio
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.requests import Request
from starlette.routing import Route
from agents import Agent, Runner, function_tool

# Optional: wire Lightspeed tool if present
try:
    from lightspeed_tools import ls_list_items_flat
except Exception:
    ls_list_items_flat = None

@function_tool
async def health_ping() -> str: return "ok"

tools = [health_ping]
if ls_list_items_flat:
    @function_tool
    async def list_items(limit: int = 10) -> dict:
        return await ls_list_items_flat(limit=limit)
    tools.append(list_items)

agent = Agent(
    name="LegacyOpsAPI",
    instructions="Answer concisely. Use tools precisely.",
    tools=tools,
)

async def health(_): return PlainTextResponse("ok")

async def run_agent(req: Request):
    data = await req.json()
    q = (data.get("input") or "").strip()
    if not q:
        return JSONResponse({"error":"input is required"}, status_code=400)
    r = await Runner.run(agent, q)
    return JSONResponse({"final": r.final_output})

app = Starlette(routes=[
    Route("/health", health, methods=["GET"]),
    Route("/run", run_agent, methods=["POST"]),
])
