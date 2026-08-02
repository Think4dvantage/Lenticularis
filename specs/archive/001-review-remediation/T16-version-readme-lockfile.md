# T16 — Single-source the version, fix README casing, commit poetry.lock

**Severity:** Medium · **Phase:** 3 · **Model tier:** Trivial

## Ground Rules
- LF line endings only. Exactly this task.

## Problems
1. The version is stated three ways: `pyproject.toml` `version = "1.11.0"`,
   `create_app(version="0.1.0")` in `src/lenticularis/api/main.py` (what `/docs`/OpenAPI advertises),
   and `features.md` (v1.17/v1.18). The runtime serves a hardcoded `0.1.0`.
2. `pyproject.toml` sets `readme = "README.md"` but the tracked file is lowercase `readme.md`.
   On a case-sensitive filesystem (Linux container / CI) the build can't find it.
3. `poetry.lock` is in `.gitignore` → builds are not reproducible.

## Fix

### Version — one source of truth
- Bump `pyproject.toml` `version` to the true current release (set it to `"1.17.0"` to match the
  latest git tag/`features.md`; confirm the intended number — if v1.18 has shipped by the time you
  run this, use that).
- Make the runtime read the package version instead of a literal. In `main.py`:
  ```python
  from importlib.metadata import version, PackageNotFoundError
  try:
      _APP_VERSION = version("lenticularis")
  except PackageNotFoundError:
      _APP_VERSION = "0.0.0+dev"
  ```
  Use `version=_APP_VERSION` in `create_app(... version=_APP_VERSION ...)` and in the no-static
  fallback `root()` return (`{"status": "ok", "version": _APP_VERSION}`).
- If `/api/health` (or `health` router) reports a version, point it at `_APP_VERSION` too. The
  operability doc wants the real version logged at startup — add/confirm an INFO line in `lifespan`:
  `logger.info("Lenticularis starting — version %s", _APP_VERSION)`.

### README casing
- Rename `readme.md` → `README.md` (use `git mv readme.md README.md` so history is preserved).
  Confirm `pyproject.toml` `readme = "README.md"` now matches.

### Commit the lockfile
- Remove the `poetry.lock` line from `.gitignore`.
- In your completion summary, tell the human to run `poetry lock` (if missing) and `git add poetry.lock`
  — the model should not fabricate a lockfile; just un-ignore it.

## Acceptance criteria
- `GET /` (no-static fallback) and `/docs` show the real version, not `0.1.0`.
- Startup log prints the real version.
- A case-sensitive checkout finds `README.md`; `pyproject` matches.
- `poetry.lock` is no longer gitignored.
