"""Experiment harness executing benchmark migrations across varying conditions.

Evaluates:
- First-try pass %
- Pass after retries %
- Human review %
- Regressions %
- Dev suite pass rate
- Held-out oracle pass rate
- Planted gotchas caught & resolved (out of 12)
- Token consumption and dollar cost
- Wall-clock execution time
"""

import csv
import dataclasses
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import sys
import time
from typing import Any, Dict, List, Optional

import yaml

from lift.core.orchestrator import MigrationOrchestrator, MigrationReport
from lift.db.models import ChunkStatus
from lift.llm.cache import LLMCache
from lift.llm.client import FakeLLMProvider, UnifiedLLMClient
from lift.sandbox.runner import SandboxLimits, create_sandbox_runner


@dataclass
class ConditionConfig:
    name: str
    apply_rules: bool = True
    max_attempts: int = 3
    skip_verifier: bool = False
    role_mode: str = "separated"
    default_model: str = "gemini-2.5-flash"
    escalation_model: str = "gemini-2.5-pro"
    enable_escalation: bool = True


@dataclass
class ExperimentConfig:
    name: str
    description: str
    runs: int
    conditions: List[ConditionConfig]

    @classmethod
    def from_yaml(cls, path: str) -> "ExperimentConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        conditions = [ConditionConfig(**c) for c in data.get("conditions", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            runs=int(data.get("runs", 1)),
            conditions=conditions,
        )


@dataclass
class RunMetric:
    condition_name: str
    run_index: int
    total_chunks: int
    first_try_passed: int
    retried_passed: int
    needs_review: int
    failed: int
    first_try_pct: float
    retried_pct: float
    review_pct: float
    dev_suite_passed: bool
    heldout_suite_passed: bool
    gotchas_resolved: int
    total_gotchas: int = 12
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class AggregateMetric:
    condition_name: str
    total_runs: int
    mean_first_try_pct: float
    mean_retried_pct: float
    mean_review_pct: float
    dev_pass_rate_pct: float
    heldout_pass_rate_pct: float
    mean_gotchas_resolved: float
    mean_tokens: float
    mean_cost_usd: float
    mean_duration_seconds: float


class GotchaAuditor:
    """Inspects migrated codebase for the 12 cataloged Python 2 gotchas."""

    @classmethod
    def audit_repo(cls, repo_root: str) -> Dict[str, bool]:
        root = Path(repo_root)
        py_files = list(root.glob("**/*.py"))
        contents = {}
        for p in py_files:
            try:
                contents[p.name] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                contents[p.name] = ""

        all_text = "\n".join(contents.values())

        results = {}
        # G-01: print statement without parens
        results["G-01"] = not bool(re.search(r"^\s*print\s+[^(]", all_text, re.MULTILINE))

        # G-02: except Exception, e
        results["G-02"] = not bool(re.search(r"except\s+\w+\s*,\s*\w+:", all_text))

        # G-03: xrange
        results["G-03"] = not bool(re.search(r"\bxrange\b", all_text))

        # G-04: dict.has_key
        results["G-04"] = not bool(re.search(r"\.has_key\(", all_text))

        # G-05: integer division: check if integer_average uses // or int()
        math_content = contents.get("math_stats.py", "")
        results["G-05"] = bool(
            "//" in math_content or "int(" in math_content
        ) and "total / len" not in math_content

        # G-06: map/filter indexing
        results["G-06"] = "list(map(" in all_text or "list(filter(" in all_text or "map(" not in all_text

        # G-07: iteritems
        results["G-07"] = not bool(re.search(r"\.iteritems\(", all_text))

        # G-08: long literal or type
        results["G-08"] = not bool(re.search(r"\b\d+L\b|\blong\(", all_text))

        # G-09: __nonzero__
        results["G-09"] = "__bool__" in all_text and "__nonzero__" not in all_text

        # G-10: safe_decode handling bytes and str
        text_content = contents.get("text_utils.py", "")
        results["G-10"] = "isinstance(" in text_content and ("bytes" in text_content or "decode" in text_content)

        # G-11: custom sorting with cmp replaced by key
        results["G-11"] = "cmp=" not in math_content and "key=" in math_content

        # G-12: legacy modules (StringIO / urllib2)
        results["G-12"] = "import StringIO" not in all_text and "import urllib2" not in all_text

        return results


class ExperimentHarness:
    """Runs repeatable experiments and produces standardized benchmarks."""

    def __init__(
        self,
        benchmark_repo: str = "benchmarks/py2_app",
        fake_llm: bool = False,
        cache_dir: Optional[str] = None,
    ):
        self.benchmark_repo = Path(benchmark_repo).resolve()
        self.fake_llm = fake_llm
        self.cache_dir = cache_dir
        self.sandbox = create_sandbox_runner()

    def run_experiment(
        self,
        config: ExperimentConfig,
        runs_override: Optional[int] = None,
    ) -> List[RunMetric]:
        runs_count = runs_override if runs_override is not None else config.runs
        all_metrics: List[RunMetric] = []

        for condition in config.conditions:
            for run_idx in range(1, runs_count + 1):
                metric = self._execute_single_run(condition, run_idx)
                all_metrics.append(metric)

        return all_metrics

    def _execute_single_run(
        self,
        condition: ConditionConfig,
        run_idx: int,
    ) -> RunMetric:
        # Create an isolated temporary directory for the benchmark repo
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir) / "app"
            shutil.copytree(str(self.benchmark_repo), str(temp_repo))

            # Set up LLM client
            cache_path = self.cache_dir or os.path.join(temp_dir, ".llm_cache")
            llm_cache = LLMCache(cache_dir=cache_path)
            llm_client = UnifiedLLMClient(
                token_budget=1_000_000,
                cache=llm_cache,
            )
            if self.fake_llm:
                # Pre-seed fake provider responses
                provider = FakeLLMProvider()
                self._configure_fake_llm(provider)
                llm_client.provider = provider

            orchestrator = MigrationOrchestrator(
                llm_client=llm_client,
                default_model=condition.default_model,
                escalation_model=condition.escalation_model,
                max_attempts=condition.max_attempts,
                apply_rules_pass=condition.apply_rules,
                skip_verifier=condition.skip_verifier,
                role_mode=condition.role_mode,
                enable_escalation=condition.enable_escalation,
            )

            report = orchestrator.run_migration(str(temp_repo))

            # Evaluate chunks
            total_chunks = report.total_chunks or 1
            first_try = sum(
                1 for s in report.chunk_summaries
                if s.get("status") == ChunkStatus.PASSED.value and s.get("attempts", 0) <= 1
            )
            retried = sum(
                1 for s in report.chunk_summaries
                if s.get("status") == ChunkStatus.PASSED.value and s.get("attempts", 0) > 1
            )
            needs_review = report.review_chunks
            failed = report.failed_chunks

            # Run test suites inside sandbox
            dev_passed = self._run_test_suite(temp_repo, "tests_dev/test_dev_suite.py")
            heldout_passed = self._run_test_suite(temp_repo, "tests_heldout/test_eval_suite.py")

            # Audit planted gotchas
            gotcha_audit = GotchaAuditor.audit_repo(str(temp_repo))
            gotchas_resolved = sum(1 for v in gotcha_audit.values() if v)

            return RunMetric(
                condition_name=condition.name,
                run_index=run_idx,
                total_chunks=total_chunks,
                first_try_passed=first_try,
                retried_passed=retried,
                needs_review=needs_review,
                failed=failed,
                first_try_pct=round((first_try / total_chunks) * 100.0, 1),
                retried_pct=round(((first_try + retried) / total_chunks) * 100.0, 1),
                review_pct=round((needs_review / total_chunks) * 100.0, 1),
                dev_suite_passed=dev_passed,
                heldout_suite_passed=heldout_passed,
                gotchas_resolved=gotchas_resolved,
                total_gotchas=len(gotcha_audit),
                tokens_used=report.tokens_used,
                cost_usd=round(report.cost_usd, 4),
                duration_seconds=report.duration_seconds,
            )

    def _run_test_suite(self, repo_path: Path, test_rel_path: str) -> bool:
        test_file = repo_path / test_rel_path
        if not test_file.exists():
            return False

        module_name = test_rel_path.replace("/", ".").replace("\\", ".").replace(".py", "")
        limits = SandboxLimits(timeout_seconds=10.0)
        exec_res = self.sandbox.run(
            image="local",
            cmd=[sys.executable, "-m", "unittest", module_name],
            mounts={str(repo_path): {"bind": "/workspace", "mode": "rw"}},
            working_dir=str(repo_path),
            limits=limits,
        )
        return exec_res.exit_code == 0

    def _configure_fake_llm(self, provider: FakeLLMProvider) -> None:
        """Configures intelligent mock responses for reproducible offline tests."""
        modernized_text_utils = """# -*- coding: utf-8 -*-
import hashlib
import io

def render_banner(title):
    border = "=" * (len(title) + 8)
    return f"{border}\\n[   {title}   ]\\n{border}"

def hash_text(content, salt=""):
    return hashlib.md5((content + salt).encode("utf-8")).hexdigest()

def format_error_message(err):
    try:
        raise ValueError(err)
    except ValueError as e:
        return f"Captured: {e}"

def safe_decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
"""

        modernized_math_stats = """# -*- coding: utf-8 -*-
from functools import cmp_to_key

def integer_average(numbers):
    if not numbers:
        return 0
    return sum(numbers) // len(numbers)

def generate_series(start, count, step=1):
    return list(range(start, start + count * step, step))

def parse_large_id(id_str):
    return int(id_str)

def sort_records_mixed(records):
    def sort_key(item):
        if item is None:
            return (0, "")
        if isinstance(item, (int, float)):
            return (1, item)
        return (2, str(item))
    return sorted(records, key=sort_key)

def custom_compare_sort(items):
    def compare_desc(a, b):
        if a < b:
            return 1
        elif a > b:
            return -1
        return 0
    return sorted(items, key=cmp_to_key(compare_desc))
"""

        modernized_data_store = """# -*- coding: utf-8 -*-
import io

class InventoryStatus:
    def __init__(self, count):
        self.count = count

    def __bool__(self):
        return self.count > 0

    def __nonzero__(self):
        return self.__bool__()

class ProductCatalog:
    def __init__(self):
        self.inventory = {}
        self.prices = {}

    def add_product(self, sku, name, price, stock=0):
        self.inventory[sku] = (name, InventoryStatus(stock))
        self.prices[sku] = price

    def has_product(self, sku):
        return sku in self.inventory

    def get_all_skus(self):
        return sorted(list(self.inventory.keys()))

    def get_active_items(self):
        active = []
        for sku, (name, status) in self.inventory.items():
            if status:
                active.append((sku, name))
        return sorted(active)

    def filter_by_max_price(self, max_price):
        for sku, price in self.prices.items():
            if price <= max_price:
                return sku
        return None
"""

        responses = []
        for code in [modernized_text_utils, modernized_math_stats, modernized_data_store]:
            resp = json.dumps({
                "migrated_code": code,
                "changes_explained": ["Modernized Python 2 patterns to Python 3"],
                "flagged_for_review": False,
                "review_note": None,
            })
            responses.append(resp)
        provider.canned_responses = responses * 20

    @classmethod
    def aggregate_metrics(cls, metrics: List[RunMetric]) -> List[AggregateMetric]:
        by_cond: Dict[str, List[RunMetric]] = {}
        for m in metrics:
            by_cond.setdefault(m.condition_name, []).append(m)

        aggregates = []
        for cond_name, runs in by_cond.items():
            n = len(runs)
            aggregates.append(
                AggregateMetric(
                    condition_name=cond_name,
                    total_runs=n,
                    mean_first_try_pct=round(sum(r.first_try_pct for r in runs) / n, 1),
                    mean_retried_pct=round(sum(r.retried_pct for r in runs) / n, 1),
                    mean_review_pct=round(sum(r.review_pct for r in runs) / n, 1),
                    dev_pass_rate_pct=round(sum(100.0 for r in runs if r.dev_suite_passed) / n, 1),
                    heldout_pass_rate_pct=round(sum(100.0 for r in runs if r.heldout_suite_passed) / n, 1),
                    mean_gotchas_resolved=round(sum(r.gotchas_resolved for r in runs) / n, 1),
                    mean_tokens=round(sum(r.tokens_used for r in runs) / n, 0),
                    mean_cost_usd=round(sum(r.cost_usd for r in runs) / n, 4),
                    mean_duration_seconds=round(sum(r.duration_seconds for r in runs) / n, 2),
                )
            )
        return aggregates

    @classmethod
    def render_markdown_table(cls, aggregates: List[AggregateMetric]) -> str:
        lines = [
            "| Condition | Runs | First-Try Pass % | Overall Pass % | Human Review % | Dev Suite % | Held-Out % | Gotchas (/12) | Mean Tokens | Cost ($) | Duration (s) |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for a in aggregates:
            lines.append(
                f"| **{a.condition_name}** | {a.total_runs} | {a.mean_first_try_pct}% | {a.mean_retried_pct}% | "
                f"{a.mean_review_pct}% | {a.dev_pass_rate_pct}% | {a.heldout_pass_rate_pct}% | "
                f"{a.mean_gotchas_resolved} | {int(a.mean_tokens):,} | ${a.mean_cost_usd:.4f} | {a.mean_duration_seconds:.2f}s |"
            )
        return "\n".join(lines)

    @classmethod
    def export_csv(cls, metrics: List[RunMetric], out_path: str) -> None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[f.name for f in dataclasses.fields(RunMetric)])
            writer.writeheader()
            for m in metrics:
                writer.writerow(asdict(m))
