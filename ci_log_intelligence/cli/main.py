from __future__ import annotations

import argparse
import json
import sys

from ..ci_analysis import analyze_ci_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-log-intel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--url", required=True)
    analyze_parser.add_argument("--include-passed", action="store_true")
    analyze_parser.add_argument("--max-passed-runs", type=int, default=3)
    analyze_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "analyze":
        parser.error(f"Unsupported command: {args.command}")

    report = analyze_ci_url(
        args.url,
        include_passed=args.include_passed,
        max_passed_runs=args.max_passed_runs,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"Root cause: {report.root_cause.summary}")
    if report.root_cause.log_excerpt:
        print()
        print("Log excerpt:")
        print(report.root_cause.log_excerpt)

    if report.failures:
        print()
        print("Failures:")
        for failure in report.failures:
            print(f"- [{failure.type}/{failure.classification}] {failure.summary}")
            if failure.extracted_fields:
                kv = ", ".join(
                    f"{k}={v}" for k, v in sorted(failure.extracted_fields.items())
                )
                print(f"    {kv}")

    if report.passed_context:
        print()
        print("Passed context:")
        for context in report.passed_context:
            print(f"- {context.job_name}")
            print(context.excerpt)

    if report.cross_run_insights:
        print()
        print("Cross-run insights:")
        for insight in report.cross_run_insights:
            print(f"- {insight}")

    print()
    print(
        "Metadata: "
        f"runs={report.metadata.total_runs_analyzed} "
        f"failed_runs={report.metadata.failed_runs} "
        f"passed_runs={report.metadata.passed_runs}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
