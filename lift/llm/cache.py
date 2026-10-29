"""Disk-based response cache for LLM requests keyed by prompt/parameter hash."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class LLMCache:
    """Stores and retrieves LLM responses on disk to guarantee reproducible, zero-cost reruns."""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or os.path.join(os.getcwd(), ".cache", "llm"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(
        model: str,
        messages: Any,
        temperature: float = 0.0,
        schema_name: Optional[str] = None,
    ) -> str:
        """Generate deterministic SHA256 hex hash of the complete request signature."""
        serialized = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "schema": schema_name,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached response payload if exists."""
        file_path = self.cache_dir / f"{key}.json"
        if file_path.is_file():
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def set(self, key: str, payload: Dict[str, Any]) -> None:
        """Persist response payload to disk."""
        file_path = self.cache_dir / f"{key}.json"
        try:
            file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def clear(self) -> None:
        """Wipe all disk cache entries."""
        for item in self.cache_dir.glob("*.json"):
            try:
                item.unlink()
            except Exception:
                pass
