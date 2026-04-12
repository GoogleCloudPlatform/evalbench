import argparse
import asyncio
import json
import logging
import sys
import yaml

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)


async def run_server(args):
    try:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load config {args.config}: {e}")
        sys.exit(1)

    server_config = config.get("fake_mcp_tools", {}).get(args.server_name, [])
    if not server_config:
        logger.warning(
            f"No fake tools found for server '{args.server_name}' in '{args.config}'")

    tools = []
    for t in server_config:
        tools.append(Tool(
            name=t["name"],
            description=t.get("description", ""),
            inputSchema=t.get(
                "parameters", {"type": "object", "properties": {}})
        ))

    app = Server(args.server_name)

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        tool_config = next((t for t in server_config if t["name"] == name), None)
        if not tool_config:
            raise ValueError(f"Tool {name} not found")

        if "response" in tool_config:
            result = tool_config["response"]
        else:
            result = {
                "status": "success",
                "tool": name,
                "args": arguments
            }
        return [
            TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )
        ]

    # Run the server on stdio
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description="Fake MCP Server")
    parser.add_argument("--config", required=True, help="Path to config yaml")
    parser.add_argument("--server-name", required=True,
                        help="Server name in config")
    args = parser.parse_args()

    asyncio.run(run_server(args))


if __name__ == "__main__":
    main()
