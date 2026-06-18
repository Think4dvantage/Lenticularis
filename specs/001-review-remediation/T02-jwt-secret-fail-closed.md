# T02 — Fail closed on a weak/placeholder JWT secret

**Severity:** High · **Phase:** 1 · **Model tier:** Moderate

## Ground Rules (read before editing)
- Read `.ai/instructions/04-constraints.md`, `08-operability.md`.
- LF line endings only. No new dependencies. No `print()`. No `os.environ` (use `get_config()`).
- Implement exactly this task. Verify with Acceptance Criteria before reporting done.

## Problem
`src/lenticularis/config.py` defaults the JWT secret to a publicly known string:
```python
class AuthConfig(BaseModel):
    jwt_secret: str = "change-me-in-production"
```
If `config.yml` omits `auth.jwt_secret` (or keeps a placeholder), the app boots silently with a
guessable secret. Anyone who knows it can forge an `admin` token. The doctrine (`08-operability.md`)
says a missing/invalid required key must fail fast at `CRITICAL`, never fall back to a magic default.

## Fix
Add a startup validation that aborts the boot when the secret is unset, empty, too short, or a
known placeholder. Put the check in the `lifespan` startup in `src/lenticularis/api/main.py`,
immediately after `cfg = get_config()` and `_configure_logging(cfg)`:
```python
_PLACEHOLDER_SECRETS = {
    "change-me-in-production",
    "change-me-in-production-use-openssl-rand-hex-32",
    "dev-secret-change-in-production",
    "",
}
_secret = cfg.auth.jwt_secret
if _secret in _PLACEHOLDER_SECRETS or len(_secret) < 32:
    logger.critical(
        "auth.jwt_secret is unset, a known placeholder, or shorter than 32 chars — refusing to start. "
        "Generate one with: openssl rand -hex 32"
    )
    raise RuntimeError("Insecure auth.jwt_secret — see logs")
```
(Place the constant at module level, and emit the `logger.critical` before raising so the reason
is in the logs.)

Also remove the insecure default so a missing key is a validation error rather than a silent
fallback — in `src/lenticularis/config.py` change:
```python
    jwt_secret: str = "change-me-in-production"
```
to (no default — required field):
```python
    jwt_secret: str
```
Keep `jwt_algorithm`, expiry fields unchanged.

## Acceptance criteria
- Booting with `auth.jwt_secret` set to any placeholder or `< 32` chars logs a `CRITICAL` line and
  the process exits (does not serve traffic).
- Booting with a real ≥32-char secret starts normally and login still issues working tokens.
- `config.yml.example` still documents the key (it already ships a placeholder + the `openssl`
  hint — leave the example as-is; it is not loaded at runtime).
