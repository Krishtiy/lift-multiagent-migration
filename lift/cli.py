"""Command-line interface for the Lift migration platform."""

import argparse
import sys
from pathlib import Path

from lift.core.orchestrator import MigrationOrchestrator, MigrationReport
from lift.db.session import create_db_engine


def main():
    parser = argparse.ArgumentParser(
        prog="lift",
        description="Lift: Deterministic Multi-Agent Legacy Code Migration System",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run migration on a repository")
    run_parser.add_argument("repo_path", type=str, help="Path to repository to migrate")
    run_parser.add_argument(
        "--recipe",
        type=str,
        default="py2_to_py3",
        choices=["py2_to_py3", "express_to_fastapi"],
        help="Migration recipe to execute (default: py2_to_py3)",
    )
    run_parser.add_argument(
        "--db-url",
        type=str,
        default="sqlite:///:memory:",
        help="Database connection URL (default: in-memory sqlite)",
    )
    run_parser.add_argument(
        "--token-budget",
        type=int,
        default=500_000,
        help="Total token budget for migration job (default: 500,000)",
    )

    args = parser.parse_args()

    if args.command == "run":
        target_path = Path(args.repo_path).resolve()
        if not target_path.exists():
            print(f"Error: Target path '{args.repo_path}' does not exist.")
            sys.exit(1)

        print("\n" + "=" * 60)
        print(" LIFT: MULTI-AGENT LEGACY CODE MIGRATION")
        print("=" * 60)
        print(f" Target Repository : {target_path}")
        print(f" Migration Recipe  : {args.recipe}")
        print(f" Token Budget      : {args.token_budget:,} tokens")
        print("=" * 60 + "\n")

        engine = create_db_engine(args.db_url)
        orchestrator = MigrationOrchestrator(engine=engine)

        report: MigrationReport = orchestrator.run_migration(
            repo_path=str(target_path),
            recipe=args.recipe,
            token_budget=args.token_budget,
        )

        print("\n" + "=" * 60)
        print(" MIGRATION EXECUTION SUMMARY (MILESTONE 1)")
        print("=" * 60)
        print(f" Job ID           : {report.job_id}")
        print(f" Total Chunks     : {report.total_chunks}")
        print(f" Passed (100%)    : {report.passed_chunks}")
        print(f" Needs Review     : {report.review_chunks}")
        print(f" Failed           : {report.failed_chunks}")
        print(f" Tokens Consumed  : {report.tokens_used:,}")
        print(f" Total LLM Cost   : ${report.cost_usd:.4f}")
        print(f" Wall Duration    : {report.duration_seconds}s")
        print("=" * 60)

        print("\nChunk Breakdown:")
        for summary in report.chunk_summaries:
            status_symbol = "✔" if summary["status"] == "PASSED" else "⚠"
            attempts_info = f"(Attempts: {summary.get('attempts', 1)})"
            reason_info = f"[Reason: {summary.get('reason')}]" if "reason" in summary else ""
            print(f"  {status_symbol} {summary['file_path']:<35} : {summary['status']} {attempts_info} {reason_info}")

        print("\n")
        if report.review_chunks > 0 or report.failed_chunks > 0:
            sys.exit(1)
        sys.exit(0)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
