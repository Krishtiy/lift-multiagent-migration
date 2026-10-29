"""Deterministic AST repository analyzer and dependency graph builder."""

import ast
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import networkx as nx


@dataclass
class FileSymbols:
    """Symbols and dependencies extracted from a single source file."""
    file_path: str  # Relative path from repo root
    imports: List[str] = field(default_factory=list)
    internal_dependencies: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    dependency_level: int = 0


class PythonSourceParser:
    """Extracts imports and symbols from Python 2 and Python 3 source code.
    
    Uses standard ast where possible, with a robust fallback parser for Python 2
    constructs that trigger SyntaxError on Python 3 runtimes.
    """

    @classmethod
    def parse_source(cls, source_code: str, file_path: str) -> FileSymbols:
        symbols = FileSymbols(file_path=file_path)

        try:
            tree = ast.parse(source_code)
            cls._extract_from_ast(tree, symbols)
        except SyntaxError:
            # Python 2 syntax fallback (e.g. print "...", except E, e)
            cls._extract_fallback(source_code, symbols)

        return symbols

    @classmethod
    def _extract_from_ast(cls, tree: ast.AST, symbols: FileSymbols) -> None:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, getattr(ast, "AsyncFunctionDef", ())):
                symbols.functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                symbols.classes.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    symbols.imports.append(f"{module}.{alias.name}" if module else alias.name)

    @classmethod
    def _extract_fallback(cls, source_code: str, symbols: FileSymbols) -> None:
        """Regex/heuristic parser for Python 2 files with unparseable syntax on Python 3."""
        lines = source_code.splitlines()
        for line in lines:
            line_str = line.strip()
            # Top-level functions
            fn_match = re.match(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line_str)
            if fn_match and not line.startswith(" "):
                symbols.functions.append(fn_match.group(1))

            # Top-level classes
            class_match = re.match(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(\(|:)", line_str)
            if class_match and not line.startswith(" "):
                symbols.classes.append(class_match.group(1))

            # Imports: import x, y
            imp_match = re.match(r"^import\s+(.+)$", line_str)
            if imp_match:
                for item in imp_match.group(1).split(","):
                    mod = item.strip().split()[0]
                    symbols.imports.append(mod)

            # From imports: from x import y, z
            from_match = re.match(r"^from\s+([a-zA-Z0-9_\.]+)\s+import\s+(.+)$", line_str)
            if from_match:
                mod = from_match.group(1)
                for item in from_match.group(2).split(","):
                    target = item.strip().split()[0]
                    symbols.imports.append(f"{mod}.{target}")


class RepositoryAnalyzer:
    """Deterministic analyzer building NetworkX dependency graph and topological levels."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()

    def analyze(self, exclude_dirs: Optional[Set[str]] = None) -> nx.DiGraph:
        """Analyze repository and construct directed dependency graph.
        
        Edge (A, B) represents: Module A depends on Module B.
        Level 0 = Leaf modules that depend on nothing within the repository.
        """
        excludes = exclude_dirs or {".git", ".venv", "venv", "__pycache__", "tests_dev", "tests_heldout"}
        py_files: List[Path] = []

        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in excludes]
            for file in files:
                if file.endswith(".py") and not file.startswith("."):
                    py_files.append(Path(root) / file)

        # Collect symbol metadata for each file
        file_map: Dict[str, FileSymbols] = {}
        for p in py_files:
            rel_path = p.relative_to(self.repo_root).as_posix()
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            symbols = PythonSourceParser.parse_source(content, rel_path)
            file_map[rel_path] = symbols

        # Resolve internal dependencies
        module_to_file = self._build_module_lookup(list(file_map.keys()))
        for rel_path, symbols in file_map.items():
            internal_deps = set()
            for imp in symbols.imports:
                matched_file = self._match_import_to_file(imp, module_to_file, rel_path)
                if matched_file and matched_file != rel_path:
                    internal_deps.add(matched_file)
            symbols.internal_dependencies = sorted(list(internal_deps))

        # Build NetworkX DiGraph
        graph = nx.DiGraph()
        for rel_path, symbols in file_map.items():
            graph.add_node(
                rel_path,
                file_path=rel_path,
                functions=symbols.functions,
                classes=symbols.classes,
                imports=symbols.imports,
                internal_dependencies=symbols.internal_dependencies,
            )

        # Add directed edge (A -> B means A depends on B)
        for rel_path, symbols in file_map.items():
            for dep in symbols.internal_dependencies:
                graph.add_edge(rel_path, dep)

        # Compute topological levels
        self._compute_topological_levels(graph)

        return graph

    def _build_module_lookup(self, file_paths: List[str]) -> Dict[str, str]:
        """Map Python module dot paths to relative file paths."""
        lookup = {}
        for fp in file_paths:
            # E.g. "benchmarks/py2_app/text_utils.py" -> "benchmarks.py2_app.text_utils"
            # and short basename "text_utils"
            mod_path = fp[:-3].replace("/", ".") if fp.endswith(".py") else fp.replace("/", ".")
            lookup[mod_path] = fp
            basename = Path(fp).stem
            lookup[basename] = fp
        return lookup

    def _match_import_to_file(self, imp_str: str, lookup: Dict[str, str], current_file: str) -> Optional[str]:
        """Resolve import statement to a relative file path in the repository."""
        # Exact match
        if imp_str in lookup:
            return lookup[imp_str]

        parts = imp_str.split(".")
        # Check all possible sub-segments (e.g. "text_utils" inside "benchmarks.py2_app.text_utils.func")
        for i in range(len(parts)):
            for j in range(len(parts), i, -1):
                candidate = ".".join(parts[i:j])
                if candidate in lookup:
                    return lookup[candidate]

        # Relative import resolution within same folder
        curr_dir = Path(current_file).parent.as_posix()
        candidate_rel = f"{curr_dir}/{parts[0]}.py" if curr_dir != "." else f"{parts[0]}.py"
        if candidate_rel in lookup.values():
            return candidate_rel

        return None


    def _compute_topological_levels(self, graph: nx.DiGraph) -> None:
        """Assign topological dependency levels.
        
        Level 0: files with no internal dependencies (out_degree == 0).
        Level N: 1 + max(level of dependencies).
        """
        # Check for cycles
        cycles = list(nx.simple_cycles(graph))
        if cycles:
            # If cycles exist, mark all nodes in cycle at same level
            for cycle in cycles:
                for node in cycle:
                    graph.nodes[node]["has_cycle"] = True

        levels: Dict[str, int] = {}

        def get_level(node: str, visited: Set[str]) -> int:
            if node in levels:
                return levels[node]
            if node in visited:
                return 0  # Cycle safeguard

            visited.add(node)
            deps = list(graph.successors(node))  # Node depends on these
            if not deps:
                lvl = 0
            else:
                lvl = 1 + max(get_level(dep, visited.copy()) for dep in deps)

            levels[node] = lvl
            return lvl

        for node in graph.nodes():
            lvl = get_level(node, set())
            graph.nodes[node]["dependency_level"] = lvl

    @staticmethod
    def to_json_dict(graph: nx.DiGraph) -> Dict[str, Any]:
        """Serialize graph to structured JSON dict for planner consumption."""
        nodes = []
        for node, data in graph.nodes(data=True):
            nodes.append({
                "file_path": node,
                "dependency_level": data.get("dependency_level", 0),
                "functions": data.get("functions", []),
                "classes": data.get("classes", []),
                "imports": data.get("imports", []),
                "internal_dependencies": data.get("internal_dependencies", []),
            })

        # Sort by dependency level (level 0 first)
        nodes.sort(key=lambda n: (n["dependency_level"], n["file_path"]))

        edges = [{"source": u, "target": v} for u, v in graph.edges()]
        return {"nodes": nodes, "edges": edges}
