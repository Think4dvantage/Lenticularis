# Claude Code Instructions — Lenticularis

Project conventions, architecture, and patterns live in `.ai/`:
- `.ai/instructions/` — coding conventions (backend, frontend, constraints)
- `.ai/context/` — architecture reference, feature history, backlog
- `.ai/prompts/` — reusable task templates

Read the relevant `.ai/` files before making changes. The instructions below are Claude Code–specific.

---

## Planning Mode

**When the user enters plan mode (`/plan` or similar), produce a plan and stop. Never start implementing immediately after a plan is approved.**

- Exit plan mode → write the plan file → wait for an explicit "go ahead" / "implement" instruction in a new message.
- If `ExitPlanMode` is called and the user approves, that approval means "the plan looks good" — not "start coding now".
- Do not write, edit, or create any files (except the plan file) during or immediately after planning.
- Implementation begins only when the user sends a separate follow-up message explicitly asking to proceed.

---

## Tool Usage

- Use `Grep` / `Read` / `Glob` tools instead of `rg`, `grep`, `cat`, `find` as bash commands.
- Use `Edit` instead of `sed` / `awk`.
- Use `Write` instead of `echo >` / `cat <<EOF`.
- Reserve `Bash` for system commands that have no dedicated tool equivalent.
