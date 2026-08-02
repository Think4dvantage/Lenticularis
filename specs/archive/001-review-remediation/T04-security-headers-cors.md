# T04 — Add security-header + CORS middleware

**Severity:** Medium · **Phase:** 1 · **Model tier:** Moderate

## Ground Rules (read before editing)
- Read `.ai/instructions/04-constraints.md`, `08-operability.md`. LF line endings only.
- No new dependencies (CORS/GZip middleware ship with FastAPI/Starlette). Implement exactly this task.

## Problem
`create_app()` in `src/lenticularis/api/main.py` registers **no middleware**: no CORS, no security
headers, no clickjacking/MIME-sniffing protection. With tokens in `localStorage` and no CSP, any XSS
(see T03) is unconstrained.

## Fix
In `src/lenticularis/api/main.py`, inside `create_app()` after `app = FastAPI(...)` and before the
routers are included, add a small response-header middleware plus CORS.

```python
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware

# ... inside create_app(), after `app = FastAPI(...)`:

async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Conservative CSP: same-origin by default; allows the CDN libs the pages load
    # (Leaflet, Chart.js) and inline scripts the no-build pages rely on.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
        "connect-src 'self'; frame-ancestors 'none'",
    )
    return resp

app.add_middleware(BaseHTTPMiddleware, dispatch=_security_headers)
```

CORS: this app is same-origin (frontend served by the same FastAPI app), so the safe default is to
**not** open CORS. If the Flutter app / a separate origin must call the API later, add an explicit
allowlist driven by config — never `allow_origins=["*"]` with credentials. For now, add CORS only if
a cross-origin client already exists; otherwise add a commented stub:
```python
# Cross-origin clients (e.g. the mobile app) — add explicit origins here, never "*":
# app.add_middleware(CORSMiddleware, allow_origins=["https://lenti.cloud"],
#                    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
```

> Verify the actual CDN hostnames the HTML pages use (grep `static/*.html` for `unpkg`, `cdn.`,
> `jsdelivr`) and align the CSP `script-src`/`style-src` with them. If a page breaks because a CDN
> host is missing from the CSP, add that exact host — do not fall back to `*`.

## Acceptance criteria
- Every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and a
  `Content-Security-Policy` header (check with `curl -I`).
- All pages still load: map (Leaflet), charts (Chart.js), and the inline `<script type="module">`
  blocks run with no CSP violations in the browser console.
- No CORS header is emitted for now (or only the explicit allowlist if one was required).
