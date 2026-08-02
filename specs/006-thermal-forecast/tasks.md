# Tasks: Thermal Forecasting from LSMFAPI

**Plan**: [plan.md](./plan.md)
**Phase**: 3 — Tasks · **Date**: 2026-08-02
**Target version**: v1.22.2 → **v1.23.0**
**Status**: Phase 1 implemented (2026-08-02), awaiting sync/restart + the coverage gate (§10.1 below). Phases 2-4 not started.

Ordered. Each task is independently verifiable. Section numbers in parentheses refer to `plan.md`.

> **Read first**: `.ai/instructions/` (all), then `plan.md` §3.4 (the wind/thermal grid join),
> §3.5 (what changed since the plan was written), and §10.1 (the Phase 1 exit gate — it is a
> coverage check, not a row count).

---

## Phase 1 — Ingestion

No user-visible change. Ends at a hard gate; do not start Phase 2 before it passes.

- [x] **T01** — `src/lenticularis/services/thermal.py` **(NEW)**. Pure functions + module-level
  threshold constants for all seven derived metrics (§6.1–6.7): `thermal_ceiling_m`,
  `cloud_base_agl_m`, `thermal_strength`, `overdevelopment_risk`, `blue_thermal`,
  `turbulence_index`, `ceiling_spread_m`. No InfluxDB import, no I/O, plain floats in and out.
  `cin_effective = 0.0` when `cin is None` (§2.4); any other null input makes its metric `None`.
- [x] **T06** — `tests/backend/test_thermal_derived.py` **(NEW)**. 63 tests: every band boundary
  on-threshold and either side, the null paths, the `sunshine` cloud-cover guard, the `cloud_mid`
  CAPE gate, and `ceiling_spread_m == 0.0` staying distinct from `None`.
- [x] **T02** — `src/lenticularis/models/weather.py`: added `ThermalForecastPoint` and
  `ThermalGridForecastPoint` (§5.3). All weather values `Optional`; identity fields required.
- [x] **T04** — `src/lenticularis/database/influx.py`: `write_thermal_forecast()`,
  `query_thermal_forecast_for_stations()`, `query_thermal_forecast_snapshot_for_stations()` (§7.2).
  Chunked at 5000. Both queries use `_slow_query_api`. `None` fields skipped; no duplicate field
  keys; station ids interpolated through `_flux_str()`.
- [x] **T03** — `src/lenticularis/collectors/forecast_thermal_swissmeteo.py` **(NEW)** (§7.1).
  Plain class (not `BaseForecastCollector`). `asyncio.to_thread` for both `response.json()` in
  `fetch()` and `build_station_points()`. Uses `haversine_m()` from `services/dedup.py:44`. Reads
  `station.elevation`/`.latitude`/`.longitude` via `getattr` (duck-typed, not the Pydantic
  `WeatherStation` type — matches the collector's own guard pattern). Null-checked per field per
  frame; all-null points skipped. Logs both INFO lines from §7.1 note 6, including the §10.1
  coverage gate. Re-collection-guard state (`last_init_time`/`last_model`/`last_usable_frame_count`)
  exposed as instance attributes, consumed by the scheduler (T05).
- [x] **T07** — `tests/backend/test_thermal_collector.py` **(NEW)**. 9 tests on a 4-point × 3-frame
  hand-written fixture: fully-null frame → zero points; partially-null frame → point emitted with
  `solar=None`; `cin=None` → not treated as a cap, `overdevelopment_risk` computed as if `0.0`;
  unambiguous nearest-grid-point selection (distinct-per-point values prove it, not just identical
  coordinates); station with `latitude=None` skipped; empty grid/frames; missing `init_time`;
  `fetch()`'s 503 → `None` and 200 → parsed payload, both against a fake `httpx` client (no network).
- [x] **T05** — `src/lenticularis/scheduler.py`: registered the `forecast_thermal` job (§7.3).
  `IntervalTrigger(minutes=60)`, health key `forecast_thermal` (type `forecast`, so the existing
  `trigger_collector_now` dispatch already covers it with no special-casing needed), `base_url`
  reused from the `swissmeteo` forecast-collector config, empty-registry guard mirroring
  `scheduler.py:645-655`, `asyncio.to_thread` for both fetch's JSON parse and the Influx write,
  re-collection guard via `self._last_thermal_run_key`, `logger.error(..., exc_info=True)` + health
  record on failure. No Open-Meteo fallback.
- [x] **T00** — `pytest`: **188 passed** (was 96 before this session). `ruff check --isolated` on
  every new/changed file: clean. (3 pre-existing findings elsewhere in `influx.py`/`scheduler.py`
  predate this change set — confirmed via `git diff`, left untouched.)

> ### ⛔ GATE — do not proceed to Phase 2 until this passes (§10.1)
> **Not yet checked — needs a live run.** Code is implemented and unit-tested, but the coverage
> numbers only exist once lsmfapi is actually queried. User syncs and restarts. Then read the
> collector log:
> ```
> [Lenti:thermal-collector] coverage today=%d/12 d1=%d/12 (local 08-19, non-null solar)
> ```
> **Pass:** both days ≥ 10/12 → continue to Phase 2.
> **Fail:** the §3.2 null hole (h+8…h+33 — today's and tomorrow morning's flyable hours) is still
> open upstream. Phase 1 stays merged as a diagnostic; report to the lsmfapi side and **hold
> Phases 2–3**. "Rows exist" is not the gate — rows will exist either way.

---

## Phase 2 — Rules + station detail

- [ ] **T08** — `src/lenticularis/models/rules.py`: extend `FieldName` with the 11 thermal keys
  including `ceiling_spread` (§7.4).
- [ ] **T09** — `src/lenticularis/rules/evaluator.py` **and** `src/lenticularis/services/public_map.py`:
  extend `FIELD_MAP`, then merge thermal into `station_data` at **5 of the 6 sites** (§7.4):
  A `run_evaluation:350-363`, B `run_evaluation_at:509-526`, C `run_forecast_evaluation_at:652-662`,
  D `run_forecast_evaluation:736-740`, F `public_map.py:96-120`. **Not** E
  (`run_history_backfill:885-898`) — no thermal data predates this feature.
  Merge as an explicit key-by-key update of the thermal field names, never `{**live, **thermal}`.
  One batched query per evaluation, never per-station. **No decision-logic changes** — the four
  duplicated decision blocks are field-agnostic.
- [ ] **T10** — `tests/backend/test_rules_thermal.py` **(NEW)**. One test per merge site; the site-F
  public-map regression (a showcase set with a thermal condition must not silently vanish); the
  wind × thermal mixed rule set (§6.9); `FieldName`/`FIELD_MAP` key-set parity. Full list in §9.
- [ ] **T11** — `src/lenticularis/api/routers/stations.py`:
  `GET /api/stations/{station_id}/thermal-forecast?hours=120` (§7.5). Declare it **before** any
  `/{station_id}` catch-all. Allowlist-validate the id (`^[\w\-.]{1,64}$`) → 404. `async def` +
  `await asyncio.to_thread(...)`. `elevation_m` in the response comes from `WeatherStation.elevation`.
  Compute the 8-key `day_windows` summary per §6.8 — and re-read the §6.8 warning about
  partially-null days before deciding this is finished.
- [ ] **T14** — `static/i18n/{en,de,fr,it}.json`: `thermal.*` block + `editor.fields.*` for the 11
  new fields. **All four locales** — they are line-for-line parallel today. Do this **before**
  T12/T13 so the markup has keys to reference.
- [ ] **T12** — `static/station-detail.html` + `.js`: four new chart cards (§7.6). Reuse
  `fcDatasets()` (`station-detail.js:118-155`) for the `lcl_min`/`lcl_max` and `cape_max` bands and
  `renderSimpleChart()` for the 0–5 / 0–3 index bars — **no new charting code**. Dataset order is
  load-bearing (`fill: '-1'`); legend filters `(min)`; tooltip uses `makeForecastFilter()`.
  `textContent` never `innerHTML`; dark-theme tokens not hex; `fetchAuth()`; the console-logging
  policy.
- [ ] **T13** — `static/ruleset-editor.html` `FIELDS` array (`:704-715`) **with units**, plus the two
  duplicated copies at `index.html:637-653` and `ruleset-analysis.html:350-369` — otherwise the map
  popup and analysis page render the new fields unlabelled.
- [ ] **T13b** — `static/help.html`: new `<div class="help-section" id="thermal">` + a `.jump-link`
  in the jump bar (`:211-224`), documenting that **thermal conditions on a live decision read from
  the forecast row valid at the current hour** (§11 decision 1). Plain English — `help.html` is not
  internationalised and has no `help` i18n key.
- [ ] **T00b** — pytest + `ruff check --isolated` green.

> **STOP.** User syncs and verifies in the browser before Phase 3.

---

## Phase 3 — Map layer

- [ ] **T15** — `influx.py`: `write_thermal_forecast_grid()` + `query_thermal_forecast_grid()`
  (§5.2). Chunk at 5000. `grid_id = f"{lat:.4f}_{lon:.4f}"` — **exactly** this format, or the
  §3.4 join with `wind_forecast_grid` breaks silently.
- [ ] **T16** — `collectors/forecast_thermal_swissmeteo.py`: emit grid points from the **same**
  parsed payload as T03. Never a second 28.6 MB fetch.
- [ ] **T17** — `src/lenticularis/api/routers/thermal_forecast.py` **(NEW)**, modelled on
  `wind_forecast.py`: parallel arrays index-aligned to a canonical `grid`, ordered lat-DESC /
  lon-ASC (`wind_forecast.py:122`) so index `j` matches the wind grid.
- [ ] **T18** — `api/main.py` register the router; add the `/thermal-forecast` page route in
  **`routers/pages.py`, never `main.py`** (`pages.py:138-140` is the `/wind-forecast` precedent).
- [ ] **T19** — `static/thermal-forecast.html` + `static/thermal-forecast.js`. Modelled on
  `wind-forecast.html`, but ⚠️ **there is no `wind-forecast.js`** — that page is one 597-line HTML
  with a 348-line inline module. Extract logic to a real `.js` as `index.html`/`map.js` does.
  Per §6.9, overlay the existing wind arrows: same 1272 cells, same index `j`, no join code —
  but tolerate a lookup miss (§3.4 note 3, the Open-Meteo fallback grid does not join).
- [ ] **T20** — Nav link in **13 places, not 14 files**: `bootstrap.js:19` `_NAV_HTML` (covers
  foehn / ruleset-editor / rulesets / stats at once), then the 9 inline navs listed in the T20 row
  of §7. Skip `login`, `register`, `org-dashboard`, `oauth-callback`.
- [ ] **T00c** — pytest + `ruff check --isolated` green.

---

## Phase 4 — Docs & release

- [ ] **T21** — `pyproject.toml` → `version = "1.23.0"`. **Mandatory** — static assets changed, and
  the version is the asset cache key (`04-constraints.md`).
- [ ] **T22** — `.ai/context/architecture.md`: both new measurements, both new endpoints, the
  derived-metric table, and the §3.4 grid-join fact.
- [ ] **T23** — `.ai/context/features.md`: v1.23.0 milestone entry; move the "Thermal Forecast"
  backlog entry out of Backlog.
- [ ] **T24** — Three doc fixes (§7 T24 row):
  - `.ai/context/lsmfapi-thermal-grid.md` — payload table says ~0.5 MB at `stride_km=10`, measured
    **28.6 MB**; drop the stale "serves FGA stations only" note and the "Integration ideas" section.
  - `.ai/context/architecture.md:65` — documents `wind_forecast_grid.init_date` as `YYYY-MM-DDTHH`;
    `influx.py:898` writes `%Y-%m-%d`.
  - `.ai/context/features.md:258` + `.ai/context/forecast-analysis-wip.md:18,50` — describe a
    `_ranking_client` / `ranking_query_timeout: 300000` that **does not exist**; `influx.py:49-66`
    has only `_client` (10 s) and `_slow_client` (60 s).
- [ ] **T25** — `.ai/instructions/01-project-overview.md`: add the thermal grid to the SwissMeteo
  data-sources row.
- [ ] **T26** — `README.md`: feature + API sections.
- [ ] **T27** — `specs/archive/README.md`: add the `006-thermal-forecast` row and move the folder to
  `specs/archive/` once shipped.

---

## Out of scope — recorded, not scheduled

| Item | Why not here |
|---|---|
| **`scheduler.py:840` altitude bug** | `getattr(station, "altitude", None)` is always `None` (the attribute is `elevation`), so every station falls through to `level_hpa = 950` in the wind-grid mapping. Pre-existing, unrelated to thermal, and fixing it changes wind-grid behaviour — needs its own change with its own verification. T03 must simply not copy it. |
| **Fixing lsmfapi's null frames** | lsmfapi-side workstream (§3.2). This plan survives nulls; it does not fix them. |
| **`/api/forecast/altitude-winds` / `vertical_wind`** | 100 % null across every probed level (§1.1). No data to build on. |
| **A combined `flyability` metric in the collector** | Would couple two collectors with different `init_time`s and bake one pilot's wind tolerance into stored data (§6.9). The composition belongs in the ruleset. |
| **Thermal accuracy verification via the JFB elevation ladder** | Genuinely feasible — JFB gives a measured lapse rate and observed cloud base from T−Td spread, the observed counterpart to forecast `lcl` (§12). Deserves its own spec once Phase 1 has accumulated data. |
| **InfluxDB retention / downsampling** | There is no retention policy anywhere in the repo; one bucket per environment, created out-of-band. These measurements add ~870 k points/day. Pre-existing gap, already in the backlog as "Performance pass". |
