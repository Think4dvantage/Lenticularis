# AI Instruction Starter Kit

Drop this into the root of any new project that shares the same tech stack
(FastAPI / SQLite / InfluxDB / vanilla JS / Docker).

## Setup steps

1. Copy `CLAUDE.md` to the repo root.
2. Copy the `.ai/` folder to the repo root.
3. Find and replace every `[PROJECT NAME]` / `[package]` / `[App` placeholder with real values.
4. Fill in `.ai/instructions/01-project-overview.md` — tech stack table, repo layout, data sources, user roles.
5. Start filling in `.ai/context/architecture.md` as you add tables, measurements, and routes.
6. Update `.ai/context/features.md` as milestones ship.

## File map

```
CLAUDE.md                            ← Root instructions for Claude Code
.ai/
  instructions/
    01-project-overview.md           ← What the project is, stack, layout, roles
    02-backend-conventions.md        ← Routers, migrations, InfluxDB, auth, coding standards
    03-frontend-conventions.md       ← No-build-step JS, i18n, dark theme, logging policy
    04-constraints.md                ← Hard rules: no prod SSH, no npm, no Alembic, etc.
  context/
    architecture.md                  ← SQLite tables, InfluxDB measurements, API contracts
    features.md                      ← Shipped milestones + backlog
  prompts/
    new-feature.md                   ← End-to-end implementation checklist
    add-api-router.md                ← Router scaffolding steps
    add-sqlite-table.md              ← Table + migration steps
    add-i18n-keys.md                 ← Locale file update steps
```
