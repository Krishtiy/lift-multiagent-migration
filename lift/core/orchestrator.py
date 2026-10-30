"""Deterministic migration orchestrator executing the chunk state machine control loop."""

import hashlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from lift.agents.analyzer import RepositoryAnalyzer
from lift.agents.migrator import MigratorAgent
from lift.agents.planner import ContextPack, RepositoryPlanner
from lift.agents.recorder import (
    CharacterizationRecorder,
    GoldenSuite,
    InputProposalGenerator,
)
from lift.agents.verifier import VerificationResult, VerifierAgent
from lift.core.state_machine import transition_chunk
from lift.db.models import (
    Attempt,
    Chunk,
    ChunkStatus,
    Job,
    JobStatus,
    ReviewReason,
)
from lift.db.session import create_db_engine, get_db, init_db
from lift.llm.client import UnifiedLLMClient
from lift.recipes.py2_to_py3.rules import Py2ToPy3RulesPass
from lift.sandbox.runner import create_sandbox_runner

MAX_ATTEMPTS = 3


@dataclass
class MigrationReport:
    """End-to-end report generated upon migration job completion."""
    job_id: str
    recipe: str
    repo_path: str
    total_chunks: int
    passed_chunks: int
    review_chunks: int
    failed_chunks: int
    tokens_used: int
    cost_usd: float
    duration_seconds: float
    chunk_summaries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MigrationOrchestrator:
    """The central deterministic control loop managing state transitions and rollbacks."""

    def __init__(
        self,
        engine=None,
        llm_client: Optional[UnifiedLLMClient] = None,
        default_model: str = "mock-model",
        escalation_model: str = "mock-model-strong",
        max_attempts: int = 3,
        apply_rules_pass: bool = True,
        skip_verifier: bool = False,
        role_mode: str = "separated",
        enable_escalation: bool = True,
    ):
        self.engine = engine or create_db_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.llm_client = llm_client or UnifiedLLMClient()
        self.default_model = default_model
        self.escalation_model = escalation_model
        self.max_attempts = max_attempts
        self.apply_rules_pass = apply_rules_pass
        self.skip_verifier = skip_verifier
        self.role_mode = role_mode
        self.enable_escalation = enable_escalation

        self.migrator = MigratorAgent(llm_client=self.llm_client)
        self.sandbox_runner = create_sandbox_runner()
        self.recorder = CharacterizationRecorder(sandbox_runner=self.sandbox_runner)
        self.verifier = VerifierAgent(sandbox_runner=self.sandbox_runner)

    def run_migration(
        self,
        repo_path: str,
        recipe: str = "py2_to_py3",
        token_budget: int = 500_000,
        job_id: Optional[str] = None,
    ) -> MigrationReport:
        """Execute end-to-end multi-agent migration on the target repository."""
        start_time = time.monotonic()
        repo_root = Path(repo_path).resolve()

        # 1. Initialize Job in DB
        with get_db(self.engine) as session:
            job = Job(
                recipe=recipe,
                repo_reference=str(repo_root),
                status=JobStatus.RUNNING,
                token_budget=token_budget,
            )
            if job_id:
                job.id = job_id
            session.add(job)
            session.flush()
            active_job_id = job.id

        # 2. Phase 4: Deterministic Analysis
        analyzer = RepositoryAnalyzer(str(repo_root))
        graph = analyzer.analyze()

        # 3. Phase 5: Deterministic Planning
        planner = RepositoryPlanner(
            repo_root=str(repo_root),
            token_budget_per_chunk=4000,
            recipe_name=recipe,
        )
        plan = planner.plan_from_graph(graph)

        # 4. Populate Chunks in DB
        with get_db(self.engine) as session:
            for c in plan.chunks:
                chunk_rec = Chunk(
                    id=c.chunk_id,
                    job_id=active_job_id,
                    file_path=c.file_path,
                    dependency_level=c.dependency_level,
                    status=ChunkStatus.PENDING,
                )
                session.add(chunk_rec)

        regression_suites: List[GoldenSuite] = []
        chunk_summaries: List[Dict[str, Any]] = []

        # 5. Execute Loop Level by Level
        for level in sorted(plan.chunks_by_level.keys()):
            for chunk in plan.chunks_by_level[level]:
                summary = self._process_chunk(
                    active_job_id=active_job_id,
                    repo_root=repo_root,
                    chunk=chunk,
                    regression_suites=regression_suites,
                )
                chunk_summaries.append(summary)

        # 6. Aggregate Final Results
        passed = sum(1 for s in chunk_summaries if s["status"] == ChunkStatus.PASSED.value)
        review = sum(1 for s in chunk_summaries if s["status"] == ChunkStatus.NEEDS_REVIEW.value)
        failed = sum(1 for s in chunk_summaries if s["status"] == ChunkStatus.FAILED.value)

        duration = round(time.monotonic() - start_time, 2)

        with get_db(self.engine) as session:
            job_obj = session.get(Job, active_job_id)
            if job_obj:
                job_obj.status = JobStatus.COMPLETED if review == 0 and failed == 0 else JobStatus.FAILED
                job_obj.tokens_used = self.llm_client.total_tokens_used
                job_obj.cost_usd = self.llm_client.total_cost_usd

        return MigrationReport(
            job_id=active_job_id,
            recipe=recipe,
            repo_path=str(repo_root),
            total_chunks=len(plan.chunks),
            passed_chunks=passed,
            review_chunks=review,
            failed_chunks=failed,
            tokens_used=self.llm_client.total_tokens_used,
            cost_usd=self.llm_client.total_cost_usd,
            duration_seconds=duration,
            chunk_summaries=chunk_summaries,
        )

    def _process_chunk(
        self,
        active_job_id: str,
        repo_root: Path,
        chunk: ContextPack,
        regression_suites: List[GoldenSuite],
    ) -> Dict[str, Any]:
        """Drive a single chunk through the state machine."""
        chunk_id = chunk.chunk_id
        target_file = repo_root / chunk.file_path

        # Snapshot original code for rollback
        original_source = target_file.read_text(encoding="utf-8", errors="replace")

        # Step 1: Record characterization tests on old code
        with get_db(self.engine) as session:
            transition_chunk(session, chunk_id, ChunkStatus.PENDING, ChunkStatus.TESTS_READY)

        # Extract functions and generate test cases
        test_cases = []
        for fn_name in self._extract_function_names(original_source):
            test_cases.extend(InputProposalGenerator.propose_inputs_for_function(fn_name, []))

        golden_suite = self.recorder.record_module(
            repo_root=str(repo_root),
            module_rel_path=chunk.file_path,
            test_cases=test_cases,
        )

        # Step 2: Apply deterministic rules pass if enabled
        if self.apply_rules_pass:
            rules_res = Py2ToPy3RulesPass.apply_rules(original_source)
            candidate_code = rules_res.code
        else:
            candidate_code = original_source

        with get_db(self.engine) as session:
            transition_chunk(session, chunk_id, ChunkStatus.TESTS_READY, ChunkStatus.RULES_APPLIED)
            transition_chunk(session, chunk_id, ChunkStatus.RULES_APPLIED, ChunkStatus.MIGRATING)

        # Baseline A: Rules-only mode (no LLM attempts)
        if self.max_attempts <= 0:
            target_file.write_text(candidate_code, encoding="utf-8")
            with get_db(self.engine) as session:
                transition_chunk(session, chunk_id, ChunkStatus.MIGRATING, ChunkStatus.VERIFYING)
                transition_chunk(session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.PASSED)
            return {
                "chunk_id": chunk_id,
                "file_path": chunk.file_path,
                "status": ChunkStatus.PASSED.value,
                "attempts": 0,
            }

        # Baseline B: 1-shot LLM rewrite without verifier feedback loop
        if self.skip_verifier:
            attempt_res = self.migrator.migrate_chunk(
                context_pack=chunk,
                rules_pass_code=candidate_code,
                failing_report=None,
                previous_code=candidate_code,
                model=self.default_model,
            )
            target_file.write_text(attempt_res.candidate_code, encoding="utf-8")
            with get_db(self.engine) as session:
                transition_chunk(session, chunk_id, ChunkStatus.MIGRATING, ChunkStatus.VERIFYING)
                transition_chunk(session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.PASSED)
            return {
                "chunk_id": chunk_id,
                "file_path": chunk.file_path,
                "status": ChunkStatus.PASSED.value,
                "attempts": 1,
            }

        last_report: Optional[str] = None
        last_failure_key: Optional[str] = None
        final_status = ChunkStatus.FAILED
        review_reason: Optional[ReviewReason] = None
        total_attempts = 0

        # Step 3: Migration attempt loop
        for attempt_idx in range(1, self.max_attempts + 1):
            total_attempts = attempt_idx
            # Escalate to stronger model on final retry if enabled
            model_to_use = (
                self.escalation_model
                if (self.enable_escalation and attempt_idx == self.max_attempts and self.max_attempts > 1)
                else self.default_model
            )

            # Check budget
            if self.llm_client.total_tokens_used >= self.llm_client.token_budget:
                review_reason = ReviewReason.BUDGET
                self._rollback(target_file, original_source)
                with get_db(self.engine) as session:
                    transition_chunk(
                        session, chunk_id, ChunkStatus.MIGRATING, ChunkStatus.NEEDS_REVIEW, review_reason
                    )
                return {"chunk_id": chunk_id, "file_path": chunk.file_path, "status": ChunkStatus.NEEDS_REVIEW.value, "reason": "budget"}

            # Migrator agent proposes candidate code
            attempt_res = self.migrator.migrate_chunk(
                context_pack=chunk,
                rules_pass_code=candidate_code,
                failing_report=last_report,
                previous_code=candidate_code,
                model=model_to_use,
            )
            candidate_code = attempt_res.candidate_code

            # Verifier runs syntax, golden equivalence, and regression checks
            with get_db(self.engine) as session:
                transition_chunk(session, chunk_id, ChunkStatus.MIGRATING, ChunkStatus.VERIFYING)

            verif_res: VerificationResult = self.verifier.verify(
                repo_root=str(repo_root),
                chunk_file_path=chunk.file_path,
                candidate_code=candidate_code,
                golden_suite=golden_suite,
                regression_suites=regression_suites,
            )

            # Log attempt to database
            input_hash = hashlib.sha256(candidate_code.encode("utf-8")).hexdigest()
            with get_db(self.engine) as session:
                attempt_row = Attempt(
                    chunk_id=chunk_id,
                    attempt_number=attempt_idx,
                    input_hash=input_hash,
                    result="PASSED" if verif_res.passed else "FAILED",
                    trimmed_error=verif_res.trimmed_report if not verif_res.passed else None,
                )
                session.add(attempt_row)

            # Check success
            if verif_res.passed and not verif_res.has_regressions:
                # Permanently commit modernized code
                target_file.write_text(candidate_code, encoding="utf-8")
                if golden_suite and golden_suite.records:
                    regression_suites.append(golden_suite)
                with get_db(self.engine) as session:
                    transition_chunk(session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.PASSED)
                return {
                    "chunk_id": chunk_id,
                    "file_path": chunk.file_path,
                    "status": ChunkStatus.PASSED.value,
                    "attempts": attempt_idx,
                }

            # Loop Detection: same exact failure report twice consecutively
            current_failure_key = verif_res.trimmed_report
            if last_failure_key and last_failure_key == current_failure_key:
                review_reason = ReviewReason.STUCK
                self._rollback(target_file, original_source)
                with get_db(self.engine) as session:
                    transition_chunk(
                        session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.NEEDS_REVIEW, review_reason
                    )
                return {
                    "chunk_id": chunk_id,
                    "file_path": chunk.file_path,
                    "status": ChunkStatus.NEEDS_REVIEW.value,
                    "reason": "stuck",
                    "attempts": attempt_idx,
                }

            last_failure_key = current_failure_key
            last_report = verif_res.trimmed_report

            # Prepare retry if attempts remain
            if attempt_idx < self.max_attempts:
                with get_db(self.engine) as session:
                    transition_chunk(session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.RETRY)
                    transition_chunk(session, chunk_id, ChunkStatus.RETRY, ChunkStatus.MIGRATING)

        # Max attempts exhausted -> Rollback and mark NEEDS_REVIEW
        self._rollback(target_file, original_source)
        with get_db(self.engine) as session:
            transition_chunk(
                session, chunk_id, ChunkStatus.VERIFYING, ChunkStatus.NEEDS_REVIEW, ReviewReason.MAX_ATTEMPTS
            )

        return {
            "chunk_id": chunk_id,
            "file_path": chunk.file_path,
            "status": ChunkStatus.NEEDS_REVIEW.value,
            "reason": "max_attempts",
            "attempts": total_attempts,
        }

    @staticmethod
    def _rollback(target_file: Path, original_source: str) -> None:
        """Restore file back to its pre-migration snapshot."""
        try:
            target_file.write_text(original_source, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _extract_function_names(source_code: str) -> List[str]:
        names = []
        for line in source_code.splitlines():
            s = line.strip()
            if s.startswith("def ") and "(" in s:
                fn = s[4:].split("(")[0].strip()
                names.append(fn)
        return names
