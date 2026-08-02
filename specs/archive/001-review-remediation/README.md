# Review Remediation — Task Pack

Generated from the external repo review (architecture, tech debt, security, performance).
This folder turns every recommendation into a **self-contained task file** you can hand to a
cheaper model one session at a time.

## How to use this with a cheaper model

1. Open `tasks.md` — it is the master checklist, dependency-ordered into phases.
2. Pick the next unchecked task. Open its task file (e.g. `T01-flux-injection.md`).
3. Paste the **entire task file** as the prompt to the cheaper model. Each file is standalone:
   it names the files to read, the exact edits, the conventions to follow, and the acceptance
   criteria. The model should not need any other context.
4. When the task verifies green, tick its box in `tasks.md`.

Each task file starts with a **Ground Rules** block. That block is mandatory context for the
executing model — do not strip it.

## Recommended execution order

Phases are ordered by risk/value. Within a phase, follow the task numbers. Do **not** start
Phase 4 refactors before the Phase 1 security fixes are merged.

| Phase | Theme | Tasks | Why first/last |
|---|---|---|---|
| 1 | Security (must-fix) | T01–T05 | One Critical (unauthenticated injection) + token/XSS holes |
| 2 | Performance (cheap, high-value) | T06–T11 | Small diffs, large latency/throughput wins |
| 3 | Architecture & correctness | T12–T19 | Convention conformance + two real bugs |
| 4 | Debt & quality (larger) | T20–T23 | Tests/CI and refactors; needs more model judgment |

## Manual actions (NOT model tasks — do these yourself)

These cannot/should not be done by an LLM session:

- **Rotate the live secrets** currently in the working-copy `config.yml`: SMTP password,
  Google OAuth `client_secret`, and the InfluxDB token. They are not committed (correctly
  gitignored) but are real and were surfaced during review. Rotate, then store outside YAML.
- **Scope the InfluxDB token** to read-only + bucket-scoped where the API only reads. This is
  the blast-radius reducer behind T01.
- After T16, **commit `poetry.lock`** (the task edits `.gitignore`; you still run
  `git add poetry.lock`).

## Model tier guidance

- **Trivial (Haiku-class fine):** T06, T11, T15, T16, T17, T18, T19, T23
- **Moderate (Sonnet-class recommended):** T01, T02, T03, T04, T07, T08, T09, T10, T12, T13, T14, T05
- **Larger / iterative (Sonnet-class, multiple sessions):** T20, T21, T22

## Source of findings

Full reasoning and severities are in the review delivered in-session. Line references in these
task files were verified against the working tree at generation time; if the code has moved,
locate the named **function/symbol** rather than trusting the line number.
