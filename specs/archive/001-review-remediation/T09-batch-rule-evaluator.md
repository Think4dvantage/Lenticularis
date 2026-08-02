# T09 — Batch the rule evaluator's per-station Influx fetch

**Severity:** High · **Phase:** 2 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/08-operability.md`. LF line endings only. Exactly this task — keep the
  decision logic identical, only change how station data is fetched.

## Problem
In `src/lenticularis/rules/evaluator.py`, `run_evaluation` fetches latest data **one station at a
time** (around lines 322–327):
```python
for sid in station_ids:
    members = (virtual_members or {}).get(sid)
    if members:
        d = influx.query_latest_virtual(members)
    else:
        d = influx.query_latest(sid)
    ...
```
The scheduler calls `run_evaluation` for every ruleset, so total Influx round-trips =
Σ(stations per ruleset) per tick — heavily redundant when rulesets share popular launch sites.
A batch query method already exists: `query_latest_for_stations(station_ids)`.

## Fix (two parts)

### Part A — batch within a single evaluation
Replace the per-station loop with a single batched call for the non-virtual stations, keeping the
per-virtual-station path (which needs member newest-wins):
```python
plain_ids = [sid for sid in station_ids if not (virtual_members or {}).get(sid)]
batched = influx.query_latest_for_stations(plain_ids) if plain_ids else {}
for sid in station_ids:
    members = (virtual_members or {}).get(sid)
    if members:
        d = influx.query_latest_virtual(members)
    else:
        d = batched.get(sid)
    if d:
        station_data[sid] = d
    else:
        no_data.append(sid)
        logger.warning("No InfluxDB data for station %s during evaluation of ruleset %s", sid, ruleset.id)
```
Verify `query_latest_for_stations` returns a `{station_id: {fields…}}` dict shape matching what the
downstream code expects from `query_latest` (same keys). If the shapes differ, adapt the mapping —
do not change downstream consumers.

### Part B (optional, only if low-risk) — share across rulesets per tick
In the scheduler's evaluation loop, if it iterates many rulesets, you may fetch the union of all
their station ids once and pass a prebuilt `station_data` dict into evaluation. **Only do this if
`run_evaluation` can accept an optional pre-fetched data dict without restructuring its signature in
a breaking way.** If it would require invasive changes, skip Part B and ship Part A alone — note the
skip in your completion summary.

## Acceptance criteria
- Evaluating a ruleset whose conditions span N plain stations issues **one** `query_latest_for_stations`
  call instead of N `query_latest` calls (confirm via DEBUG logs or by reading the code path).
- Decisions are byte-for-byte identical to before for the same data (green/orange/red unchanged).
- Virtual (deduped) stations still resolve via `query_latest_virtual`.
