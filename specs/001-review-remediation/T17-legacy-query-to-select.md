# T17 — Convert legacy SQLAlchemy query() to 2.0 select()

**Severity:** Medium · **Phase:** 3 · **Model tier:** Trivial

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md` ("SQLAlchemy 2.0 style — use select(), not legacy
  query()"). LF line endings only. Exactly this task — same results, no logic change.

## Problem
Two routers still use the legacy `db.query(...)` API:
- `src/lenticularis/api/routers/auth.py`: lines ~68, ~86, ~168, ~176
  (`db.query(User).filter(...).first()`, `db.query(OAuthIdentity).filter_by(...).first()`)
- `src/lenticularis/api/routers/rulesets.py`: lines ~478, ~512
  (`db.query(LaunchLandingLink)...`, `db.query(RuleSetWebcam)...`)

## Fix
Convert each to `select()` + `db.execute(...).scalars()`. Patterns:
```python
# before
user = db.query(User).filter(User.email == body.email).first()
# after
from sqlalchemy import select
user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
```
```python
# before
identity = db.query(OAuthIdentity).filter_by(provider=provider, provider_user_id=pid).first()
# after
identity = db.execute(
    select(OAuthIdentity).where(OAuthIdentity.provider == provider,
                                OAuthIdentity.provider_user_id == pid)
).scalar_one_or_none()
```
For list results use `.scalars().all()`. Ensure `from sqlalchemy import select` is imported in each
file (add if missing). Do not change surrounding logic, ordering, or return values.

## Acceptance criteria
- No `db.query(` remains in `auth.py` or `rulesets.py` (grep is clean).
- Register, login, refresh, OAuth upsert, and ruleset landing/webcam paths behave exactly as before.
