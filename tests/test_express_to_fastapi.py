"""Unit tests for Phase 14 Express to FastAPI migration recipe."""

import ast
from pathlib import Path
import unittest

from lift.recipes.express_to_fastapi.parser import ExpressParser, ExpressRoute
from lift.recipes.express_to_fastapi.recorder import (
    HttpCharacterizationRecorder,
    HttpGoldenComparator,
    HttpRequestProposal,
    HttpResponseRecord,
)
from lift.recipes.express_to_fastapi.rules import ExpressToFastApiRulesPass


class TestExpressToFastApiRecipe(unittest.TestCase):
    def setUp(self):
        self.express_root = Path("benchmarks/express_app")
        self.users_js = self.express_root / "routes" / "users.js"
        self.products_js = self.express_root / "routes" / "products.js"

    def test_express_parser_extracts_users_routes(self):
        self.assertTrue(self.users_js.exists(), "Missing users.js benchmark file")
        routes = ExpressParser.parse_file(str(self.users_js))

        # Expecting GET /, GET /:id, POST /, DELETE /:id
        self.assertEqual(len(routes), 4)

        methods = [r.method for r in routes]
        self.assertIn("GET", methods)
        self.assertIn("POST", methods)
        self.assertIn("DELETE", methods)

        # Verify route conversion
        user_detail_route = next(r for r in routes if r.path == "/:id" and r.method == "GET")
        self.assertEqual(user_detail_route.fastapi_path, "/{id}")
        self.assertEqual(user_detail_route.path_params, ["id"])

        # Verify middleware detection on DELETE
        delete_route = next(r for r in routes if r.method == "DELETE")
        self.assertIn("authenticate", delete_route.middlewares)

        # Verify body field extraction on POST
        post_route = next(r for r in routes if r.method == "POST")
        self.assertIn("username", post_route.body_fields)
        self.assertIn("email", post_route.body_fields)

    def test_express_parser_extracts_products_routes(self):
        self.assertTrue(self.products_js.exists(), "Missing products.js benchmark file")
        routes = ExpressParser.parse_file(str(self.products_js))
        self.assertEqual(len(routes), 3)

        product_detail = next(r for r in routes if r.path == "/:sku")
        self.assertEqual(product_detail.fastapi_path, "/{sku}")
        self.assertEqual(product_detail.path_params, ["sku"])

        post_route = next(r for r in routes if r.method == "POST")
        self.assertIn("sku", post_route.body_fields)
        self.assertIn("name", post_route.body_fields)
        self.assertIn("price", post_route.body_fields)

    def test_deterministic_rules_pass_generates_valid_python_fastapi(self):
        users_code = self.users_js.read_text(encoding="utf-8")
        result = ExpressToFastApiRulesPass.apply_rules(users_code, module_name="users")

        self.assertGreater(result.routes_translated, 0)
        self.assertGreater(result.models_generated, 0)

        # Verify generated Python code compiles cleanly with Python AST parser
        try:
            parsed_ast = ast.parse(result.code)
            self.assertIsNotNone(parsed_ast)
        except SyntaxError as e:
            self.fail(f"Generated FastAPI code has SyntaxError: {e}\n\nCode:\n{result.code}")

        # Check key FastAPI and Pydantic primitives in generated code
        self.assertIn("APIRouter()", result.code)
        self.assertIn("BaseModel", result.code)
        self.assertIn("Depends(authenticate)", result.code)
        self.assertIn("@router.get('/{id}'", result.code)
        self.assertIn("@router.post('/', status_code=201", result.code)

    def test_http_probes_proposal(self):
        dummy_route = ExpressRoute(
            method="DELETE",
            path="/:id",
            fastapi_path="/{id}",
            middlewares=["authenticate"],
            path_params=["id"],
        )
        probes = HttpCharacterizationRecorder.propose_probes_for_route(dummy_route, base_path="/api/users")

        # Expect standard authenticated probe, unauthenticated 401 probe, and 404 missing probe
        self.assertGreaterEqual(len(probes), 3)

        # Probe 1: authenticated
        p1 = probes[0]
        self.assertEqual(p1.method, "DELETE")
        self.assertEqual(p1.path, "/api/users/1")
        self.assertIn("Authorization", p1.headers)

        # Probe 2: unauthenticated (expect 401)
        p2 = probes[1]
        self.assertEqual(p2.headers, {})

        # Probe 3: non-existent id (expect 404)
        p3 = probes[2]
        self.assertEqual(p3.path, "/api/users/99999")

    def test_http_golden_comparator(self):
        golden = HttpResponseRecord(
            status_code=200,
            json_body={"id": 1, "username": "alice", "role": "admin"},
        )
        exact_actual = HttpResponseRecord(
            status_code=200,
            json_body={"id": 1, "username": "alice", "role": "admin"},
        )
        res_exact = HttpGoldenComparator.compare(golden, exact_actual)
        self.assertTrue(res_exact.match)
        self.assertTrue(res_exact.status_match)
        self.assertTrue(res_exact.body_match)

        # Status mismatch
        error_actual = HttpResponseRecord(status_code=500, json_body={"error": "Server error"})
        res_error = HttpGoldenComparator.compare(golden, error_actual)
        self.assertFalse(res_error.match)
        self.assertFalse(res_error.status_match)

        # Missing key in response body
        missing_key_actual = HttpResponseRecord(status_code=200, json_body={"id": 1})
        res_missing = HttpGoldenComparator.compare(golden, missing_key_actual)
        self.assertFalse(res_missing.match)
        self.assertFalse(res_missing.body_match)
        self.assertIn("Missing key", res_missing.diff_report)


if __name__ == "__main__":
    unittest.main()
