from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(executable: Path, python_module: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="researchbrain-mcp-smoke-") as data_dir:
        environment = dict(os.environ)
        environment["RESEARCHBRAIN_DATA_DIR"] = data_dir
        parameters = StdioServerParameters(
            command=str(executable),
            args=["-m", "researchbrain.mcp_server"] if python_module else ["mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                libraries = await session.call_tool("list_libraries", {})
                return {
                    "tools": sorted(tool.name for tool in tools.tools),
                    "list_libraries_is_error": bool(libraries.isError),
                    "data_dir": data_dir,
                }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument(
        "--python-module",
        action="store_true",
        help="Run the MCP server as python -m researchbrain.mcp_server.",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.executable, args.python_module)), ensure_ascii=False))


if __name__ == "__main__":
    main()
