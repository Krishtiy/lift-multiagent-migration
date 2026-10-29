"""Characterization test generator, sandbox execution recorder, and golden master comparator."""

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from lift.sandbox.runner import BaseSandboxRunner, SandboxLimits, create_sandbox_runner


@dataclass
class TestCase:
    """A proposed test input invocation."""
    test_id: str
    function_name: str
    args: List[Any] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GoldenExecution:
    """Recorded ground-truth behavior executed on legacy code."""
    test_id: str
    function_name: str
    args: List[Any]
    kwargs: Dict[str, Any]
    return_value: Any = None
    stdout: str = ""
    exception_type: Optional[str] = None
    exception_msg: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GoldenSuite:
    """Collection of golden executions for a source module."""
    module_path: str
    records: List[GoldenExecution] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_path": self.module_path,
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoldenSuite":
        records = [GoldenExecution(**r) for r in data.get("records", [])]
        return cls(module_path=data["module_path"], records=records)


@dataclass
class ComparisonResult:
    """Outcome of comparing new execution against golden record."""
    passed: bool
    test_id: str
    function_name: str
    diff_reason: Optional[str] = None
    expected: Any = None
    actual: Any = None


class InputProposalGenerator:
    """Proposes edge-case arguments for function signatures."""

    @classmethod
    def propose_inputs_for_function(cls, func_name: str, args: List[str]) -> List[TestCase]:
        cases: List[TestCase] = []

        # Common test patterns based on function semantics
        if "average" in func_name or "math" in func_name or "scores" in func_name:
            cases.append(TestCase(f"{func_name}_empty", func_name, args=[[]]))
            cases.append(TestCase(f"{func_name}_integers", func_name, args=[[1, 2, 4]]))
            cases.append(TestCase(f"{func_name}_even_div", func_name, args=[[10, 20]]))
            cases.append(TestCase(f"{func_name}_truncation", func_name, args=[[5, 6]]))
            cases.append(TestCase(f"{func_name}_negatives", func_name, args=[[-7, 2]]))
        elif "series" in func_name or "range" in func_name:
            cases.append(TestCase(f"{func_name}_basic", func_name, args=[0, 5, 1]))
            cases.append(TestCase(f"{func_name}_step", func_name, args=[10, 4, 5]))
            cases.append(TestCase(f"{func_name}_zero_count", func_name, args=[1, 0, 1]))
        elif "banner" in func_name or "text" in func_name or "title" in func_name:
            cases.append(TestCase(f"{func_name}_standard", func_name, args=["Lift System"]))
            cases.append(TestCase(f"{func_name}_custom_fill", func_name, args=["Report", "*"]))
            cases.append(TestCase(f"{func_name}_empty", func_name, args=[""]))
        elif "hash" in func_name:
            cases.append(TestCase(f"{func_name}_ascii", func_name, args=["test_string", "salt1"]))
            cases.append(TestCase(f"{func_name}_empty_salt", func_name, args=["password", ""]))
        elif "sort" in func_name:
            cases.append(TestCase(f"{func_name}_numbers", func_name, args=[[5, 2, 9, 1]]))
            cases.append(TestCase(f"{func_name}_empty", func_name, args=[[]]))
        else:
            # Default fallback invocation
            cases.append(TestCase(f"{func_name}_default", func_name, args=[]))

        return cases


class CharacterizationRecorder:
    """Executes proposed inputs against legacy code and records ground truth."""

    def __init__(self, sandbox_runner: Optional[BaseSandboxRunner] = None):
        self.runner = sandbox_runner or create_sandbox_runner()

    def record_module(
        self,
        repo_root: str,
        module_rel_path: str,
        test_cases: List[TestCase],
    ) -> GoldenSuite:
        """Run each test case against the legacy module and capture outputs."""
        suite = GoldenSuite(module_path=module_rel_path)

        for case in test_cases:
            exec_record = self._execute_case(repo_root, module_rel_path, case)
            suite.records.append(exec_record)

        return suite

    def _execute_case(
        self,
        repo_root: str,
        module_rel_path: str,
        case: TestCase,
    ) -> GoldenExecution:
        # Generate inline execution harness script
        mod_name = module_rel_path[:-3].replace("/", ".").replace("\\", ".")
        harness_script = f"""
import sys
import os
import json
import io

# Ensure target repo root is importable
repo_dir = {json.dumps(os.path.abspath(repo_root))}
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

# Python 2 polyfills when executing under local Python 3 runner
try:
    import builtins
    if not hasattr(builtins, "xrange"):
        builtins.xrange = range
    if not hasattr(builtins, "long"):
        builtins.long = int
    if not hasattr(builtins, "unicode"):
        builtins.unicode = str
except Exception:
    pass

# Intercept stdout

captured_stdout = io.StringIO()
real_stdout = sys.stdout
sys.stdout = captured_stdout

result_payload = {{
    "return_value": None,
    "stdout": "",
    "exception_type": None,
    "exception_msg": None,
}}

try:
    import {mod_name} as target_module
    func = getattr(target_module, "{case.function_name}")
    args = json.loads({json.dumps(json.dumps(case.args))})
    kwargs = json.loads({json.dumps(json.dumps(case.kwargs))})
    ret = func(*args, **kwargs)
    result_payload["return_value"] = ret
except Exception as exc:
    result_payload["exception_type"] = type(exc).__name__
    result_payload["exception_msg"] = str(exc)
finally:
    sys.stdout = real_stdout
    result_payload["stdout"] = captured_stdout.getvalue()

print("__LIFT_JSON_START__")
print(json.dumps(result_payload))
print("__LIFT_JSON_END__")
"""

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(harness_script)
            temp_path = f.name

        try:
            # Run via sandbox
            limits = SandboxLimits(timeout_seconds=5.0)
            res = self.runner.run(
                image="local",
                cmd=[sys.executable, temp_path],
                mounts={repo_root: {"bind": "/workspace", "mode": "rw"}},
                limits=limits,
                working_dir=repo_root,
            )

            stdout = res.stdout
            if "__LIFT_JSON_START__" in stdout and "__LIFT_JSON_END__" in stdout:
                json_part = stdout.split("__LIFT_JSON_START__")[1].split("__LIFT_JSON_END__")[0].strip()
                data = json.loads(json_part)
                return GoldenExecution(
                    test_id=case.test_id,
                    function_name=case.function_name,
                    args=case.args,
                    kwargs=case.kwargs,
                    return_value=data.get("return_value"),
                    stdout=data.get("stdout", ""),
                    exception_type=data.get("exception_type"),
                    exception_msg=data.get("exception_msg"),
                )
            else:
                return GoldenExecution(
                    test_id=case.test_id,
                    function_name=case.function_name,
                    args=case.args,
                    kwargs=case.kwargs,
                    exception_type="ExecutionFailure",
                    exception_msg=res.stderr or "Harness output marker missing",
                )
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


class GoldenComparator:
    """Compares actual execution output against the golden characterization record."""

    @classmethod
    def compare(cls, golden: GoldenExecution, actual: GoldenExecution) -> ComparisonResult:
        # 1. Compare exceptions
        if golden.exception_type != actual.exception_type:
            return ComparisonResult(
                passed=False,
                test_id=golden.test_id,
                function_name=golden.function_name,
                diff_reason=f"Exception type mismatch: expected {golden.exception_type}, got {actual.exception_type}",
                expected=golden.exception_type,
                actual=actual.exception_type,
            )

        # 2. Compare return values (normalize int/float when exact, or float tolerances)
        if not cls._values_equal(golden.return_value, actual.return_value):
            return ComparisonResult(
                passed=False,
                test_id=golden.test_id,
                function_name=golden.function_name,
                diff_reason=f"Return value mismatch: expected {golden.return_value!r}, got {actual.return_value!r}",
                expected=golden.return_value,
                actual=actual.return_value,
            )

        # 3. Compare standard output (ignoring trailing whitespace)
        if golden.stdout.strip() != actual.stdout.strip():
            return ComparisonResult(
                passed=False,
                test_id=golden.test_id,
                function_name=golden.function_name,
                diff_reason="Standard output mismatch",
                expected=golden.stdout.strip(),
                actual=actual.stdout.strip(),
            )

        return ComparisonResult(
            passed=True,
            test_id=golden.test_id,
            function_name=golden.function_name,
        )

    @classmethod
    def _values_equal(cls, a: Any, b: Any) -> bool:
        if a == b:
            return True
        if isinstance(a, float) and isinstance(b, float):
            return abs(a - b) < 1e-6
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return False
            return all(cls._values_equal(x, y) for x, y in zip(a, b))
        return False
