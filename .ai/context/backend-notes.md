# Backend Notes — Project-Specific

> Companion to `instructions/02-backend-conventions.md`. That file holds the generic,
> blueprint-owned patterns; this file holds Lenticularis's own instantiation of them —
> the actual role model, error vocabulary, and collector rules. Update this file freely;
> it is never touched by `update-blueprint.md`.

---

## Auth Dependencies

Import from `lenticularis.api.dependencies`:

| Dependency | Who passes |
|---|---|
| `get_current_user` | Any user with a valid token **and `is_active`**; 401 otherwise |
| `get_current_user_optional` | Same, but returns `None` instead of raising. For endpoints with both a public and an authenticated view. **Does not check `is_active`** — see the caveat below |
| `require_pilot` | **Denylist, not an allowlist** — rejects `customer` and `org_pilot` only. `pilot`, `admin`, *and* `org_admin` all pass. Guards write operations |
| `require_admin` | `role == "admin"` only |
| `require_org_admin` | `admin` bypasses; otherwise `org_admin` **with `org_id` set** |
| `require_org_member` | `admin` bypasses; otherwise `org_admin` or `org_pilot` **with `org_id` set** |

System `admin` bypasses both org guards. All rejections are 403 except `get_current_user`'s 401.

> **Caveat:** `get_current_user_optional` resolves the user without an `is_active` check, so a
> deactivated account still returns a `User` there while `get_current_user` 401s. Do not use it to
> guard anything that depends on the account being live.

---

## Success Response Shapes

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

## Error Responses

All errors leave the app as `{"error": {"code", "message", "details"}}`. Handlers in `api/main.py`
enforce this for `AppException`, `HTTPException`, and `RequestValidationError` alike.

> This is the project's own envelope — **not** [RFC 7807](https://datatracker.ietf.org/doc/html/rfc7807),
> which uses `type`/`title`/`status`/`detail`/`instance` under `application/problem+json`. Parts of the
> codebase and older docs call it "RFC 7807"; that label is wrong and is being retired. Do not
> reintroduce it.

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

Raise `AppException` from `api/errors.py` when you need a specific code or structured `details`:

```python
from lenticularis.api.errors import AppException

raise AppException(404, "ENTITY_NOT_FOUND", "Station not found", {"station_id": station_id})
```

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

**Current state:** every router raises `HTTPException`, not `AppException`, so codes are
status-derived in practice. That is acceptable — the envelope holds either way. Reach for
`AppException` only when the status alone does not identify the failure, or the frontend needs
`details`. See `context/security-notes.md` (T12) for the full rule.

---

## Batch Before Looping — canonical example

Never call `influx.query_latest(station_id)` in a per-station loop. Use
`query_latest_for_stations(list[str])` once. See `rules/evaluator.py` for the canonical pattern
this project follows.

---

## Collector Conventions

New collector checklist:
- Subclass `BaseCollector` from `collectors/base.py`.
- Import `to_float` and `normalize_wind_dir` from `collectors/utils.py` — never redefine local copies.
- Use `self._collect_concurrent(items, fn, limit=8)` for bounded parallel fetches (wraps asyncio gather with a semaphore).
- Log every fetch with elapsed time and result count.
- Use `asyncio.to_thread()` for any synchronous write to InfluxDB.
- Register the class in `_COLLECTOR_REGISTRY` in `scheduler.py`, add a config block to
  `config.yml.example`, and add the network to `NETWORK_PRIORITY` in `services/dedup.py`.
- **Normalise units to the unified schema.** `WeatherMeasurement` is km/h, °C, %, hPa. Convert at
  the collector boundary (e.g. `jfb.py` multiplies knots by 1.852) — never store a foreign unit.
- **Map only what the schema already holds.** If a source field has no `WeatherMeasurement` field,
  drop it — do not widen the model to fit one source. `jfb.py` drops `TD`/`DIFFTD` (derivable from
  temperature + humidity) and `G1h` (1-hour gust ≠ `wind_gust`, which is the 10-min peak everywhere
  else — storing it there would silently break cross-network comparability).
- **Never synthesise a field the source does not measure.** JFB reports QFE only; `pressure_qff` is
  left `None`, because QFF is not derivable from QFE + elevation (that is QNH) and a fake value
  would corrupt the föhn pressure-gradient comparison. `fga.py` does the same.
- **Guard against stale data.** Skip and `WARNING` any reading older than ~2 h. Some APIs return
  hours-old data with a `200 OK` and no error (see the `currentDateTime` note in `jfb.py`).
