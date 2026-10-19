from contextlib import asynccontextmanager
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT, settings, validate_as_of


class MCPBridge:
    def __init__(self, session):
        self.session = session

    async def call(self, name, arguments, as_of):
        result = await self.session.call_tool(name, {**arguments, "as_of": validate_as_of(as_of)})
        if result.isError:
            raise ValueError("MCP 工具失败：" + " ".join(x.text for x in result.content if hasattr(x, "text"))[:500])
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured
        text = "\n".join(x.text for x in result.content if hasattr(x, "text"))
        return json.loads(text)


@asynccontextmanager
async def connect_mcp():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["FIN_DATA_PROCESS"] = "1"
    env["DATABASE_URL"] = settings.database_url
    env["FIN_DATABASE_SCHEMA"] = settings.database_schema
    env["FIN_DATA_DIR"] = str(settings.data_dir)
    # The data MCP process does not need the LLM API key.
    env.pop("DASHSCOPE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", "-m", "finagent.mcp_server"],
                                   env=env, cwd=str(ROOT))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            yield MCPBridge(session)
