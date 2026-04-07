# Copilot Instructions — Lenticularis

Project conventions and architecture live in `.ai/`:

- `.ai/instructions/01-project-overview.md` — what this project is, tech stack, repository layout, data flow
- `.ai/instructions/02-backend-conventions.md` — routers, migrations, auth, config, org multi-tenancy, collectors
- `.ai/instructions/03-frontend-conventions.md` — no build step, i18n, dark theme, fetchAuth, module scripts
- `.ai/instructions/04-constraints.md` — what NOT to do
- `.ai/context/architecture.md` — SQLite schema, InfluxDB measurements, API contracts, rules engine, statistics
- `.ai/context/features.md` — shipped milestones and backlog
- `.ai/prompts/` — reusable task templates (add router, add table, add i18n keys, new feature checklist)

Read the relevant files before suggesting changes.
