import asyncio
from agents import Agent, Runner, function_tool

@function_tool
def ping(target: str) -> str:
    return f"pong:{target}"

agent = Agent(
    name="LegacyOps",
    instructions="Return exactly: ok when asked for health. Otherwise answer briefly.",
    tools=[ping],
)

async def main():
    res = await Runner.run(agent, "health")
    print(res.final_output)

if __name__ == "__main__":
    asyncio.run(main())
