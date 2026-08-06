# Prompt: Sync Session & Prepare for /clear

> **Purpose**: Flush everything learned this session into `.ai/` and human-readable files, then close all open loops so the next session can start clean.
> **Use when**: Before running `/clear`, ending a long session, or any time context has drifted ahead of the files.

---

## Why This Exists

The AI accumulates discoveries during a session — new tables, new routes, changed plans, resolved decisions — that live only in conversation context. A `/clear` command destroys that context. This prompt makes the session's knowledge durable before the wipe.

---

## Step 1 — Audit What Changed This Session

Scan the conversation and the working directory. For each category, note what is new or different since the session started:

| Category | What to check |
|---|---|
| Schema | New SQLite tables, columns, or relationships |
| API | New or changed routes, request/response shapes |
| Architecture | New services, patterns, or integration points |
| Config | New config keys or environment variables |
| Features | Milestones shipped, backlog items completed or added |
| Decisions | Technical choices made, options ruled out |
| Open issues | Bugs found but not fixed, TODOs left in code |
| In-progress work | Partially completed tasks that need to be resumed |

**Action**: Share a summary of these discoveries with the user.
"I've audited the session and found the following changes to sync:
[Summary of changes]
Should I proceed with updating the `.ai/` context and human-readable files?"

Wait for user confirmation before proceeding to Step 2.

---

## Step 2 — Update `.ai/context/`

### `context/architecture.md`

For every new or changed item found in Step 1:
- Add new SQLite tables to the **SQLite Tables** section
- Add new InfluxDB measurements to the **InfluxDB Measurements** section
- Add or update API routes in the **API Contracts** section
- Add any new deployment notes

Do not remove existing entries unless they were explicitly deleted this session.

### `context/features.md`

- Move completed milestones from "Backlog" to "Shipped Milestones" with a brief description
- Add new backlog items discovered this session
- Update the current version number if a milestone shipped

### `context/backend-notes.md`, `frontend-notes.md`, `security-notes.md`, `testing-notes.md`

This is where most session discoveries land — not `instructions/`. Write here whenever the
session surfaced:
- A new auth dependency, role, or permission rule → `backend-notes.md`
- A fixed bug with a root cause worth guarding against → `security-notes.md`
- A new frontend pattern, asset-pipeline detail, or logging convention → `frontend-notes.md`
- A new test gotcha, fixture pattern, or coverage change → `testing-notes.md`

If the matching file doesn't exist yet, create it (see `00-ai-usage.md` — "Framework vs Project
Knowledge") and add a one-line cross-reference from the relevant `instructions/0X-*.md` file.

---

## Step 3 — Update `.ai/instructions/` (rare)

`instructions/00-ai-usage.md` and `02`–`07` are blueprint-owned — `update-blueprint.md`
overwrites them. Only touch them when something **generic and reusable by any project on this
blueprint** changed, e.g. a new section heading, a cross-reference to a new context file, or a
pattern that has nothing project-specific in it. If what changed is Lenticularis-specific, it
belongs in Step 2's `context/*-notes.md` files instead, not here.

Do not rewrite instructions just to add detail. Update only when the generic pattern itself
should change.

---

## Step 4 — Update Human-Readable Files

Run `.ai/prompts/update-readme.md` now to sync all human-readable files from `.ai/`.

If `PLANNING.md` exists and milestone or roadmap data changed, update it with:
- Current status (one-line summary)
- Completed items ticked off
- In-progress items updated
- Next steps refreshed

---

## Step 5 — Leave Resume Notes

If there is in-progress or unfinished work, create or update `specs/<current-feature>/tasks.md` with:
- All completed tasks marked `[x]`
- The next task clearly marked as the starting point
- Any blockers or open questions noted inline

If no spec folder exists for the in-progress work, write a brief `RESUME.md` at the repo root:

```markdown
# Resume Notes — [DATE]

## In Progress
[What was being worked on]

## Next Step
[Exact next action to take when resuming]

## Open Questions
[Anything unresolved that needs a decision]

## Context
[Key decisions made this session that aren't yet in .ai/]
```

---

## Step 6 — Final Checklist

Before declaring the session closed:

- [ ] `context/architecture.md` reflects all new tables, routes, and measurements
- [ ] `context/features.md` reflects shipped milestones and updated backlog
- [ ] `context/*-notes.md` files reflect any new project-specific conventions or fixed bugs
- [ ] Instruction files updated only if a genuinely generic, blueprint-reusable pattern changed
- [ ] README.md (and PLANNING.md if present) synced via `update-readme.md`
- [ ] In-progress tasks have a clear resume point in `tasks.md` or `RESUME.md`
- [ ] No secrets or credentials appear in any committed file
- [ ] Open issues or bugs are noted somewhere (inline TODO, `RESUME.md`, or GitHub Issue)

Report the checklist result. If any item is incomplete, complete it before finishing.

---

## After This Prompt

The session is ready to clear. Run `/clear` when ready. 

> **Tip**: If you want to pull in the latest framework improvements or new prompts from the central AI Blueprint repository, run the update prompt:
> 
> `/prompt .ai/prompts/update-blueprint.md`

The next session should start by reading `.ai/` — everything needed to continue will be there.
