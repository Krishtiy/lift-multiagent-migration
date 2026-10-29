"""Deterministic Verifier agent that checks syntax, executes characterization tests, and catches regressions."""

import ast
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from lift.agents.recorder import (
    CharacterizationRecorder,
    ComparisonResult,
    GoldenComparator,
    GoldenExecution,
    GoldenSuite,
    TestCase,
)
from lift.sandbox.runner import BaseSandboxRunner, create_sandbox_runner


@dataclass
class VerificationResult:
    """Outcome of deterministic candidate verification."""
    passed: bool
    has_regressions: bool = False
    syntax_valid: bool = True
    syntax_error: Optional[str] = None
    failing_tests: List[str] = field(default_factory=list)
    trimmed_report: str = ""
    triage_category: Optional[str] = None  # syntax, semantic, regression, environment
    triage_hint: Optional[str] = None


class VerifierAgent:
    """Deterministic verifier running syntax checks, golden master equivalence tests, and regression tests."""

    def __init__(self, sandbox_runner: Optional[BaseSandboxRunner] = None):
        self.runner = sandbox_runner or create_sandbox_runner()
        self.recorder = CharacterizationRecorder(sandbox_runner=self.runner)

    def verify(
        self,
        repo_root: str,
        chunk_file_path: str,
        candidate_code: str,
        golden_suite: Optional[GoldenSuite] = None,
        regression_suites: Optional[List[GoldenSuite]] = None,
    ) -> VerificationResult:
        """Run strict multi-stage verification on proposed candidate code."""
        # Check 1: Syntax & compilation check
        try:
            ast.parse(candidate_code)
        except SyntaxError as exc:
            report = f"SyntaxError in {chunk_file_path} (line {exc.lineno}): {exc.msg}"
            return VerificationResult(
                passed=False,
                syntax_valid=False,
                syntax_error=report,
                trimmed_report=report,
                triage_category="syntax",
                triage_hint="Candidate code contains invalid Python 3 syntax. Fix brackets or keywords.",
            )

        # If no tests exist to run, candidate passed syntax check
        if not golden_suite or not golden_suite.records:
            return VerificationResult(
                passed=True,
                syntax_valid=True,
                trimmed_report="Passed syntax check (no characterization tests provided).",
            )

        # Prepare workspace: backup original file and write candidate code
        target_file = Path(repo_root) / chunk_file_path
        original_backup = target_file.read_text(encoding="utf-8", errors="replace") if target_file.exists() else None

        failing_cases: List[str] = []
        failure_details: List[str] = []
        has_regressions = False

        try:
            # Overwrite file with candidate code temporarily for test execution
            target_file.write_text(candidate_code, encoding="utf-8")

            # Check 2: Execute chunk's own characterization tests
            test_cases = [
                TestCase(
                    test_id=rec.test_id,
                    function_name=rec.function_name,
                    args=rec.args,
                    kwargs=rec.kwargs,
                )
                for rec in golden_suite.records
            ]

            actual_suite = self.recorder.record_module(
                repo_root=repo_root,
                module_rel_path=chunk_file_path,
                test_cases=test_cases,
            )

            for golden_rec, actual_rec in zip(golden_suite.records, actual_suite.records):
                comp: ComparisonResult = GoldenComparator.compare(golden_rec, actual_rec)
                if not comp.passed:
                    failing_cases.append(comp.test_id)
                    failure_details.append(
                        f"Test '{comp.test_id}' failed: {comp.diff_reason}\n"
                        f"  Expected: {comp.expected!r}\n"
                        f"  Actual:   {comp.actual!r}"
                    )

            # Check 3: Re-run regression suites of previously passed chunks
            if not failing_cases and regression_suites:
                for reg_suite in regression_suites:
                    reg_cases = [
                        TestCase(
                            test_id=r.test_id,
                            function_name=r.function_name,
                            args=r.args,
                            kwargs=r.kwargs,
                        )
                        for r in reg_suite.records
                    ]
                    reg_actual = self.recorder.record_module(
                        repo_root=repo_root,
                        module_rel_path=reg_suite.module_path,
                        test_cases=reg_cases,
                    )
                    for golden_r, actual_r in zip(reg_suite.records, reg_actual.records):
                        comp = GoldenComparator.compare(golden_r, actual_r)
                        if not comp.passed:
                            has_regressions = True
                            failing_cases.append(f"REGRESSION:{comp.test_id}")
                            failure_details.append(
                                f"Regression in earlier module '{reg_suite.module_path}':\n"
                                f"  Test '{comp.test_id}' broke: {comp.diff_reason}"
                            )

        finally:
            # Restore original file state
            if original_backup is not None:
                target_file.write_text(original_backup, encoding="utf-8")

        if failing_cases:
            trimmed = "\n---\n".join(failure_details[:3])  # Cap at first 3 failures
            category = "regression" if has_regressions else "semantic"
            return VerificationResult(
                passed=False,
                has_regressions=has_regressions,
                syntax_valid=True,
                failing_tests=failing_cases,
                trimmed_report=trimmed,
                triage_category=category,
                triage_hint=f"Failed {len(failing_cases)} characterization tests. Check expected return values.",
            )

        return VerificationResult(
            passed=True,
            has_regressions=False,
            syntax_valid=True,
            trimmed_report=f"All {len(golden_suite.records)} characterization tests passed with zero regressions.",
        )
