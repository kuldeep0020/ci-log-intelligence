from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import analyze_log
from ..ci_analysis import analyze_ci_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-log-intel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze")
    source = analyze_parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--url",
        help="GitHub PR / workflow-run / job URL to fetch and analyze.",
    )
    source.add_argument(
        "--file",
        help="Path to a log file to analyze locally. Use '-' to read from stdin.",
    )
    analyze_parser.add_argument("--include-passed", action="store_true")
    analyze_parser.add_argument("--max-passed-runs", type=int, default=3)
    analyze_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "analyze":
        parser.error(f"Unsupported command: {args.command}")

    if args.file is not None:
        return _run_analyze_file(args)
    return _run_analyze_url(args)


def _run_analyze_url(args: argparse.Namespace) -> int:
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


def _run_analyze_file(args: argparse.Namespace) -> int:
    log = _read_log_source(args.file)
    result = analyze_log(log)

    if args.json:
        print(json.dumps(_result_to_json(result), indent=2, sort_keys=True, default=str))
        return 0

    if result.summary:
        print(f"Summary: {result.summary}")

    if result.blocks:
        print()
        print("Top failure blocks:")
        for index, scored in enumerate(result.blocks):
            block = scored.block
            print(
                f"- [{scored.classification}] block {index} "
                f"lines {block.start_line}-{block.end_line} "
                f"(score: {scored.score:.2f})"
            )
            excerpt = _format_excerpt(block.lines, max_lines=10)
            if excerpt:
                print(excerpt)

    if result.detected_failures:
        print()
        print("Detected failures:")
        for failure in result.detected_failures:
            anchors = ",".join(str(line) for line in failure.anchor_lines)
            print(
                f"- [{failure.type}] severity={failure.severity} "
                f"anchors=[{anchors}]"
            )
            if failure.extracted_fields:
                kv = ", ".join(
                    f"{k}={v}" for k, v in sorted(failure.extracted_fields.items())
                )
                print(f"    {kv}")

    print()
    print(
        "Metadata: "
        f"blocks={len(result.blocks)} "
        f"detected_failures={len(result.detected_failures)}"
    )
    return 0


def _read_log_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _format_excerpt(lines: list, max_lines: int) -> str:
    body = lines[:max_lines]
    if not body:
        return ""
    out = "\n".join(f"    {line.line_number:>6}  {line.content}" for line in body)
    if len(lines) > max_lines:
        out += f"\n    ... ({len(lines) - max_lines} more lines)"
    return out


def _result_to_json(result) -> dict[str, Any]:
    return {
        "summary": result.summary,
        "blocks": [_scored_block_to_json(block) for block in result.blocks],
        "detected_failures": [
            _detected_failure_to_json(failure) for failure in result.detected_failures
        ],
    }


def _scored_block_to_json(scored) -> dict[str, Any]:
    return {
        "classification": scored.classification,
        "score": scored.score,
        "score_components": scored.score_components.to_dict(),
        "start_line": scored.block.start_line,
        "end_line": scored.block.end_line,
        "lines": [_parsed_line_to_json(line) for line in scored.block.lines],
        "anchors": [dataclasses.asdict(anchor) for anchor in scored.block.anchors],
    }


def _parsed_line_to_json(line) -> dict[str, Any]:
    data = dataclasses.asdict(line)
    if isinstance(data.get("timestamp"), datetime):
        data["timestamp"] = data["timestamp"].isoformat()
    return data


def _detected_failure_to_json(failure) -> dict[str, Any]:
    return {
        "type": failure.type,
        "anchor_lines": list(failure.anchor_lines),
        "severity": failure.severity,
        "classification_claim": failure.classification_claim,
        "extracted_fields": dict(failure.extracted_fields),
        "suggested_block_range": (
            list(failure.suggested_block_range)
            if failure.suggested_block_range is not None
            else None
        ),
        "anchor_type": failure.anchor_type,
    }


if __name__ == "__main__":
    sys.exit(main())
