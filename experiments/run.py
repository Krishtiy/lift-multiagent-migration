"""CLI entry point to execute Lift migration experiments E0 through E4.

Usage:
    python -m experiments.run --config experiments/configs/e0_baselines.yaml --runs 3
    python -m experiments.run --all --fake-llm
"""

import argparse
from datetime import datetime
import glob
from pathlib import Path
import sys

from experiments.harness import ExperimentConfig, ExperimentHarness


def parse_args():
    parser = argparse.ArgumentParser(description="Lift Benchmark Experiment Runner")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to experiment YAML configuration file",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all experiment configurations in experiments/configs/",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Override number of runs per condition",
    )
    parser.add_argument(
        "--benchmark",
        type=str,
        default="benchmarks/py2_app",
        help="Target benchmark repository path",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="experiments/results",
        help="Output directory for CSV and markdown results",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Use deterministic mock LLM provider for offline testing",
    )
    return parser.parse_args()


def run_single_config(config_path: str, args, harness: ExperimentHarness) -> None:
    config = ExperimentConfig.from_yaml(config_path)
    runs = args.runs if args.runs is not None else config.runs

    print(f"\n=======================================================")
    print(f"Executing: {config.name}")
    print(f"Description: {config.description}")
    print(f"Conditions: {[c.name for c in config.conditions]}")
    print(f"Runs per condition: {runs}")
    print(f"Benchmark: {args.benchmark}")
    print(f"=======================================================\n")

    metrics = harness.run_experiment(config, runs_override=runs)
    aggregates = harness.aggregate_metrics(metrics)

    print("\n--- Experiment Results Summary ---")
    markdown_table = harness.render_markdown_table(aggregates)
    print(markdown_table)
    print("")

    # Export results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(config_path).stem
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{stem}_{timestamp}.csv"
    md_path = out_dir / f"{stem}_{timestamp}.md"

    harness.export_csv(metrics, str(csv_path))
    md_content = f"# Experiment Report: {config.name}\n\n"
    md_content += f"**Timestamp**: {datetime.now().isoformat()}\n"
    md_content += f"**Description**: {config.description}\n\n"
    md_content += "## Summary Table\n\n"
    md_content += markdown_table + "\n"

    md_path.write_text(md_content, encoding="utf-8")
    print(f"Saved CSV export: {csv_path}")
    print(f"Saved Markdown report: {md_path}\n")


def main():
    args = parse_args()
    configs_to_run = []

    if args.all:
        pattern = str(Path("experiments/configs/*.yaml"))
        configs_to_run = sorted(glob.glob(pattern))
        if not configs_to_run:
            print(f"No configuration files found in experiments/configs/*.yaml")
            sys.exit(1)
    elif args.config:
        configs_to_run = [args.config]
    else:
        # Default to e0_baselines.yaml
        default_cfg = "experiments/configs/e0_baselines.yaml"
        if Path(default_cfg).exists():
            configs_to_run = [default_cfg]
        else:
            print("Error: Specify --config <path> or --all")
            sys.exit(1)

    harness = ExperimentHarness(
        benchmark_repo=args.benchmark,
        fake_llm=args.fake_llm,
    )

    for cfg_path in configs_to_run:
        run_single_config(cfg_path, args, harness)


if __name__ == "__main__":
    main()
