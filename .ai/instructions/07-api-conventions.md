# API Conventions

> Generic, blueprint-owned response shape. Lenticularis's actual error-code vocabulary, the
> handler chain, and the (inconsistent, documented) collection-response shapes live in
> `context/backend-notes.md` — read both.

## Philosophy

Standardizing API responses ensures that the frontend can handle both successes and errors in a predictable way. All API endpoints must follow these conventions for a consistent developer and user experience.

---

## Success Response

For single entities, return the object directly, as the Pydantic `response_model`. For a new
collection endpoint, prefer a bare `response_model=list[X]` unless the surrounding router already
uses an envelope — see `context/backend-notes.md` for which shape is in use where.

```json
{ "id": "123", "name": "Widget A", "created_at": "2024-01-01T00:00:00Z" }
```

---

## Error Response (Typed Envelope)

When an error occurs, the API must return a standardized error object in the shape below.

### Format
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message explaining the error.",
    "details": { "any": "extra context" }
  }
}
```

### Common Error Codes
- `AUTH_REQUIRED`: Session expired or not provided.
- `PERMISSION_DENIED`: User doesn't have the required role.
- `ENTITY_NOT_FOUND`: Resource with the given ID does not exist.
- `VALIDATION_FAILED`: Request payload is invalid (Pydantic error).
- `CONFLICT`: Resource already exists or version mismatch.
- `INTERNAL_ERROR`: Unexpected server-side failure.

See `context/backend-notes.md` for the complete, closed vocabulary this project actually emits.

### Example: Validation Error
```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Field 'email' must be a valid email address.",
    "details": { "field": "email", "value": "invalid-email" }
  }
}
```

---

## HTTP Status Codes

| Code | Use Case |
|---|---|
| **200 OK** | Successful read or update. |
| **201 Created** | Successful resource creation. |
| **400 Bad Request** | Validation failed or bad logic (e.g., negative amount). |
| **401 Unauthorized** | Missing or invalid authentication token. |
| **403 Forbidden** | User role is insufficient for this action. |
| **404 Not Found** | Resource ID is invalid or missing. |
| **409 Conflict** | Resource with this key already exists. |
| **500 Internal Error** | Database lock, logic bug, or unexpected exception. |

---

## Implementation (FastAPI)

Use a global exception handler to catch common errors and return the standardized JSON format.

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details
            }
        }
    )
```

See `context/backend-notes.md` for this project's actual `api/errors.py` / `AppException`
implementation and the full three-handler chain.
