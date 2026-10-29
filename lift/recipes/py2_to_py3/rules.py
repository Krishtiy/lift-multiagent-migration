"""Deterministic rules pass for Python 2 to Python 3 modernization.

Performs cheap, predictable AST and regex codemods for mechanical syntax changes
before passing remaining semantic challenges to the LLM Migrator.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class RuleTransformationResult:
    """Result of running deterministic codemods on a source chunk."""
    code: str
    rules_applied: List[str] = field(default_factory=list)
    changed: bool = False


class Py2ToPy3RulesPass:
    """Applies mechanical rules for common Python 2 idioms."""

    @classmethod
    def apply_rules(cls, source_code: str) -> RuleTransformationResult:
        code = source_code
        applied: List[str] = []

        # Rule 1: Print statement to print() function call (G-01)
        code, count = cls._transform_print(code)
        if count > 0:
            applied.append(f"G-01:print_function ({count} occurrence{'s' if count > 1 else ''})")

        # Rule 2: Exception handling syntax: except Exception, e -> except Exception as e (G-02)
        code, count = cls._transform_except(code)
        if count > 0:
            applied.append(f"G-02:except_as_syntax ({count} occurrence{'s' if count > 1 else ''})")

        # Rule 3: xrange -> range (G-03)
        code, count = re.subn(r"\bxrange\b", "range", code)
        if count > 0:
            applied.append(f"G-03:xrange_to_range ({count} occurrence{'s' if count > 1 else ''})")

        # Rule 4: dict.has_key(k) -> k in dict (G-04)
        code, count = cls._transform_has_key(code)
        if count > 0:
            applied.append(f"G-04:has_key_to_in ({count} occurrence{'s' if count > 1 else ''})")

        # Rule 5: dict.iteritems() / iterkeys() / itervalues() (G-07)
        code, count = re.subn(r"\.iteritems\(\)", ".items()", code)
        count_k, count_v = 0, 0
        code, count_k = re.subn(r"\.iterkeys\(\)", ".keys()", code)
        code, count_v = re.subn(r"\.itervalues\(\)", ".values()", code)
        total_iter = count + count_k + count_v
        if total_iter > 0:
            applied.append(f"G-07:dict_iter_methods ({total_iter} occurrence{'s' if total_iter > 1 else ''})")

        # Rule 6: Long integer literals (e.g. 123456789L -> 123456789) (G-08)
        code, count = re.subn(r"\b(\d+)L\b", r"\1", code)
        if count > 0:
            applied.append(f"G-08:long_literal ({count} occurrence{'s' if count > 1 else ''})")

        # Rule 7: StringIO import to io.StringIO (G-12)
        code, count = cls._transform_stringio(code)
        if count > 0:
            applied.append(f"G-12:stringio_to_io ({count} occurrence{'s' if count > 1 else ''})")

        return RuleTransformationResult(
            code=code,
            rules_applied=applied,
            changed=(code != source_code),
        )

    @classmethod
    def _transform_print(cls, code: str) -> Tuple[str, int]:
        """Convert 'print ...' statements without parentheses into print(...) calls."""
        count = 0
        new_lines = []
        for line in code.splitlines():
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]

            # Check if line begins with print keyword followed by space and not an existing call
            if stripped.startswith("print ") and not stripped.startswith("print("):
                content = stripped[len("print "):].strip()
                # Check for print >> sys.stderr, "..."
                redir_match = re.match(r"^>>\s*([a-zA-Z0-9_\.]+)\s*,\s*(.*)$", content)
                if redir_match:
                    target_file = redir_match.group(1)
                    actual_content = redir_match.group(2)
                    new_line = f"{indent}print({actual_content}, file={target_file})"
                else:
                    new_line = f"{indent}print({content})"
                new_lines.append(new_line)
                count += 1
            elif stripped == "print":
                new_lines.append(f"{indent}print()")
                count += 1
            else:
                new_lines.append(line)
        return "\n".join(new_lines), count

    @classmethod
    def _transform_except(cls, code: str) -> Tuple[str, int]:
        """Convert 'except E, e:' into 'except E as e:'."""
        # Handles single and parenthesized exception types
        pattern = r"except\s+(\(?[a-zA-Z0-9_.,\s]+\)?)\s*,\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:"
        transformed, count = re.subn(pattern, r"except \1 as \2:", code)
        return transformed, count

    @classmethod
    def _transform_has_key(cls, code: str) -> Tuple[str, int]:
        """Convert 'target.has_key(key)' to 'key in target'."""
        pattern = r"([a-zA-Z0-9_.]+)\.has_key\(([^)]+)\)"
        transformed, count = re.subn(pattern, r"\2 in \1", code)
        return transformed, count

    @classmethod
    def _transform_stringio(cls, code: str) -> Tuple[str, int]:
        """Modernize legacy import StringIO to import io."""
        count = 0
        if "import StringIO" in code or "StringIO.StringIO" in code:
            code = re.sub(r"\bimport StringIO\b", "import io", code)
            code = re.sub(r"\bStringIO\.StringIO\b", "io.StringIO", code)
            count += 1
        return code, count
