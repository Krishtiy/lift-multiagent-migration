# Express to FastAPI Migration Gotchas Catalog

This catalog defines the intentional legacy Express patterns planted across the `express_app` benchmark service. Each gotcha has a unique ID, description, Express behavior, and FastAPI failure/drift mode.

| Gotcha ID | Category | Description | Express Behavior | FastAPI Target Pattern | Difficulty |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **E-01** | Routing | Express `:param` path syntax | Express uses `:userId` or `:productId` | FastAPI uses `{user_id}` or `{product_id}` with typed arguments | **Easy** |
| **E-02** | Response | `res.status(c).json(data)` | Method chaining on response object | Route decorator `status_code=c` and direct dictionary/model return | **Easy** |
| **E-03** | Types | Untyped query parameters | `req.query.limit` returns string `"10"` | FastAPI typed query parameter `limit: int = 10` | **Easy** |
| **E-04** | Auth/Ctx | Attached `req.user` in middleware | Middleware mutates `req.user = { id: 1 }` | FastAPI dependency injection `Depends(get_current_user)` | **Medium** |
| **E-05** | Error Handling | Missing `return` after `res.send()` | Code continues execution unless explicitly returned | FastAPI raises `HTTPException(status_code=400, detail=...)` | **Medium** |
| **E-06** | Body Validation | Manual payload validation | `if (!req.body.name) return res.status(400)...` | Pydantic model validation with automatic 422 Unprocessable Entity | **Medium** |
| **E-07** | Async/Callbacks | Uncaught async errors or callback hell | `db.find((err, data) => ...)` | Python async/await native coroutines with clean exception propagation | **Hard** |
| **E-08** | Header Parsing | Lowercase header indexing | `req.headers['authorization']` | FastAPI `Header(alias="Authorization")` or `Security(HTTPBearer())` | **Hard** |
