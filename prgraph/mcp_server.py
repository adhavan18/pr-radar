"""MCP server (stdio) exposing the PR Radar graph tools.

Run:  python -m prgraph.mcp_server   (env: FALKORDB_HOST/PORT, PRGRAPH_REPO = default owner/repo)

Claude Code:
  claude mcp add pr-radar -e FALKORDB_HOST=localhost -e PRGRAPH_REPO=sqlfluff/sqlfluff \
      -- python -m prgraph.mcp_server
Claude Desktop (claude_desktop_config.json):
  {"mcpServers": {"pr-radar": {"command": "python", "args": ["-m", "prgraph.mcp_server"],
     "cwd": "/path/to/prgraph",
     "env": {"FALKORDB_HOST": "localhost", "PRGRAPH_REPO": "sqlfluff/sqlfluff"}}}}
Every tool also takes an optional `repo` overriding PRGRAPH_REPO.
"""
import json
import os

from .agent.tools import anthropic_tools, call_tool

_graph = None


def _g():
    global _graph
    if _graph is None:
        from .db import connect
        _graph = connect()
    return _graph


def tool_specs() -> list[dict]:
    specs = anthropic_tools()
    for s in specs:
        s["input_schema"]["properties"]["repo"] = {"type": "string", "description": "owner/repo"}
    return specs


def handle(name: str, args: dict, graph=None) -> str:
    args = dict(args or {})
    repo = args.pop("repo", None) or os.environ.get("PRGRAPH_REPO")
    rows = call_tool(graph or _g(), name, args, repo, session=os.environ.get("PRGRAPH_SESSION", "mcp"))
    return json.dumps(rows, default=str, indent=1)


def build_server():
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("pr-radar")
    specs = tool_specs()

    @server.list_tools()
    async def _list():
        return [Tool(name=s["name"], description=s["description"], inputSchema=s["input_schema"])
                for s in specs]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        return [TextContent(type="text", text=handle(name, arguments))]

    return server


def main():
    import anyio
    from mcp.server.stdio import stdio_server

    async def run():
        server = build_server()
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
