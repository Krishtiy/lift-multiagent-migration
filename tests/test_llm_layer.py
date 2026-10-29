"""Unit tests for unified LLM provider abstraction, retries, caching, and token budgeting."""

import tempfile
import unittest
from pydantic import BaseModel

from lift.llm.cache import LLMCache
from lift.llm.client import (
    BudgetExceededError,
    FakeLLMProvider,
    UnifiedLLMClient,
)


class MigrationResponseSchema(BaseModel):
    migrated_code: str
    changes_count: int


class TestLLMLayer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache = LLMCache(cache_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_retry_on_transient_rate_limit(self):
        """Requirement: Retry with exponential backoff on rate limits."""
        # Provider configured to fail twice with rate limit, then succeed
        provider = FakeLLMProvider(
            canned_responses=['{"migrated_code": "print(1)", "changes_count": 1}'],
            rate_limit_failures=2,
        )
        client = UnifiedLLMClient(provider=provider, cache=self.cache, max_retries=3)

        messages = [{"role": "user", "content": "migrate code"}]
        res = client.complete(messages=messages, model="mock-model")

        self.assertFalse(res.cached)
        self.assertIn("migrated_code", res.content)
        self.assertEqual(provider.call_count, 3)  # 2 failed attempts + 1 success

    def test_invalid_json_auto_repair(self):
        """Requirement: Re-ask once with error if JSON output is invalid."""
        # Provider returns malformed JSON once, then valid JSON on repair
        provider = FakeLLMProvider(
            canned_responses=['{"migrated_code": "def test(): pass", "changes_count": 2}'],
            malformed_json_failures=1,
        )
        client = UnifiedLLMClient(provider=provider, cache=self.cache)

        messages = [{"role": "user", "content": "migrate code"}]
        res = client.complete(
            messages=messages,
            model="mock-model",
            schema=MigrationResponseSchema,
        )

        self.assertIsNotNone(res.parsed)
        self.assertEqual(res.parsed.changes_count, 2)
        self.assertEqual(res.parsed.migrated_code, "def test(): pass")

    def test_disk_cache_hit_returns_free_instant_response(self):
        """Requirement: Keyed disk cache makes identical reruns free and reproducible."""
        provider = FakeLLMProvider(canned_responses=['{"result": "unique_response_123"}'])
        client = UnifiedLLMClient(provider=provider, cache=self.cache)

        messages = [{"role": "user", "content": "calculate"}]

        # Call 1: Misses cache, hits provider
        res1 = client.complete(messages=messages, model="mock-model")
        self.assertFalse(res1.cached)
        self.assertGreater(res1.cost_usd, 0.0)
        self.assertEqual(provider.call_count, 1)

        # Call 2: Identical parameters -> must hit disk cache
        res2 = client.complete(messages=messages, model="mock-model")
        self.assertTrue(res2.cached)
        self.assertEqual(res2.cost_usd, 0.0)
        self.assertEqual(res2.content, res1.content)
        self.assertEqual(provider.call_count, 1)  # Provider was NOT called again

    def test_budget_exceeded_stops_execution(self):
        """Requirement: Token budget exhaustion raises BudgetExceededError."""
        provider = FakeLLMProvider(canned_responses=['{"result": "ok"}'])
        # Token budget of 150 tokens
        client = UnifiedLLMClient(provider=provider, cache=self.cache, token_budget=150)

        messages1 = [{"role": "user", "content": "task 1"}]
        client.complete(messages=messages1, model="mock-model")  # Consumes 200 tokens

        # Second call must be blocked by budget cap
        messages2 = [{"role": "user", "content": "task 2"}]
        with self.assertRaises(BudgetExceededError):
            client.complete(messages=messages2, model="mock-model")


if __name__ == "__main__":
    unittest.main()
