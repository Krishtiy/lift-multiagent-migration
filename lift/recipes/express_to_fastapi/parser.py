"""JavaScript / Express route and handler AST-aware parser."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set


@dataclass
class ExpressRoute:
    """Structured representation of an Express route endpoint."""
    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # e.g. "/:id"
    fastapi_path: str  # e.g. "/{id}"
    middlewares: List[str] = field(default_factory=list)
    path_params: List[str] = field(default_factory=list)
    query_params: List[str] = field(default_factory=list)
    body_fields: List[str] = field(default_factory=list)
    status_codes: List[int] = field(default_factory=list)
    handler_snippet: str = ""


class ExpressParser:
    """Parses Express JavaScript source code to extract routes, parameters, and middleware chains."""

    # Regex detecting router.<method>('path', [middleware, ...] (req, res) => { ... })
    ROUTE_PATTERN = re.compile(
        r"(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*(['\"][^'\"]*['\"])\s*,",
        re.IGNORECASE,
    )

    @classmethod
    def parse_file(cls, file_path: str) -> List[ExpressRoute]:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        return cls.parse_code(content)

    @classmethod
    def parse_code(cls, source_code: str) -> List[ExpressRoute]:
        routes: List[ExpressRoute] = []

        # Find all router calls
        for match in cls.ROUTE_PATTERN.finditer(source_code):
            method = match.group(1).upper()
            raw_path = match.group(2).strip("'\"")
            start_pos = match.end()

            # Find matching closing bracket for this route definition
            handler_block = cls._extract_enclosing_block(source_code, start_pos)
            
            # Extract middlewares
            middlewares = cls._extract_middlewares(source_code[start_pos:start_pos + 120])

            # Convert Express :param to FastAPI {param}
            path_params = re.findall(r":([a-zA-Z0-9_]+)", raw_path)
            fastapi_path = re.sub(r":([a-zA-Z0-9_]+)", r"{\1}", raw_path)

            # Extract query parameters
            query_params = list(set(re.findall(r"req\.query\.([a-zA-Z0-9_]+)", handler_block)))

            # Extract body fields (e.g., const { username, email } = req.body; or req.body.username)
            body_fields = set(re.findall(r"req\.body\.([a-zA-Z0-9_]+)", handler_block))
            destructure_matches = re.findall(r"const\s*\{([^}]+)\}\s*=\s*req\.body", handler_block)
            for dm in destructure_matches:
                for f in dm.split(","):
                    field_name = f.strip().split(":")[0].strip()
                    if field_name:
                        body_fields.add(field_name)

            # Extract response status codes
            status_codes = [int(s) for s in re.findall(r"res\.status\((\d{3})\)", handler_block)]
            if not status_codes:
                status_codes = [201 if method == "POST" else 200]

            routes.append(
                ExpressRoute(
                    method=method,
                    path=raw_path,
                    fastapi_path=fastapi_path,
                    middlewares=middlewares,
                    path_params=path_params,
                    query_params=query_params,
                    body_fields=sorted(list(body_fields)),
                    status_codes=sorted(list(set(status_codes))),
                    handler_snippet=handler_block[:200],
                )
            )

        return routes

    @classmethod
    def _extract_middlewares(cls, prefix_text: str) -> List[str]:
        """Extract middleware identifier names preceding the (req, res) handler."""
        known_middlewares = ["authenticate", "requireAuth", "checkPermission", "verifyToken"]
        found = []
        for kw in known_middlewares:
            if re.search(r"\b" + kw + r"\b", prefix_text):
                found.append(kw)
        return found

    @classmethod
    def _extract_enclosing_block(cls, source: str, start_index: int) -> str:
        """Finds the full handler body text."""
        # Search ahead up to next router.<method> or 2000 chars
        end_match = cls.ROUTE_PATTERN.search(source, start_index)
        if end_match:
            return source[start_index:end_match.start()]
        return source[start_index:start_index + 2000]
