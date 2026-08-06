# Constraints — What NOT to Do

> Generic, blueprint-owned hard rules. The fixed-bug history behind the Security/Performance/
> Error-Handling rules below — real incidents, with root causes — lives in
> `context/security-notes.md`. Read both.

## AI Files

**All AI-related content lives exclusively in `.ai/`.** Never create tool-specific instruction files such as `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`, or any equivalent — not even as thin pointers. Instructions, context, prompts, and plans all go in `.ai/` and nowhere else.

---

## Production

**Never touch prod directly.** All production changes go through the IaC repo. No direct SSH, no direct `docker-compose` on the prod host.

---

## Frontend

**Never add npm or a build step.** The frontend is intentionally dependency-free. No webpack, vite, rollup, parcel, or any bundler. No `package.json`.

**Never load a library from a CDN.** See `context/frontend-notes.md` for where vendored libraries
live and the exact caching/version-bump mechanism.

---

## Secrets

**Never commit secrets.** `config.yml` and `.env` are gitignored. Only `config.yml.example` (with placeholder values) is committed.

---

## Database Migrations

**No Alembic. No `.sql` migration files. No `_migrations` table.**

All schema changes use raw `ALTER TABLE` statements guarded by `PRAGMA table_info()` inside `_run_column_migrations()` in `database/db.py`. `Base.metadata.create_all()` handles the initial schema at startup — it is idempotent. See `02-backend-conventions.md` for the exact pattern.

---

## i18n

**Never hardcode user-visible strings in JS** without a corresponding key in all locale files. All locales must be updated simultaneously — see `03-frontend-conventions.md` for the current list.

---

## Code Quality

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't use feature flags or backwards-compatibility shims when you can just change the code.

---

## Architecture

- No Alembic — schema migrations are done with raw `ALTER TABLE` in `_run_column_migrations()`.
- No print statements in production code — use the standard `logging` module.
- Never read `os.environ` directly — always go through `get_config()`.
- Never put page routes in `main.py` — they go in `api/routers/pages.py`.
- Never monkey-patch `scheduler` attributes — use `scheduler.on_forecast_run` / `scheduler.on_collector_run` hooks set in the lifespan.

---

## Dependencies

**Never let the Dockerfile's lockfile `COPY` fall back silently to a fresh resolve.** Use
`COPY pyproject.toml poetry.lock ./` (the literal filename), never a glob like `poetry.lock*` that
succeeds even when the file is missing. A missing lock must fail the build loudly — silently
re-resolving lets dependency versions drift under a fixed image tag. Also drop any `poetry lock`
step that runs before `poetry install` — once the `COPY` is strict, that step exists only to
re-resolve a stale lock, which is the same drift risk in a different guise.

---

## Security

**Never interpolate a user-supplied value directly into a query string** (SQL, Flux, or any
query language built by string formatting). Validate with an allowlist regex first, then
interpolate the validated value.

**The app must refuse to start if a secret (JWT signing key, API key) is empty, too short, or a
known placeholder value.** Fail closed at startup — never fall back to a default secret in any
deployed environment.

**Never assign untrusted data to `element.innerHTML`, `element.outerHTML`, or `document.write()`**
in frontend JS. Use `element.textContent` for plain text. If markup must be rendered, sanitize it
first against a known-safe allowlist of tags/attributes — never trust it raw.

**Never put an access or refresh token in a URL** — query param, hash fragment, or redirect
target. URLs get logged (proxies, browser history, referrer headers). Tokens belong in
`localStorage` or an HttpOnly cookie, set via a POST response body, never via a redirect URL.

See `context/security-notes.md` for the specific past incidents (T01–T05) these rules trace back
to, including the exact validation regex and OAuth `email_verified` check this project uses.

---

## Performance

**Never call blocking synchronous I/O inside `async def` without `asyncio.to_thread()`.** This
includes any synchronous DB/query client used from an async handler or scheduler job — it blocks
the entire event loop and stalls every concurrent request.

**Never loop over items to fetch data one at a time when a batch method exists.** Use a
`query_x_for_stations(ids)`-style batch call once instead of a single-ID call per iteration.

**Every module-level cache dict must have a maximum size.** An unbounded cache eventually OOMs
the process. Bound it and evict (LRU or oldest-first) when full.

See `context/security-notes.md` (T07–T10) for the specific incidents and this project's cache
eviction pattern.

---

## Error Handling

**Never silence an exception in a background task, scheduler job, or async callback.** A
swallowed exception makes a job stop doing its work with no visible signal — log with
`logger.exception()` and re-raise (or let it propagate), never `pass` or log-and-continue.

**Every error response must leave the app as `{"error": {"code", "message", "details"}}`.** See
`07-api-conventions.md` for the format and `context/security-notes.md` (T12, T18–T19) for the
current implementation and this project's InfluxDB write-integrity rules.
