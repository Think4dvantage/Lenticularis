# API Conventions

## Philosophy

Standardizing API responses ensures that the frontend can handle both successes and errors in a predictable way. All API endpoints must follow these conventions for a consistent developer and user experience.

---

## Success Response

Single entities are returned directly, as the Pydantic `response_model`:

```json
{ "id": "123", "name": "Widget A", "created_at": "2024-01-01T00:00:00Z" }
```

**Collections have no single house style — this is a known inconsistency, not a rule.** Three shapes
are in use today:

| Shape | Where | Example |
|---|---|---|
| **Bare JSON array** (most common) | `response_model=list[X]` | `GET /api/rulesets`, `/gallery`, `/presets` → `[{...}, {...}]` |
| `data` + endpoint-specific metadata | Composite/computed payloads | `/api/stations/replay` → `{start, end, station_count, obs_frame_count, data}`; `/{id}/history` → `{station_id, hours, count, data}` |
| `data` + `total` | `admin.py` only | `{data: [...], total: N}` |

Match the surrounding router rather than imposing a new shape. **Do not** retrofit an envelope onto
an endpoint that returns a bare array — the frontend parses these shapes as they are, and changing
one is a breaking change. Prefer a bare `response_model=list[X]` for a plain new collection.

> The error envelope below **is** uniform and is enforced globally. Only the success shape varies.

---

## Error Response (Typed Envelope)

When an error occurs, the API must return a standardized error object in the shape below.

> This is the project's own envelope — **not** [RFC 7807](https://datatracker.ietf.org/doc/html/rfc7807),
> which uses `type`/`title`/`status`/`detail`/`instance` under `application/problem+json`. Parts of the
> codebase and older docs call it "RFC 7807"; that label is wrong and is being retired. Do not
> reintroduce it.

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

### Error Codes

This is the complete vocabulary — `_STATUS_TO_CODE` in `main.py` emits nothing else.

| Code | Status | Meaning |
|---|---|---|
| `VALIDATION_FAILED` | 400, 422 | Request payload invalid (also every Pydantic `RequestValidationError`) |
| `AUTH_REQUIRED` | 401 | Session expired or not provided |
| `PERMISSION_DENIED` | 403 | User lacks the required role |
| `ENTITY_NOT_FOUND` | 404 | Resource with the given ID does not exist |
| `CONFLICT` | 409 | Resource already exists or version mismatch |
| `INTERNAL_ERROR` | ≥500 | Unexpected server-side failure |
| `ERROR` | any other 4xx | Fallback for an unmapped non-5xx status (e.g. 429). Avoid relying on it — raise `AppException` with a real code instead |

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

`api/errors.py` defines `AppException` and the `_envelope(code, message, details)` helper.
`create_app()` in `api/main.py` registers **three** handlers, all emitting that envelope:

| Handler | Code source |
|---|---|
| `AppException` | `exc.code` verbatim — the only way to set a specific code |
| `HTTPException` | Derived from status via `_STATUS_TO_CODE`; unmapped ≥500 → `INTERNAL_ERROR`, else `ERROR` |
| `RequestValidationError` | Always 422 `VALIDATION_FAILED`, with `{"errors": exc.errors()}` as details |

`_STATUS_TO_CODE` maps 400→`VALIDATION_FAILED`, 401→`AUTH_REQUIRED`, 403→`PERMISSION_DENIED`,
404→`ENTITY_NOT_FOUND`, 409→`CONFLICT`. Both `AppException` and `HTTPException` handlers log at
`ERROR` when the status is ≥500.

```python
from lenticularis.api.errors import AppException

raise AppException(404, "ENTITY_NOT_FOUND", "Station not found", {"station_id": station_id})
```

**Current state:** every router raises `HTTPException`, not `AppException`, so codes are
status-derived in practice. That is acceptable — the envelope holds either way. Reach for
`AppException` when the status alone does not identify the failure, or the frontend needs `details`.
