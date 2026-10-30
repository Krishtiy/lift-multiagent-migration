"""HTTP characterization recorder and golden comparator for Express-to-FastAPI migrations."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Dict, List, Optional, Tuple

from lift.recipes.express_to_fastapi.parser import ExpressRoute


@dataclass
class HttpRequestProposal:
    """Proposed HTTP request probe for recording baseline behavior."""
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    json_body: Optional[Dict[str, Any]] = None


@dataclass
class HttpResponseRecord:
    """Recorded HTTP response snapshot."""
    status_code: int
    json_body: Optional[Any] = None
    text: str = ""


@dataclass
class HttpGoldenExecution:
    """Paired request probe and baseline response."""
    request: HttpRequestProposal
    response: HttpResponseRecord


@dataclass
class HttpGoldenSuite:
    """Collection of recorded HTTP golden test cases."""
    records: List[HttpGoldenExecution] = field(default_factory=list)


@dataclass
class HttpComparisonResult:
    """Outcome of comparing actual response against recorded golden baseline."""
    match: bool
    status_match: bool
    body_match: bool
    diff_report: str = ""


class HttpCharacterizationRecorder:
    """Proposes and records HTTP baseline interactions."""

    @classmethod
    def propose_probes_for_route(cls, route: ExpressRoute, base_path: str = "") -> List[HttpRequestProposal]:
        probes: List[HttpRequestProposal] = []
        full_path = f"{base_path.rstrip('/')}/{route.path.lstrip('/')}".rstrip('/')
        if not full_path:
            full_path = "/"

        # 1. Standard probe
        path_filled = full_path
        for pp in route.path_params:
            sample_val = "1" if "id" in pp.lower() else "SKU-001"
            path_filled = path_filled.replace(f":{pp}", sample_val).replace(f"{{{pp}}}", sample_val)

        headers = {}
        if "authenticate" in route.middlewares:
            headers["Authorization"] = "Bearer secret-token-123"

        body = None
        if route.body_fields:
            body = {}
            for bf in route.body_fields:
                if "email" in bf:
                    body[bf] = "test@example.com"
                elif "price" in bf:
                    body[bf] = 19.99
                elif "stock" in bf:
                    body[bf] = 20
                else:
                    body[bf] = f"sample_{bf}"

        probes.append(
            HttpRequestProposal(
                method=route.method,
                path=path_filled,
                headers=headers,
                json_body=body,
            )
        )

        # 2. If authentication is required, probe without auth to record 401 behavior
        if "authenticate" in route.middlewares:
            probes.append(
                HttpRequestProposal(
                    method=route.method,
                    path=path_filled,
                    headers={},
                    json_body=body,
                )
            )

        # 3. If path params exist, probe non-existent item to record 404 behavior
        if route.path_params:
            path_404 = full_path
            for pp in route.path_params:
                sample_val = "99999" if "id" in pp.lower() else "SKU-99999"
                path_404 = path_404.replace(f":{pp}", sample_val).replace(f"{{{pp}}}", sample_val)
            probes.append(
                HttpRequestProposal(
                    method=route.method,
                    path=path_404,
                    headers=headers,
                    json_body=body,
                )
            )

        return probes


class HttpGoldenComparator:
    """Verifies semantic equivalence of HTTP responses."""

    @classmethod
    def compare(
        cls,
        golden: HttpResponseRecord,
        actual: HttpResponseRecord,
    ) -> HttpComparisonResult:
        status_match = (golden.status_code == actual.status_code)
        
        # Check body structure
        body_match = True
        diff_msgs = []

        if not status_match:
            diff_msgs.append(
                f"Status code mismatch: expected {golden.status_code}, got {actual.status_code}"
            )

        if golden.json_body is not None and actual.json_body is not None:
            if isinstance(golden.json_body, dict) and isinstance(actual.json_body, dict):
                # Verify expected keys are present
                for key in golden.json_body.keys():
                    if key not in actual.json_body:
                        body_match = False
                        diff_msgs.append(f"Missing key in response JSON: '{key}'")
            elif type(golden.json_body) != type(actual.json_body):
                body_match = False
                diff_msgs.append(
                    f"Response JSON type mismatch: expected {type(golden.json_body).__name__}, got {type(actual.json_body).__name__}"
                )
        elif golden.json_body is not None and actual.json_body is None:
            body_match = False
            diff_msgs.append("Expected JSON body, but received non-JSON or empty response")

        overall_match = status_match and body_match
        return HttpComparisonResult(
            match=overall_match,
            status_match=status_match,
            body_match=body_match,
            diff_report="; ".join(diff_msgs) if diff_msgs else "Equivalent",
        )
