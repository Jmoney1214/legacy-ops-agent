import bootstrap_secrets
import asyncio
from agents import Agent, Runner, function_tool
from agents import SQLiteSession

@function_tool
def gross_margin(sell_price: float, cost: float) -> dict:
    m = sell_price - cost
    return {"margin": round(m, 2), "margin_pct": round((m/sell_price)*100, 2)}

agent = Agent(
    name="LegacyOps",
    instructions="Be concise. Use tools precisely.",
    tools=[gross_margin],
)

async def main():
    sess = SQLiteSession("legacy:main", db_path="agent_sessions.db")
    r1 = await Runner.run(agent, "If cost is 22.50 and price is 39.99, what's the margin?", session=sess)
    print("turn1:", r1.final_output)
    r2 = await Runner.run(agent, "Now give only the margin percent.", session=sess)
    print("turn2:", r2.final_output)

if __name__ == "__main__":
    asyncio.run(main())
