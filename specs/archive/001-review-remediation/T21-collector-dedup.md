# T21 — De-duplicate collectors

**Severity:** Medium · **Phase:** 4 · **Model tier:** Larger (refactor; do after T20 exists)

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md` (collectors use ABC bases), `08-operability.md`.
- LF line endings only. **Behavior-preserving refactor** — output of each collector must be identical
  before/after. Strongly prefer doing this only once T20 gives you a test to lean on.

## Problem
~25–30% of collector code is copy-paste:
- `_to_float` reimplemented in 7 files (`metar.py`, `slf.py`, `holfuy.py`, `fga.py`, `windline.py`,
  `wunderground.py`, `ecowitt.py`).
- Timestamp parsing in ~6 variants; wind-direction normalize (float→int→`%360`) in ~4.
- Concurrency boilerplate drift: SLF/Windline use a bounded `Semaphore`; Ecowitt/Wunderground use
  **unbounded** `asyncio.gather`. Error log levels also drift (some WARNING, some ERROR).

## Fix — incremental, low-risk
1. Create `src/lenticularis/collectors/utils.py` with shared pure helpers:
   - `to_float(value) -> float | None` (the common tolerant parse).
   - `normalize_wind_dir(value) -> int | None` (float → int → `% 360`, None-safe).
   - `parse_timestamp(...)` — only if a single signature can cover the existing variants; if formats
     genuinely differ per network, keep per-collector parsing but factor the common ISO/epoch cases.
2. Replace the per-file duplicates with imports from `utils.py`, **one collector at a time**, running
   the collector (or its test) after each swap to confirm identical output.
3. Standardize concurrency: add a `_collect_concurrent(self, items, fn, *, limit=8)` helper on
   `BaseCollector` that uses a bounded `asyncio.Semaphore`, and convert the unbounded-`gather`
   collectors (Ecowitt, Wunderground) to it. Keep the bound configurable; default to a sane limit.
4. Standardize per-station error handling to one pattern + consistent log level (WARNING for a
   single-station fetch failure, ERROR only for whole-collector failure) per `08-operability.md`.

## Constraints
- Do not change the network response parsing semantics or the resulting `WeatherMeasurement` fields.
- Do not introduce new dependencies.
- If a helper cannot cleanly cover a collector's quirk, leave that collector's local version and note
  it — partial de-duplication is fine; correctness first.

## Acceptance criteria
- `to_float` / wind-direction normalize exist once in `collectors/utils.py` and are imported by the
  collectors that had local copies.
- Ecowitt and Wunderground use a bounded concurrency helper (no unbounded `gather` over all stations).
- Each refactored collector produces the same measurements as before (spot-check against a live or
  recorded response; ideally a test from T20).
