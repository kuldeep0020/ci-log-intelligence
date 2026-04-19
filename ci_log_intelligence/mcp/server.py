from __future__ import annotations

import argparse

from fastmcp import FastMCP

from ..ci_analysis import analyze_ci_url

server = FastMCP("ci-log-intelligence")


@server.tool(name="analyze_ci_failure")
def analyze_ci_failure(ci_url: str) -> dict[str, object]:
    report = analyze_ci_url(
        ci_url,
        include_passed=True,
        max_passed_runs=1,
        max_runs=3,
    )
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ci_log_intelligence.mcp.server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http",
            host=args.host,
            port=args.port,
            show_banner=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
