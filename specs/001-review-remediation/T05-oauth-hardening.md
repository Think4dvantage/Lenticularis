# T05 — OAuth hardening: tokens out of URL, verified-email link, bound state

**Severity:** Medium · **Phase:** 1 · **Model tier:** Moderate (multi-file)

## Ground Rules (read before editing)
- Read `.ai/instructions/04-constraints.md`, `03-frontend-conventions.md`. LF line endings only.
- Implement exactly this task. Verify with Acceptance Criteria.

## Problems (in `src/lenticularis/api/routers/auth.py`)
1. **Tokens in the URL.** `_build_success_redirect()` puts `access_token` + `refresh_token` in the
   `/oauth-callback?…` query string. URLs leak via Referer, history, and logs.
2. **Auto-link without verified email.** `_upsert_oauth_user()` links a Google identity to any
   existing local account with the same email, and never checks Google's `email_verified`.
3. **Floating CSRF state.** `_make_state()` / `_verify_state()` prove only that the server issued
   *some* state in the last 10 min — it is not bound to the user's browser, enabling login-CSRF.

## Fix

### 1. Deliver tokens via a short-lived one-time code (not the URL)
- Add a module-level dict `_oauth_handoff: dict[str, tuple[str, str, str, float]] = {}` keyed by a
  random code → `(access, refresh, user_json_b64, created_monotonic)`.
- In the callback success path, generate `code = secrets.token_urlsafe(32)`, store the tuple, and
  redirect to `/oauth-callback?code=<code>` (no tokens in the URL).
- Add `POST /api/auth/oauth-exchange` that accepts `{code}`, pops the entry if present and `< 60 s`
  old, and returns the `Token` JSON. Reject unknown/expired codes with 400.
- Update `static/oauth-callback.html` to read `code` from the URL, `POST` it to
  `/api/auth/oauth-exchange`, store the returned tokens (same place the current code stores them),
  then `history.replaceState` to strip the query string. Add `[Lenti:oauth]` console logging.

### 2. Require verified email + stop silent cross-account linking
- In `google_callback`, read `email_verified` from the userinfo response; if it is not `true`,
  redirect to `/login?error=google_email_unverified`.
- In `_upsert_oauth_user`, when an existing local account matches by email **and has a password**
  (`hashed_password is not None`), do **not** auto-link. Redirect to a
  `/login?error=oauth_link_requires_signin` flow instead (the user must log in with their password
  first to link). Auto-create is fine only when no local account exists.

### 3. Bind the OAuth state to the browser
- Set the `state` value in an `HttpOnly`, `SameSite=Lax`, `Secure` cookie (`oauth_state`) on the
  `/google` and `/facebook` redirect responses.
- In the callbacks, compare the `state` query param to the cookie with `hmac.compare_digest` in
  addition to the existing signature/age check; clear the cookie afterwards.

## Acceptance criteria
- After a successful Google login, the browser URL on `/oauth-callback` contains only `?code=…`,
  never `access_token`/`refresh_token`; the page exchanges the code and logs in.
- A second use of the same `code`, or use after 60 s, returns 400.
- An OAuth login whose Google `email_verified` is false is rejected.
- Logging in via OAuth when a password account with that email exists does not silently merge.
- A callback whose `state` does not match the `oauth_state` cookie is rejected.

## Notes
- python-jose is unmaintained and CVE-prone; migrating JWT to `pyjwt` is a separate, optional task
  — not part of T05.
