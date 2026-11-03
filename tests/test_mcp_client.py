"""The MCP server checked by the official MCP Python SDK client over a real stdio pipe."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def _session(project: Path) -> dict:
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "proven.py"), "mcp", "--no-installed-check"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("proven_check", {"path": str(project)})
        return {"server": init.server_info.name, "tools": [t.name for t in tools.tools],
                "error": result.is_error, "structured": result.structured_content,
                "text": result.content[0].text}


def test_official_client_talks_to_proven(tmp_path):
    (tmp_path / "proven.toml").write_text('[proven]\nsrc = ["src"]\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("MRP = 3932\n", encoding="utf-8")
    out = asyncio.run(_session(tmp_path))
    assert out["server"] == "proven"
    assert out["tools"] == ["proven_check", "proven_explain", "proven_rules"]
    assert out["error"] is False and out["structured"]["accepted"] is False
    assert "MRP" in out["text"]
