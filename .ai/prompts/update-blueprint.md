# Prompt: Update AI Blueprint from Central Repo

> **Purpose**: Pull the latest framework files from the central AI Blueprint repo and apply them to this project's `.ai/` folder.
> **Source**: `C:\git\ai-blueprint` (local clone) or https://github.com/Think4dvantage/ai-blueprint
> **Use when**: You want to sync improvements made to the central blueprint into this project.

---

## The Two Categories of Files

Not all `.ai/` files are updated. The blueprint owns the **framework**. The project owns its **data**.

| Category | Files | Rule |
|---|---|---|
| **Framework** — owned by blueprint | `instructions/00-ai-usage.md`, `instructions/02-backend-conventions.md`, `instructions/03-frontend-conventions.md`, `instructions/04-constraints.md`, `instructions/05-user-profile.md`, `instructions/06-testing-conventions.md`, `instructions/07-api-conventions.md`, `instructions/08-operability.md`, all `prompts/*.md` | Always overwrite with latest from repo |
| **Project data** — owned by this project | `instructions/01-project-overview.md`, `context/architecture.md`, `context/features.md`, `context/flutter-app-plan.md` | Never touch — these are project-specific |

---

## Step 0 — Prefer Local Clone

Check if `C:\git\ai-blueprint` exists and is up to date (`git pull`). If it does, copy files directly from there — no network fetch needed.

If the local clone is absent, fall back to fetching from:
`https://raw.githubusercontent.com/Think4dvantage/ai-blueprint/main/`

---

## Step 1 — Fetch the Latest Framework Files

Copy or fetch each framework file. If fetching remotely, attempt the manifest first:

**Manifest URL**: `https://raw.githubusercontent.com/Think4dvantage/ai-blueprint/main/.ai/manifest.json`

**Hardcoded Fallback List** (only use if manifest fetch fails):

| Source path | Local path |
|---|---|
| `.ai/instructions/00-ai-usage.md` | `.ai/instructions/00-ai-usage.md` |
| `.ai/instructions/02-backend-conventions.md` | `.ai/instructions/02-backend-conventions.md` |
| `.ai/instructions/03-frontend-conventions.md` | `.ai/instructions/03-frontend-conventions.md` |
| `.ai/instructions/04-constraints.md` | `.ai/instructions/04-constraints.md` |
| `.ai/instructions/05-user-profile.md` | `.ai/instructions/05-user-profile.md` |
| `.ai/instructions/06-testing-conventions.md` | `.ai/instructions/06-testing-conventions.md` |
| `.ai/instructions/07-api-conventions.md` | `.ai/instructions/07-api-conventions.md` |
| `.ai/instructions/08-operability.md` | `.ai/instructions/08-operability.md` |
| `.ai/prompts/add-api-router.md` | `.ai/prompts/add-api-router.md` |
| `.ai/prompts/add-i18n-keys.md` | `.ai/prompts/add-i18n-keys.md` |
| `.ai/prompts/add-sqlite-table.md` | `.ai/prompts/add-sqlite-table.md` |
| `.ai/prompts/analyze.md` | `.ai/prompts/analyze.md` |
| `.ai/prompts/architect.md` | `.ai/prompts/architect.md` |
| `.ai/prompts/checklist.md` | `.ai/prompts/checklist.md` |
| `.ai/prompts/clarify.md` | `.ai/prompts/clarify.md` |
| `.ai/prompts/fix-bug.md` | `.ai/prompts/fix-bug.md` |
| `.ai/prompts/implement.md` | `.ai/prompts/implement.md` |
| `.ai/prompts/new-feature.md` | `.ai/prompts/new-feature.md` |
| `.ai/prompts/plan.md` | `.ai/prompts/plan.md` |
| `.ai/prompts/specify.md` | `.ai/prompts/specify.md` |
| `.ai/prompts/sync.md` | `.ai/prompts/sync.md` |
| `.ai/prompts/tasks.md` | `.ai/prompts/tasks.md` |
| `.ai/prompts/taskstoissues.md` | `.ai/prompts/taskstoissues.md` |
| `.ai/prompts/update-blueprint.md` | `.ai/prompts/update-blueprint.md` |
| `.ai/prompts/update-readme.md` | `.ai/prompts/update-readme.md` |

Copy all files. If a file is absent in the source, note the failure and continue.

---

## Step 2 — Detect New Files

Any file in the source that does not currently exist locally is a **new addition** — write it.

---

## Step 3 — Detect Removed Files

After writing all files, check if any local framework files exist that were **not** in the source list. If any are found:

- Do not delete them automatically
- List them and ask: "These local framework files have no equivalent in the central blueprint. Delete them? (yes/no per file)"

---

## Step 4 — Report Changes

For each file processed, report the outcome:

| File | Result |
|---|---|
| `instructions/00-ai-usage.md` | Updated |
| `prompts/sync.md` | No change |
| `prompts/new-prompt.md` | Added (new in blueprint) |
| `prompts/old-prompt.md` | Needs review (exists locally, not in blueprint) |

Summary line: `N files updated, N added, N unchanged, N failed, N need review`

---

## Step 5 — Check for Conflicts with Project Data

After updating, read the project data files and skim the updated framework files.

If the updated framework references patterns, conventions, or sections that no longer match the project data files, flag them:

> **Conflict:** `context/architecture.md` has no "Deployment" section but `00-ai-usage.md` now references it. Consider updating the project file to match the new blueprint structure.

Do not auto-fix conflicts. Report them and ask if the user wants to address them.

---

## Step 6 — Identify Contributions to the Blueprint

If this project has improved a framework file or added a useful new prompt, suggest contributing it back:

> **Contribution Suggestion**: I noticed you've improved the following framework files:
> - `instructions/02-backend-conventions.md` (Lenticularis-specific collector conventions)
>
> Would you like to contribute the generic parts back to the central AI Blueprint?

---

## Notes

- Project data files (`01-project-overview.md`, `context/architecture.md`, `context/features.md`, `context/flutter-app-plan.md`) are **never touched** by this prompt.
- Framework files that have been extended with project-specific sections (e.g. Collector Reference in `02-backend-conventions.md`) should be reviewed manually after a blueprint update to re-merge the project-specific additions.
- This prompt updates itself — that is intentional.
