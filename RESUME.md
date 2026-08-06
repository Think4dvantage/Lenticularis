# Resume Notes — 2026-08-06

## In Progress

Nothing. This session's work (restructuring `.ai/`, syncing with the central AI Blueprint) is
complete and all changes are in the working tree, uncommitted.

## Next Step

None required — this file exists only to carry forward three small open follow-ups (below). Pick
up normal feature/bug work as usual.

## Open Questions / Follow-ups

1. **`poetry.lock` sync unverified.** The Dockerfile's lockfile handling was tightened this
   session (strict `COPY pyproject.toml poetry.lock ./`, dropped the `poetry lock` re-resolve
   step) — but there's no `poetry` CLI in this environment to confirm `poetry.lock` is actually
   up to date with `pyproject.toml`. Run `poetry check --lock` (or a test build) before the next
   `docker build` / release tag; if it's stale, `poetry lock` once locally and commit the result.

2. **CI doc/reality mismatch.** `.ai/instructions/06-testing-conventions.md` now says "a green
   run is load-bearing — never merge red," but `.github/workflows/test.yml` still has
   `continue-on-error: true` on the ruff step. Either tighten the workflow to match the doc, or
   soften the doc to match the workflow — currently inconsistent.

3. **Blueprint contribution not yet sent.** A prompt proposing the `context/*-notes.md`
   companion-file pattern as a central blueprint change was drafted and handed to the user
   (not persisted in this repo — it was a one-off transport document). Send it to a session on
   `Think4dvantage/ai-blueprint` when convenient; it isn't blocking anything here.

## Context — what happened this session (2026-08-06)

1. Ran the blueprint sync (`update-blueprint.md`) against `Think4dvantage/ai-blueprint`
   (dev-web). Found that blind "always overwrite" would have destroyed substantial
   project-specific content baked into `instructions/02–07` — merged in only the genuinely new,
   generic guidance instead (config/monkeypatch pitfall, scheduler overlap guard, httpx 0.28
   note, Dockerfile lockfile guidance, "verify current versions," "What Not to Test," a
   Playwright skeleton).
2. Fixed a real bug this surfaced: `Dockerfile` used a glob lockfile `COPY` plus an unconditional
   `poetry lock` re-resolve, meaning the committed lockfile was decorative and dependency
   versions could drift silently between builds of the same commit. Both are fixed now (see
   Open Questions #1 for the remaining verification step).
3. Restructured `.ai/` per the user's own instinct: introduced `context/backend-notes.md`,
   `frontend-notes.md`, `security-notes.md`, `testing-notes.md` as project-specific companions to
   the generic, blueprint-owned `instructions/02–07` files. Moved ~600 lines of Lenticularis-specific
   content (auth role table, T01–T19 fixed-bug history, asset pipeline, test harness/coverage)
   into the new files; trimmed the instruction files to genuinely generic, blueprint-safe
   patterns with one-line cross-references. Updated `00-ai-usage.md` (new "Framework vs Project
   Knowledge" section) and `sync.md` (Steps 2/3/6) so future sessions route new project-specific
   knowledge to the right place automatically.
4. Drafted (not sent) a contribution prompt proposing this same companion-file pattern as a
   change to the central blueprint itself, so every project on it gets a sync mechanism that's
   safe to run once real content accumulates.

Nothing has been committed. See `git status` / `git diff --stat` for the full file list.
