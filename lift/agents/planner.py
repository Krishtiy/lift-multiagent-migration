"""Deterministic Planner and Chunker agent.

Transforms repository dependency graphs into ordered migration chunks and compact,
self-contained Context Packs with public dependency signatures and recipe hints.
"""

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import networkx as nx

from lift.agents.analyzer import RepositoryAnalyzer

# Recipe-specific migration hints (Python 2 to Python 3)
PY2_TO_PY3_HINTS = [
    "Integer division: In Python 2, 7 / 2 returns 3. In Python 3 it returns 3.5. Use // if integer truncation is required.",
    "Built-ins: xrange() is removed (use range()). long type is unified into int.",
    "Dictionaries: has_key() is removed (use 'key in dict'). iteritems() is removed (use items()).",
    "Strings and Bytes: In Python 2, str is bytes. In Python 3, str is unicode; hashlib and binary I/O require bytes (.encode()).",
    "Classes: __nonzero__() must be migrated to __bool__().",
    "Exceptions: except Exception, e must be rewritten as except Exception as e.",
    "Print: print statement must be rewritten as print() function call.",
]


@dataclass
class ContextPack:
    """Compact context pack supplied to the Migrator agent for a single chunk."""
    chunk_id: str
    file_path: str
    dependency_level: int
    source_code: str
    dependency_signatures: Dict[str, List[str]] = field(default_factory=dict)
    recipe_hints: List[str] = field(default_factory=list)
    estimated_tokens: int = 0
    is_parallel_safe: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MigrationPlan:
    """Complete migration execution plan grouped by dependency levels."""
    chunks: List[ContextPack]
    chunks_by_level: Dict[int, List[ContextPack]]
    total_chunks: int
    max_level: int


class SignatureExtractor:
    """Extracts public function and class signatures without implementation bodies."""

    @classmethod
    def extract_from_source(cls, source_code: str) -> List[str]:
        signatures: List[str] = []
        try:
            tree = ast.parse(source_code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, getattr(ast, "AsyncFunctionDef", ()))):
                    args = [a.arg for a in node.args.args]
                    sig = f"def {node.name}({', '.join(args)}):"
                    signatures.append(sig)
                elif isinstance(node, ast.ClassDef):
                    bases = [getattr(b, "id", getattr(b, "name", "object")) for b in node.bases]
                    base_str = f"({', '.join(bases)})" if bases else ""
                    sig = f"class {node.name}{base_str}:"
                    signatures.append(sig)
        except SyntaxError:
            # Fallback regex for Python 2 source code
            for line in source_code.splitlines():
                stripped = line.strip()
                if stripped.startswith("def ") and stripped.endswith(":"):
                    signatures.append(stripped)
                elif stripped.startswith("class ") and stripped.endswith(":"):
                    signatures.append(stripped)

        return signatures


class RepositoryPlanner:
    """Deterministic planner that converts a dependency graph into an ordered migration plan."""

    def __init__(
        self,
        repo_root: str,
        token_budget_per_chunk: int = 4000,
        recipe_name: str = "py2_to_py3",
    ):
        self.repo_root = Path(repo_root).resolve()
        self.token_budget = token_budget_per_chunk
        self.recipe_hints = PY2_TO_PY3_HINTS if recipe_name == "py2_to_py3" else []

    def plan_from_graph(self, graph: nx.DiGraph) -> MigrationPlan:
        """Create ordered migration chunks from the dependency graph."""
        chunks: List[ContextPack] = []
        chunks_by_level: Dict[int, List[ContextPack]] = {}

        # Sort nodes by topological level (Level 0 leaves first)
        sorted_nodes = sorted(
            graph.nodes(data=True),
            key=lambda item: (item[1].get("dependency_level", 0), item[0])
        )

        for rel_path, data in sorted_nodes:
            full_path = self.repo_root / rel_path
            try:
                source_code = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                source_code = ""

            dep_level = data.get("dependency_level", 0)
            internal_deps = data.get("internal_dependencies", [])

            # Extract signatures of dependencies
            dep_sigs: Dict[str, List[str]] = {}
            for dep_file in internal_deps:
                dep_full_path = self.repo_root / dep_file
                if dep_full_path.exists():
                    dep_code = dep_full_path.read_text(encoding="utf-8", errors="replace")
                    dep_sigs[dep_file] = SignatureExtractor.extract_from_source(dep_code)

            # Estimate token count (heuristic: ~4 characters per token)
            text_repr = source_code + str(dep_sigs) + str(self.recipe_hints)
            estimated_tokens = max(1, len(text_repr) // 4)

            chunk = ContextPack(
                chunk_id=f"chunk_{rel_path.replace('/', '_').replace('.', '_')}",
                file_path=rel_path,
                dependency_level=dep_level,
                source_code=source_code,
                dependency_signatures=dep_sigs,
                recipe_hints=self.recipe_hints,
                estimated_tokens=estimated_tokens,
                is_parallel_safe=False,  # Updated after grouping
            )

            chunks.append(chunk)
            chunks_by_level.setdefault(dep_level, []).append(chunk)

        # Mark chunks as parallel-safe if multiple exist at the same level
        for level, level_chunks in chunks_by_level.items():
            if len(level_chunks) > 1:
                for c in level_chunks:
                    c.is_parallel_safe = True

        max_level = max(chunks_by_level.keys()) if chunks_by_level else 0

        return MigrationPlan(
            chunks=chunks,
            chunks_by_level=chunks_by_level,
            total_chunks=len(chunks),
            max_level=max_level,
        )
