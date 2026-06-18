# T03 — Validate webcam URL scheme + escape frontend XSS sinks

**Severity:** High · **Phase:** 1 · **Model tier:** Moderate

## Ground Rules (read before editing)
- Read `.ai/instructions/03-frontend-conventions.md`, `04-constraints.md`.
- LF line endings only. No build step / npm. Frontend logging uses `console.*` with a
  `[Lenti:<page>]` prefix. Implement exactly this task.

## Problem
1. **Stored XSS via `javascript:` webcam URL.** `src/lenticularis/models/rules.py` types the webcam
   URL as a bare string (`class WebcamBase: url: str`), and `static/ruleset-analysis.html` renders it
   as an `href`. `esc()` there escapes `&<>"'` but does not block the `javascript:`/`data:` scheme.
   A pilot can set a webcam URL to `javascript:…`, mark the ruleset public, and steal the token of
   anyone who clicks it (tokens live in `localStorage`).
2. **Unescaped station name.** `static/forecast-analysis.js` renders `entry.name` into a table cell
   without escaping, while every other cell is escaped. A crafted station/site name executes script.

## Fix

### Backend — reject non-http(s) webcam URLs
In `src/lenticularis/models/rules.py`, change `WebcamBase.url` from `str` to a validated HTTP URL.
Use Pydantic v2:
```python
from pydantic import BaseModel, Field, field_validator

class WebcamBase(BaseModel):
    url: str
    label: Optional[str] = None
    sort_order: int = 0

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = (v or "").strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Webcam URL must start with http:// or https://")
        return v
```
(Using `HttpUrl` is also acceptable, but it changes the serialized type to a URL object; the
validator above keeps `url` a plain string and is the lower-risk change. Pick one, not both.)

### Frontend — block the scheme at render time + escape the name
In `static/ruleset-analysis.html`, before using a webcam URL in `href`, guard the scheme. Add a
helper near `esc()`:
```javascript
function safeUrl(u) {
  const s = String(u || '').trim();
  return /^https?:\/\//i.test(s) ? s : '#';
}
```
and use `href="${safeUrl(w.url)}"` instead of `href="${esc(w.url)}"`.

In `static/forecast-analysis.js`, wrap the station name in the existing HTML-escape helper. Find the
row template that emits `${entry.name || entry.station_id}` and change it to
`${escHtml(entry.name || entry.station_id)}` (the file already has an escaping helper used for other
cells — reuse it; if it is named differently, use that name).

## Acceptance criteria
- `POST`/`PUT` of a ruleset webcam with `url: "javascript:alert(1)"` returns **422** (validation error).
- An existing `javascript:` URL already in the DB renders as a dead `#` link, not an executable one.
- A station named `<img src=x onerror=alert(1)>` shows as literal text on `/forecast-analysis`,
  no script runs.
- Valid `http(s)` webcam URLs still save and render as working links.
