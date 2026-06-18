# T19 — Fix two InfluxDB correctness bugs

**Severity:** Medium (correctness) · **Phase:** 3 · **Model tier:** Trivial

## Ground Rules
- LF line endings only. Exactly this task. Both fixes are in `src/lenticularis/database/influx.py`.

## Bug 1 — duplicate `pressure_qff` key in the write field map
In `write_measurements()` (~lines 85–96) the `field_map` dict has `"pressure_qff"` listed twice:
```python
"pressure_qfe": m.pressure_qfe,
"pressure_qff": m.pressure_qff,
"pressure_qff": m.pressure_qff,   # <-- duplicate, delete this line
"precipitation": m.precipitation,
```
**Fix:** delete the duplicate line. Harmless today (same value) but it's dead/confusing code.

## Bug 2 — replay source-preference dedup never triggers
In `query_forecast_replay()` (~lines 1061–1082) the swissmeteo-over-open-meteo preference reads a key
that is never stored:
```python
existing = raw[sid].get(vt_iso)
if existing is None:
    raw[sid][vt_iso] = fields
elif in_src == _PREFERRED and existing.get("_source") != _PREFERRED:
    raw[sid][vt_iso] = fields
```
`fields` is built by stripping every key starting with `_` **and** `source`, so `existing` never
contains `_source` (or `source`). The `elif` is always comparing `None != "swissmeteo"` → it relies
on the stored dict carrying source info it never has. Result: **first-write-wins**, preference is a
silent no-op.

**Fix:** track the source alongside the stored row without polluting the returned `fields`. Use a
parallel map keyed the same way:
```python
raw: dict[str, dict[str, dict]] = {}
src_of: dict[tuple[str, str], str] = {}     # (sid, vt_iso) -> source
...
existing = raw[sid].get(vt_iso)
if existing is None:
    raw[sid][vt_iso] = fields
    src_of[(sid, vt_iso)] = in_src
elif in_src == _PREFERRED and src_of.get((sid, vt_iso)) != _PREFERRED:
    raw[sid][vt_iso] = fields
    src_of[(sid, vt_iso)] = in_src
```
Do not add `source` into the returned rows (the replay payload shape must stay the same).

## Acceptance criteria
- `field_map` contains exactly one `"pressure_qff"` entry.
- When both `swissmeteo` and `open-meteo` forecasts exist for the same station/valid_time in the
  replay window, the returned value is the **swissmeteo** one (verify by reading the dedup logic, or
  with a targeted test once T20 exists).
- Replay payload shape (`{station_id: [{"timestamp": …, <fields>}]}`) is unchanged.
