import asyncio
from agents import Agent, Runner, function_tool
from lightspeed_tools import ls_list_items_flat

@function_tool
async def list_items_flat(limit: int = 25, shop_id: str | None = None) -> dict:
    """Return flattened items (systemSku/price/qoh) for a shop using the no-filter Item.json endpoint."""
    return await ls_list_items_flat(limit=limit, shop_id=shop_id)

agent = Agent(
    name="LightspeedOnly",
    instructions=(
        "Be concise. Use tools precisely. For inventory/price/QOH questions, call list_items_flat. "
        "Report systemSku, price, qoh, description."
    ),
    tools=[list_items_flat],
)

async def main():
    r = await Runner.run(agent, "List 10 items with systemSku, price, qoh for my shop.")
    print(r.final_output)

if __name__ == "__main__":
    asyncio.run(main())
