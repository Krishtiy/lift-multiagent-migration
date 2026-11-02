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
    run_parser.add_argument("repo_path", type=str, nargs="?", default=None, help="Path to repository to migrate")
    run_parser.add_argument("recipe_pos", type=str, nargs="?", default=None, help="Optional recipe name (e.g. py2_to_py3, express_to_fastapi)")
    run_parser.add_argument("--repo", dest="repo_flag", type=str, default=None, help="Alternative flag for repository path")
    run_parser.add_argument(
        "--recipe",
        type=str,
        default=None,
        choices=["py2_to_py3", "express_to_fastapi"],
        help="Migration recipe to execute (choices: py2_to_py3, express_to_fastapi)",
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
    run_parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Force offline mock LLM provider (costs $0, no API key needed)",
    )

    args = parser.parse_args()

    if args.command == "run":
        repo_arg = args.repo_flag or args.repo_path
        if not repo_arg:
            run_parser.error("Repository path is required. Usage: lift run <path> or lift run --repo <path>")
        target_path = Path(repo_arg).resolve()
        if not target_path.exists():
            print(f"Error: Target path '{repo_arg}' does not exist.")
            sys.exit(1)

        # Interactive recipe selection if not supplied via command line
        recipe = args.recipe or args.recipe_pos
        if recipe and recipe not in ("py2_to_py3", "express_to_fastapi"):
            print(f"Error: Invalid recipe '{recipe}'. Choices are: py2_to_py3, express_to_fastapi")
            sys.exit(1)

        if not recipe:
            if sys.stdin.isatty():
                print("\n" + "=" * 60)
                print(" SELECT MIGRATION RECIPE:")
                print("   [1] Python 2 -> Python 3       (py2_to_py3)")
                print("   [2] Express.js -> FastAPI       (express_to_fastapi)")
                print("=" * 60)
                try:
                    choice = input(" Enter choice [1 or 2] (default: 1): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    sys.exit(0)
                if choice == "2" or choice.lower() in ("express", "fastapi", "express_to_fastapi"):
                    recipe = "express_to_fastapi"
                else:
                    recipe = "py2_to_py3"
            else:
                recipe = "py2_to_py3"

        provider_mode = "Mock Offline Provider (Fake LLM)" if args.fake_llm else "Live Gemini AI"
        print("\n" + "=" * 60)
        print(" LIFT: MULTI-AGENT LEGACY CODE MIGRATION")
        print("=" * 60)
        print(f" Target Repository : {target_path}")
        print(f" Migration Recipe  : {recipe}")
        print(f" Token Budget      : {args.token_budget:,} tokens")
        print(f" LLM Provider      : {provider_mode}")
        print("=" * 60 + "\n")

        from lift.llm.client import UnifiedLLMClient
        engine = create_db_engine(args.db_url)
        llm_client = UnifiedLLMClient(token_budget=args.token_budget, use_fake=args.fake_llm)
        default_model = "mock-model" if args.fake_llm else "gemini-3.8-flash"
        escalation_model = "mock-model-strong" if args.fake_llm else "gemini-3.8-pro"
        orchestrator = MigrationOrchestrator(
            engine=engine,
            llm_client=llm_client,
            default_model=default_model,
            escalation_model=escalation_model,
            verbose=True,
        )

        report: MigrationReport = orchestrator.run_migration(
            repo_path=str(target_path),
            recipe=recipe,
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
            status_symbol = "[PASS]" if summary["status"] == "PASSED" else "[WARN]"
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
