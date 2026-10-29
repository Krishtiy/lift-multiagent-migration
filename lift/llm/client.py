"""Unified LLM client interface with retries, Pydantic validation, disk caching, and token budgeting."""

import abc
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, ValidationError

from lift.llm.cache import LLMCache


class BudgetExceededError(Exception):
    """Raised when an operation exceeds the allocated token budget for a job."""
    pass


class LLMRateLimitError(Exception):
    """Raised when provider returns a 429 rate limit error."""
    pass


class LLMTransientError(Exception):
    """Raised on network timeouts or temporary 5xx errors."""
    pass


@dataclass
class LLMResponse:
    """Structured response from the unified LLM provider."""
    content: str
    parsed: Optional[Any] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    prompt_hash: str = ""
    prompt_version: Optional[str] = None


class BaseLLMProvider(abc.ABC):
    """Abstract interface for raw provider SDKs."""

    @abc.abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        """Returns dict: {'content': str, 'prompt_tokens': int, 'completion_tokens': int}"""
        pass


class FakeLLMProvider(BaseLLMProvider):
    """Mock provider for unit testing, offline development, and CI."""

    def __init__(
        self,
        canned_responses: Optional[List[str]] = None,
        rate_limit_failures: int = 0,
        malformed_json_failures: int = 0,
    ):
        self.canned_responses = list(canned_responses or ["{}"])
        self.rate_limit_failures = rate_limit_failures
        self.malformed_json_failures = malformed_json_failures
        self.call_count = 0

    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        self.call_count += 1

        # Simulate rate limit failures
        if self.rate_limit_failures > 0:
            self.rate_limit_failures -= 1
            raise LLMRateLimitError("Rate limit exceeded (simulated)")

        # Simulate malformed JSON
        if self.malformed_json_failures > 0:
            self.malformed_json_failures -= 1
            return {
                "content": "{malformed_json: not valid,",
                "prompt_tokens": 100,
                "completion_tokens": 20,
            }

        response_content = (
            self.canned_responses.pop(0)
            if self.canned_responses
            else '{"status": "ok"}'
        )

        return {
            "content": response_content,
            "prompt_tokens": 150,
            "completion_tokens": 50,
        }


# Standard pricing per million tokens
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gemini-2.5-flash": {"prompt": 0.15, "completion": 0.60},
    "gemini-2.5-pro": {"prompt": 1.25, "completion": 5.00},
    "mock-model": {"prompt": 0.10, "completion": 0.20},
}


class UnifiedLLMClient:
    """Unified client orchestrating caching, retries, Pydantic validation, and budget caps."""

    def __init__(
        self,
        provider: Optional[BaseLLMProvider] = None,
        cache: Optional[LLMCache] = None,
        token_budget: int = 500_000,
        max_retries: int = 3,
    ):
        self.provider = provider or FakeLLMProvider()
        self.cache = cache or LLMCache()
        self.token_budget = token_budget
        self.max_retries = max_retries
        self.total_tokens_used = 0
        self.total_cost_usd = 0.0

    def complete(
        self,
        messages: List[Dict[str, str]],
        model: str = "mock-model",
        temperature: float = 0.0,
        schema: Optional[Type[BaseModel]] = None,
        prompt_version: Optional[str] = None,
    ) -> LLMResponse:
        """Execute chat completion with caching, retries, and schema validation."""
        # Check budget
        if self.total_tokens_used >= self.token_budget:
            raise BudgetExceededError(
                f"Token budget of {self.token_budget} exceeded (used: {self.total_tokens_used})."
            )

        schema_name = schema.__name__ if schema else None
        prompt_hash = LLMCache.compute_key(model, messages, temperature, schema_name)

        # 1. Check disk cache
        cached_entry = self.cache.get(prompt_hash)
        if cached_entry:
            parsed_data = None
            if schema and "parsed" in cached_entry and cached_entry["parsed"]:
                try:
                    parsed_data = schema.model_validate(cached_entry["parsed"])
                except Exception:
                    parsed_data = None

            return LLMResponse(
                content=cached_entry["content"],
                parsed=parsed_data,
                prompt_tokens=cached_entry.get("prompt_tokens", 0),
                completion_tokens=cached_entry.get("completion_tokens", 0),
                total_tokens=cached_entry.get("total_tokens", 0),
                cost_usd=0.0,  # Cache hits cost $0.00
                cached=True,
                prompt_hash=prompt_hash,
                prompt_version=prompt_version,
            )

        # 2. Call provider with exponential backoff retry loop
        raw_output, prompt_tokens, comp_tokens = self._execute_with_retry(
            messages, model, temperature
        )

        # 3. Pydantic structured output validation with 1-shot repair retry
        parsed_instance = None
        if schema:
            parsed_instance = self._validate_or_repair(
                raw_output, schema, messages, model, temperature
            )

        # 4. Compute cost and update accounting
        total_tokens = prompt_tokens + comp_tokens
        cost = self._calculate_cost(model, prompt_tokens, comp_tokens)

        self.total_tokens_used += total_tokens
        self.total_cost_usd += cost

        # 5. Persist to disk cache
        cache_payload = {
            "content": raw_output,
            "parsed": parsed_instance.model_dump() if parsed_instance else None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": comp_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }
        self.cache.set(prompt_hash, cache_payload)

        return LLMResponse(
            content=raw_output,
            parsed=parsed_instance,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            cached=False,
            prompt_hash=prompt_hash,
            prompt_version=prompt_version,
        )

    def _execute_with_retry(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
    ) -> Any:
        attempts = 0
        backoff = 0.05  # Initial fast backoff for testing

        while attempts < self.max_retries:
            try:
                res = self.provider.generate(messages, model, temperature)
                return res["content"], res["prompt_tokens"], res["completion_tokens"]
            except (LLMRateLimitError, LLMTransientError):
                attempts += 1
                if attempts >= self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2

    def _validate_or_repair(
        self,
        raw_content: str,
        schema: Type[BaseModel],
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
    ) -> BaseModel:
        """Validate JSON against schema; re-ask provider once on error."""
        try:
            cleaned = self._clean_json(raw_content)
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            # Re-ask provider once with explicit error feedback
            repair_messages = list(messages) + [
                {"role": "assistant", "content": raw_content},
                {
                    "role": "user",
                    "content": f"Output failed schema validation: {exc}. Please respond with strict JSON.",
                },
            ]
            repaired_content, _, _ = self._execute_with_retry(repair_messages, model, temperature)
            cleaned_rep = self._clean_json(repaired_content)
            data_rep = json.loads(cleaned_rep)
            return schema.model_validate(data_rep)

    @staticmethod
    def _clean_json(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```json"):
            text = text[len("```json"):].strip()
        if text.startswith("```"):
            text = text[len("```"):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        return text

    @staticmethod
    def _calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = MODEL_PRICING.get(model, {"prompt": 0.15, "completion": 0.60})
        prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
        comp_cost = (completion_tokens / 1_000_000) * pricing["completion"]
        return round(prompt_cost + comp_cost, 6)
