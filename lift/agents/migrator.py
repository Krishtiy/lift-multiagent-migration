"""Migrator agent responsible for modernizing legacy code chunks using LLM reasoning."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from lift.agents.planner import ContextPack
from lift.llm.client import LLMResponse, UnifiedLLMClient


class MigratorOutputSchema(BaseModel):
    """Pydantic schema enforcing structured migrator outputs."""
    migrated_code: str = Field(description="Full modernized Python 3 code preserving legacy behavior.")
    changes_explained: List[str] = Field(default_factory=list, description="List of semantic transformations applied.")
    flagged_for_review: bool = Field(default=False, description="True if manual human review is recommended.")
    review_note: Optional[str] = Field(default=None, description="Explanation if flagged for review.")


@dataclass
class MigrationAttemptResult:
    """Outcome of an individual migrator execution attempt."""
    candidate_code: str
    changes_explained: List[str]
    flagged_for_review: bool
    review_note: Optional[str]
    tokens_used: int
    cost_usd: float
    cached: bool
    prompt_hash: str


class MigratorAgent:
    """Migrator agent with restricted tools: can only rewrite target chunk code."""

    def __init__(
        self,
        llm_client: UnifiedLLMClient,
        prompt_template_path: Optional[str] = None,
    ):
        self.llm_client = llm_client
        prompt_path = Path(prompt_template_path or os.path.join(os.getcwd(), "prompts", "migrator_v1.md"))
        if prompt_path.is_file():
            self.system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            self.system_prompt = "You are the Lift Migrator Agent modernizing Python 2 to Python 3."

    def migrate_chunk(
        self,
        context_pack: ContextPack,
        rules_pass_code: str,
        failing_report: Optional[str] = None,
        previous_code: Optional[str] = None,
        model: str = "mock-model",
    ) -> MigrationAttemptResult:
        """Generate modernized Python 3 candidate code for the specified chunk."""
        user_message_parts = [
            f"### Target File: {context_pack.file_path}",
            f"### Dependency Level: {context_pack.dependency_level}",
            "\n### Code after deterministic rules pass:",
            "```python",
            rules_pass_code,
            "```",
        ]

        if context_pack.dependency_signatures:
            user_message_parts.append("\n### Public Signatures of Imported Dependencies:")
            for dep_file, sigs in context_pack.dependency_signatures.items():
                user_message_parts.append(f"From {dep_file}:")
                for s in sigs:
                    user_message_parts.append(f"  {s}")

        if context_pack.recipe_hints:
            user_message_parts.append("\n### Recipe Gotchas & Behavioral Constraints:")
            for hint in context_pack.recipe_hints:
                user_message_parts.append(f"- {hint}")

        # If this is a retry attempt, include the failure report from the verifier
        if failing_report:
            user_message_parts.append("\n### ATTENTION: Previous Migration Attempt Failed Tests:")
            user_message_parts.append("```")
            user_message_parts.append(failing_report)
            user_message_parts.append("```")
            if previous_code:
                user_message_parts.append("\n### Previously Attempted Code:")
                user_message_parts.append("```python")
                user_message_parts.append(previous_code)
                user_message_parts.append("```")
            user_message_parts.append("Please analyze the test failure and adjust your implementation to match the expected legacy outputs.")

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": "\n".join(user_message_parts)},
        ]

        response: LLMResponse = self.llm_client.complete(
            messages=messages,
            model=model,
            temperature=0.0,
            schema=MigratorOutputSchema,
            prompt_version="migrator_v1.0",
        )

        parsed: MigratorOutputSchema = response.parsed or MigratorOutputSchema(
            migrated_code=rules_pass_code,
            changes_explained=["Fallback to rules code"],
            flagged_for_review=True,
            review_note="Failed to parse LLM structured output",
        )

        return MigrationAttemptResult(
            candidate_code=parsed.migrated_code,
            changes_explained=parsed.changes_explained,
            flagged_for_review=parsed.flagged_for_review,
            review_note=parsed.review_note,
            tokens_used=response.total_tokens,
            cost_usd=response.cost_usd,
            cached=response.cached,
            prompt_hash=response.prompt_hash,
        )
