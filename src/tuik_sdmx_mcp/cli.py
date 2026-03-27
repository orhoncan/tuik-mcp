"""CLI entry point for TÜİK SDMX MCP server."""

import argparse
import sys

from tuik_sdmx_mcp.server import mcp


def main():
    parser = argparse.ArgumentParser(description="TÜİK SDMX MCP Server")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the MCP server")
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
    )

    version_parser = subparsers.add_parser("version", help="Print version")

    args = parser.parse_args()

    if args.command == "version":
        from tuik_sdmx_mcp._version import __version__

        print(f"tuik-sdmx-mcp v{__version__}")
        sys.exit(0)

    # Default to serve
    transport = getattr(args, "transport", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
