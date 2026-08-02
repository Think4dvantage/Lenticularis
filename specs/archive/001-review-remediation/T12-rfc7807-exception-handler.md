# T12 — Global exception handler + standardized error envelope

**Severity:** High (largest convention gap) · **Phase:** 3 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/07-api-conventions.md` (the contract this implements) and `08-operability.md`.
- LF line endings only. Exactly this task. Do **not** rewrite every router's success payloads —
  this task only standardizes **errors** plus adds the reusable exception type.

## Problem
`07-api-conventions.md` mandates an RFC7807-style error envelope and a global `AppException`
handler. None exists: all ~78 error sites raise raw `HTTPException`, producing FastAPI's default
`{"detail": "..."}` — matching neither the documented shape nor the error-code vocabulary.

## Fix

### 1. New module `src/lenticularis/api/errors.py`
```python
from __future__ import annotations
from fastapi import Request
from fastapi.responses import JSONResponse

class AppException(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

def _envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}
```

### 2. Register handlers in `src/lenticularis/api/main.py` (`create_app()`)
Add handlers that map to the documented envelope, and translate the existing `HTTPException`s and
validation errors so the whole API is consistent without touching every raise site:
```python
import logging
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from lenticularis.api.errors import AppException, _envelope

_log = logging.getLogger(__name__)

_STATUS_CODE = {400: "VALIDATION_FAILED", 401: "AUTH_REQUIRED", 403: "PERMISSION_DENIED",
                404: "ENTITY_NOT_FOUND", 409: "CONFLICT"}

@app.exception_handler(AppException)
async def _app_exc(request: Request, exc: AppException):
    if exc.status_code >= 500:
        _log.error("%s %s → %s %s", request.method, request.url.path, exc.code, exc.message)
    return JSONResponse(exc.status_code, _envelope(exc.code, exc.message, exc.details))

@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    code = _STATUS_CODE.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "ERROR")
    return JSONResponse(exc.status_code, _envelope(code, str(exc.detail)), headers=exc.headers)

@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    return JSONResponse(422, _envelope("VALIDATION_FAILED", "Request validation failed",
                                       {"errors": exc.errors()}))
```
> `JSONResponse(status_code, content)` positional order: pass `status_code=` and `content=`
> explicitly if unsure. Verify the signature when you write it.

### 3. Adopt `AppException` going forward
Do not mass-rewrite existing raises. Just make new code raise `AppException` with a documented
`code` from `07-api-conventions.md` (`AUTH_REQUIRED`, `PERMISSION_DENIED`, `ENTITY_NOT_FOUND`,
`VALIDATION_FAILED`, `CONFLICT`, `INTERNAL_ERROR`). Optionally convert a couple of the highest-traffic
raises (e.g. the 404s in `stations.py`) as examples.

## Acceptance criteria
- A 404 (`/api/stations/does-not-exist`) returns `{"error": {"code": "ENTITY_NOT_FOUND", ...}}`,
  not `{"detail": "..."}`.
- A validation error (bad query param) returns the `VALIDATION_FAILED` envelope with status 422.
- 5xx errors are logged at ERROR and do not leak stack traces to the client.
- Existing endpoints still return their normal success payloads (unchanged).
