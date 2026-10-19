"""Optional real-provider connectivity check. Reads only the project's .env."""
import asyncio
from finagent.llm import QwenClient

async def main():
    result = await QwenClient().complete([{"role":"user","content":"仅回复：连接正常"}])
    print(result["message"].get("content",""))
    print("usage:", result["usage"])

if __name__ == "__main__":
    asyncio.run(main())
