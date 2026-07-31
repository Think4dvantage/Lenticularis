# Implementation Plan: Thermal Forecasting from LSMFAPI

**Phase**: 2 — Plan · **Date**: 2026-07-31
**Target version**: v1.20.0 → **v1.21.0**
**Audience**: this document is written to be implemented by an agent that has *not* seen the
research session. Everything needed is inline. Read `.ai/instructions/` first anyway.

---

## 1. Why this exists

Lenticularis consumes two of lsmfapi's four forecast endpoints:

| lsmfapi endpoint | Consumed today? | By what |
|---|---|---|
| `GET /api/forecast/station` | ✅ yes | `collectors/forecast_swissmeteo.py` → `weather_forecast` |
| `GET /api/forecast/grid` | ✅ yes | `collectors/forecast_grid_swissmeteo.py` → `wind_forecast_grid` |
| `GET /api/forecast/thermal-grid` | ❌ **no** | — |
| `GET /api/forecast/altitude-winds` | ❌ **no** | — |

`/api/forecast/thermal-grid` (lsmfapi v0.3.2, hardened through v0.3.40) carries **12 convective
fields × ensemble median/min/max**, hourly, h+0…h+120, on a ~1 km ICON-CH grid. These are exactly
the quantities a thermal forecast is made of — cloud base, freezing level, CAPE/CIN, solar
radiation, sunshine duration, boundary-layer turbulence. None of it reaches Lenticularis today.

**Goal of this feature**: ingest the thermal grid, derive pilot-facing thermal metrics from it,
make those metrics available to (a) the rules engine, (b) the station detail page, and (c) a new
thermal map layer.

### 1.1 What is deliberately NOT in scope

- **`/api/forecast/altitude-winds` / `vertical_wind`.** Verified live on 2026-07-31: `vertical_wind`
  is `null` for **100 %** of levels across every station probed (`meteoswiss-INT`, `meteoswiss-BER`
  — 1035 levels each, 0 non-null), while `wind_speed` on the same profiles is ~80 % populated. The
  field is plumbed through lsmfapi but carries no data. Do not build on it. Revisit when lsmfapi
  reports it fixed.
- **Fixing lsmfapi's null frames** (see §3.2). That is a separate workstream on the lsmfapi side.
  This plan only has to *survive* nulls, not fix them.
- Changing anything in `C:\git\LSMFAPI`. This is a Lenticularis-only change set.

---

## 2. Verified API contract

Probed live on **2026-07-31** from inside the `lenticularis` container on `sdh` against
`http://lsmfapi:8000` (lsmfapi **v0.3.39**, cache warm). Do not re-derive this from
`.ai/context/lsmfapi-thermal-grid.md` — that file has a wrong payload-size table, corrected in T14.

### 2.1 Request

```
GET /api/forecast/thermal-grid?bbox=45.8,47.9,5.9,10.6&stride_km=10
```

`stride_km` ∈ {1, 2, 5, 10}. `bbox` = `lat_min,lat_max,lon_min,lon_max`, must sit inside
lat 43–50 / lon 3–17. Defaults to the full Switzerland bbox above, which is what we want.

### 2.2 Response shape (measured)

```jsonc
{
  "init_time": "2026-07-31T00:00:00+00:00",
  "model":     "icon-ch1+ch2",     // or "icon-ch1" / "icon-ch2" if only one slice cached
  "stride_km": 10,
  "grid":   [ {"lat": 47.9, "lon": 5.9}, ... ],       // 1272 points, row-major lat DESC / lon ASC
  "frames": [ { "valid_time": "...", "<field>": [...], ... }, ... ]   // 121 frames, h+0…h+120
}
```

- **37 keys per frame**: `valid_time` + 12 base fields + 12 `_min` + 12 `_max`.
- `frames[i].<field>[j]` is the value for `grid[j]`. Index `j` maps 1-to-1 into every field array.
- All values rounded to 1 decimal. `null` = ICON fill value or missing data.

### 2.3 The 12 base fields

| Field | Unit | Meaning for a pilot |
|---|---|---|
| `solar` | W/m² | Primary thermal trigger. Active thermals typically need > 300 |
| `sunshine` | min/h | Sun-on-ground, 0–60. High variance = patchy = uneven thermals |
| `cloud_cover` | % | Total cover. Sustained > 80 % suppresses thermals |
| `cloud_low` | % | Fog/stratus. High = ceiling below usable altitude |
| `cloud_mid` | % | Overdevelopment cap indicator |
| `cloud_high` | % | Cirrus; indirect synoptic instability signal |
| `freezing_level` | m ASL | Hard upper ceiling (0 °C isotherm) |
| `cape` | J/kg | 0 = stable, 300–1000 = moderate, > 1500 = severe/explosive |
| `cin` | J/kg (**negative**) | Inhibition cap. `null` = **no cap at all** — see §2.4 |
| `lcl` | m ASL | Lifted Condensation Level = cloud base. **Key field** |
| `lfc` | m ASL | Level of Free Convection |
| `tke` | J/kg | Boundary-layer turbulence; > 2 = rough air |

Each has a `_min` / `_max` sibling = ensemble extremes (~10 members CH1, ~21 CH2).

> `boundary_layer_height` (HPBL) and `cloud_base_convective` (HBAS_CON) are **not published** by
> MeteoSwiss for ICON-CH1/CH2-EPS and are not available. `lcl` is the closest thermal-ceiling
> proxy. Do not go looking for HPBL.

### 2.4 Null semantics — get this right

| Field | `null` means | Handling |
|---|---|---|
| `cin` | **No convective inhibition layer exists** (ICON fill −999.9 clipped by lsmfapi) | Substitute `0.0` in every derived calculation. Do **not** write the field to InfluxDB — absence must stay distinguishable from a real 0 |
| all others | Data genuinely unavailable for that point/hour | Skip. Never write, never render, never coerce to 0 |

### 2.5 Measured payload size

| Request | Points | Frames | Bytes | Wall time |
|---|---|---|---|---|
| full CH bbox, `stride_km=10` | 1272 | 121 | **28.6 MB** | 4.1 s |
| 0.4°×0.6° bbox, `stride_km=10` | 40 | 121 | 0.96 MB | < 1 s |

**28.6 MB / 4.1 s is the number to design against.** One request covers every station and the whole
map — never fan out per-station requests against this endpoint.

---

## 3. Constraints discovered during research

### 3.1 The thermal grid and the station forecast have *different* `init_time`s

Measured simultaneously: `/thermal-grid` reported `init_time = 2026-07-31T00:00:00Z` while
`/altitude-winds` for the same station reported `2026-07-31T06:00:00Z`. They are separate caches
filled by separate collector phases.

**Consequence — this rules out one tempting design.** Writing thermal fields into the existing
`weather_forecast` measurement would tag them with a *different* `init_date` than the wind fields
at the same `(station_id, valid_time)`. That creates two InfluxDB series colliding on one
timestamp, and `query_forecast_snapshot_for_stations` (`influx.py:280`) dedups with a
"keep the entry with the most fields" heuristic (`influx.py:325`) — it would arbitrarily return
wind-only *or* thermal-only, never merged. Rules would silently see missing data.

➜ **Decision: thermal data goes in its own measurement**, `weather_forecast_thermal`, joined in
Python at the point where `station_data` is built. See §5.1 and §7.

### 3.2 Large parts of the grid are currently null (out of scope, must be survived)

Measured on the `00Z` run: frames h+0…h+7 populated, **h+8…h+33 entirely null across all 12
fields**, h+34…h+120 populated. Additionally, individual frames sporadically null a *subset* of
fields — e.g. frame 36 had `solar/sunshine/cloud_cover/freezing_level/cape` all-null while
`cin/lcl/lfc/tke` were fully populated; frame 89 had `lcl` null but `tke` present.

This is lsmfapi-side (the h8–33 hole is a mid-write or partly-failed CH1 slice; the scattered
single-frame holes are lsmfapi's v0.3.33 deliberate fail-safe, which nulls a step rather than
emit a wrongly-differenced accumulation). **Not this feature's problem to fix.**

**Requirements it does impose:**

1. Never assume 121 frames are usable. Iterate what is there.
2. Never assume a frame that has one field has all of them. Null-check **per field, per frame**.
3. Never write a null as 0 (except `cin`, per §2.4).
4. A derived metric whose inputs are missing is itself `null` — do not write it. Never
   partially compute (e.g. `thermal_strength` from `solar` alone when `cape` is missing → the
   `cape` term contributes nothing, which is correct; but if `solar` itself is null the whole
   metric is null).
5. Log the non-null frame count once per run at INFO so a degraded upstream is visible
   (`08-operability.md`).

### 3.3 lsmfapi's station list is the full Lenticularis registry

`GET /api/stations` returns **519** stations across 10 networks (slf 203, meteoswiss 155,
holfuy 113, jfb 13, fga 9, wunderground 8, foehn 7, metar 7, windline 3, ecowitt 1). Any note
claiming lsmfapi "only serves FGA stations" is stale.

Irrelevant for the thermal grid anyway — it is spatial, not per-station, so Lenticularis samples
it against its **own** `_station_registry`.

---

## 4. Architecture

Three phases, each independently shippable. **Phase 1 is the foundation and must land first.**

```
                    ┌──────────────────────────────────────────┐
                    │  lsmfapi  /api/forecast/thermal-grid     │
                    │  ONE request · 28.6 MB · 1272 × 121       │
                    └────────────────────┬─────────────────────┘
                                         │ hourly (scheduler job)
                    ┌────────────────────▼─────────────────────┐
                    │ collectors/forecast_thermal_swissmeteo.py │
                    │  · parse                                  │
                    │  · nearest grid point per station         │
                    │  · compute derived metrics (§6)           │
                    └───────┬───────────────────────┬───────────┘
                            │                       │
          ┌─────────────────▼──────────┐  ┌─────────▼────────────────────┐
          │ weather_forecast_thermal   │  │ thermal_forecast_grid        │
          │ 519 stations × 121 h       │  │ 1272 cells × 121 h           │
          │      (Phase 1)             │  │      (Phase 3)               │
          └──────┬──────────────┬──────┘  └─────────┬────────────────────┘
                 │              │                   │
        ┌────────▼──────┐  ┌────▼─────────────┐  ┌──▼──────────────────┐
        │ rules engine  │  │ station-detail   │  │ /thermal-forecast   │
        │  (Phase 2)    │  │ thermal panel    │  │ map page            │
        │               │  │  (Phase 2)       │  │  (Phase 3)          │
        └───────────────┘  └──────────────────┘  └─────────────────────┘
```

**One HTTP fetch feeds both measurements.** Do not write two collectors that each fetch 28.6 MB.

### 4.1 Storage cost — sanity check

| Measurement | Points per run | Fields/point | vs. existing |
|---|---|---|---|
| `weather_forecast_thermal` | 519 × 121 = **62 799** | ~24 | Same point count as `weather_forecast` already writes hourly |
| `thermal_forecast_grid` | 1272 × 121 = **153 912** | ~15 | **1/8** of `wind_forecast_grid` (8 levels × 1272 × 121 = 1.23 M) |

Both are comfortably within what the instance already sustains. No InfluxDB capacity work needed.

### 4.2 Interaction with `specs/005-influxdb3-migration`

`specs/005` is an in-flight, uncommitted plan to move to InfluxDB 3. The two new measurements here
are plain measurements written through the existing `InfluxClient` façade with no exotic Flux —
whatever 005 does to `weather_forecast` / `wind_forecast_grid` applies identically to these.
**Action for the implementer**: read `specs/005-influxdb3-migration/plan.md` before writing the
Flux in T04/T11 and match whatever query style it settles on. Do not invent a third style.

---

## 5. Data model

### 5.1 New measurement: `weather_forecast_thermal`

Mirrors `weather_forecast`'s tag layout exactly (`influx.py:808-873`) so dedup logic transfers.

| | |
|---|---|
| **Tags** | `station_id`, `network`, `source` (`"swissmeteo"`), `model` (`"icon-ch"`), `init_date` (`"%Y-%m-%dT%H"` UTC) |
| **Time** | `valid_time` |
| **Fields** | see below, all `float`; plus `init_time` as an **ISO string field** for Python-side dedup — same trick as `influx.py:863` |

`init_date` is hour-granular (`2026-07-31T00`) so each model run is its own series, exactly like
`weather_forecast`. **Not** day-granular like `wind_forecast_grid`.

**Raw fields (ensemble median):** `solar`, `sunshine`, `cloud_cover`, `cloud_low`, `cloud_mid`,
`cloud_high`, `freezing_level`, `cape`, `cin`, `lcl`, `lfc`, `tke`

**Ensemble spread — a deliberate subset, not all 24 siblings:**
`lcl_min`, `lcl_max` (ceiling uncertainty — the number pilots plan around),
`cape_max` (storm worst case), `cloud_cover_max` (pessimistic sky), `solar_min` (pessimistic trigger).

> Rationale: writing all 24 `_min`/`_max` would triple the write volume for fields no UI or rule
> will read. These five are the ones that change a go/no-go call. If a later need appears, adding
> a field to this list is a one-line change — dropping one is not.

**Derived fields (computed by the collector, §6):** `thermal_ceiling_m`, `cloud_base_agl_m`,
`thermal_strength`, `overdevelopment_risk`, `blue_thermal`, `turbulence_index`

Derived metrics are computed in the **collector**, not the frontend, because the rules engine must
be able to threshold them server-side.

### 5.2 New measurement: `thermal_forecast_grid` (Phase 3)

Mirrors `wind_forecast_grid` (`influx.py:879-921`).

| | |
|---|---|
| **Tags** | `grid_id` = `f"{lat:.4f}_{lon:.4f}"`, `init_date` (`"%Y-%m-%d"` — day-granular, matching `wind_forecast_grid`) |
| **Time** | `valid_time` |
| **Fields** | `lat`, `lon`, the 12 raw medians, `thermal_ceiling_m`, `thermal_strength` |

No `cloud_base_agl_m` — grid cells have no station elevation.

> Known quirk, mirrored on purpose: day-granular `init_date` means the 4 daily runs share one
> series and a later run overwrites an earlier one at the same timestamp. That is existing
> `wind_forecast_grid` behaviour (`influx.py:898`). Consistency beats correctness here; changing
> both is a separate task, not this one.

### 5.3 New Pydantic models — `models/weather.py`

Add alongside `GridForecastPoint` / `ForecastPoint`:

```python
class ThermalForecastPoint(BaseModel):
    """One hourly thermal forecast value for one station from one model run.

    Written to the ``weather_forecast_thermal`` InfluxDB measurement.
    """
    station_id: str
    network: str
    source: str = "swissmeteo"
    model: str = "icon-ch"
    init_time: datetime
    valid_time: datetime

    # raw ensemble medians
    solar: Optional[float] = None
    sunshine: Optional[float] = None
    cloud_cover: Optional[float] = None
    cloud_low: Optional[float] = None
    cloud_mid: Optional[float] = None
    cloud_high: Optional[float] = None
    freezing_level: Optional[float] = None
    cape: Optional[float] = None
    cin: Optional[float] = None          # None = no inhibition layer (see §2.4)
    lcl: Optional[float] = None
    lfc: Optional[float] = None
    tke: Optional[float] = None

    # selected ensemble spread
    lcl_min: Optional[float] = None
    lcl_max: Optional[float] = None
    cape_max: Optional[float] = None
    cloud_cover_max: Optional[float] = None
    solar_min: Optional[float] = None

    # derived (§6)
    thermal_ceiling_m: Optional[float] = None
    cloud_base_agl_m: Optional[float] = None
    thermal_strength: Optional[int] = None
    overdevelopment_risk: Optional[int] = None
    blue_thermal: Optional[int] = None
    turbulence_index: Optional[int] = None


class ThermalGridForecastPoint(BaseModel):
    """One hourly thermal forecast value for one grid cell. → ``thermal_forecast_grid``."""
    grid_id: str
    lat: float
    lon: float
    init_time: datetime
    valid_time: datetime
    # ... 12 raw medians + thermal_ceiling_m + thermal_strength
```

---

## 6. Derived thermal metrics — exact specification

Put these in a **new module** `src/lenticularis/services/thermal.py`, as pure functions over plain
floats, with the thresholds as module-level named constants. Pure + constant-driven = unit-testable
without InfluxDB and tunable without hunting through a collector.

### 6.1 `thermal_ceiling_m`

```
inputs:  lcl, freezing_level   (m ASL)
result:  min(lcl, freezing_level)   — whichever is present if only one is
         null if both are null
```
Cloud base caps the *usable* thermal; the freezing level is the hard ceiling. The lower one wins.

### 6.2 `cloud_base_agl_m`

```
inputs:  lcl (m ASL), station elevation_m
result:  max(0.0, lcl - elevation_m)
         null if lcl is null or elevation_m is null
```
Cloud base above ground is what a pilot on that hill actually experiences. Station-level only —
never computed for grid cells. Clamp at 0 (an LCL below terrain means cloud on the deck).

### 6.3 `thermal_strength` — integer 0–5

```python
THERMAL_SOLAR_BANDS = ((150, 0), (300, 1), (500, 2), (700, 3), (850, 4))  # W/m² → base score
THERMAL_CAPE_BONUS_JKG      = 300     # cape at/above this → +1
THERMAL_CLOUD_PENALTY_PCT   = 70      # cloud_cover above this → -1
THERMAL_CLOUD_HEAVY_PCT     = 90      # cloud_cover above this → -2 (instead of -1)
THERMAL_CIN_PENALTY_JKG     = -100    # cin at/below this → -1
```

```
base   = 0 if solar < 150, 1 if < 300, 2 if < 500, 3 if < 700, 4 if < 850, else 5
score  = base
score += 1  if cape is not null and cape >= 300
score -= 2  if cloud_cover is not null and cloud_cover > 90
       else -1  if cloud_cover is not null and cloud_cover > 70
score -= 1  if cin_effective <= -100          # cin_effective = 0.0 when cin is null (§2.4)
result = clamp(score, 0, 5)
null   if solar is null      # solar is the trigger; without it there is no forecast to give
```

A missing *modifier* (`cape`, `cloud_cover`) simply contributes nothing — that is intended. A
missing `solar` makes the whole index null.

### 6.4 `overdevelopment_risk` — integer 0–3

```python
OVERDEV_CAPE_BANDS   = ((300, 0), (800, 1), (1500, 2))   # J/kg → risk band
OVERDEV_UNCAPPED_CIN = -25   # cin above (weaker than) this = convection fires freely
```

```
risk   = 0 if cape < 300, 1 if < 800, 2 if < 1500, else 3
risk  += 1  if cape >= 300 and cin_effective > -25     # high CAPE + no cap = fires without warning
result = clamp(risk, 0, 3)
null   if cape is null
```
Uses `cape` (median). `cape_max` is stored separately so the UI can show the ensemble worst case —
the *rule-facing* number stays the median so a single outlier member cannot red-light a whole day.

### 6.5 `blue_thermal` — 0 or 1

```python
BLUE_THERMAL_LFC_LCL_GAP_M = 500
```

```
result = 1 if (lfc - lcl) > 500 else 0
null   if lfc is null or lcl is null
```

> Interpretation taken from `.ai/context/lsmfapi-thermal-grid.md:117`, which is the project's own
> field-semantics reference: *"lfc≈lcl → strong cumulus. lfc>>lcl → blue thermals."* The threshold
> is a named constant precisely because it is a documented rule of thumb, not a measured one — a
> pilot may want to retune it.

### 6.6 `turbulence_index` — integer 0–3

```python
TKE_BANDS = ((1.0, 0), (2.0, 1), (5.0, 2))   # J/kg → index
```

```
result = 0 if tke < 1, 1 if < 2, 2 if < 5, else 3     # 2+ = rough air
null   if tke is null
```

### 6.7 Day-window metrics — API layer, not stored

`thermal_window_start` / `thermal_window_end` (first/last hour of a local day with
`thermal_strength >= 2`) are a **presentation** concern computed in the router from the stored
hourly series. Do not add them as InfluxDB fields — they would need recomputing on every
timezone/threshold change.

---

## 7. File-by-file changes

### Phase 1 — Ingestion (foundation, must land first)

| # | File | Change |
|---|---|---|
| T01 | `src/lenticularis/services/thermal.py` | **NEW.** All §6 pure functions + threshold constants |
| T02 | `src/lenticularis/models/weather.py` | **NEW models** `ThermalForecastPoint`, `ThermalGridForecastPoint` (§5.3) |
| T03 | `src/lenticularis/collectors/forecast_thermal_swissmeteo.py` | **NEW collector** (§7.1) |
| T04 | `src/lenticularis/database/influx.py` | `write_thermal_forecast()`, `query_thermal_forecast_for_stations()`, `query_thermal_forecast_snapshot_for_stations()` (§7.2) |
| T05 | `src/lenticularis/scheduler.py` | New `forecast_thermal` job (§7.3) |
| T06 | `tests/backend/test_thermal_derived.py` | **NEW.** Unit tests for every §6 function incl. null paths |
| T07 | `tests/backend/test_thermal_collector.py` | **NEW.** Collector against a fixture payload incl. null frames |

### Phase 2 — Rules + station detail

| # | File | Change |
|---|---|---|
| T08 | `src/lenticularis/models/rules.py` | Extend `FieldName` (§7.4) |
| T09 | `src/lenticularis/rules/evaluator.py` | Extend `FIELD_MAP`; merge thermal into `station_data` at 3 sites (§7.4) |
| T10 | `tests/backend/test_rules_thermal.py` | **NEW.** Thermal conditions across live + forecast paths |
| T11 | `src/lenticularis/api/routers/stations.py` | `GET /api/stations/{station_id}/thermal-forecast` (§7.5) |
| T12 | `static/station-detail.html` + `.js` | Thermal panel: 4 new chart cards (§7.6) |
| T13 | `static/ruleset-editor.html` + `.js` | New fields in the condition-field dropdown + units |
| T14 | `static/i18n/{en,de,fr,it}.json` | `thermal.*` block — **all four locales** |

### Phase 3 — Map layer

| # | File | Change |
|---|---|---|
| T15 | `src/lenticularis/database/influx.py` | `write_thermal_forecast_grid()`, `query_thermal_forecast_grid()` |
| T16 | `src/lenticularis/collectors/forecast_thermal_swissmeteo.py` | Emit grid points from the **same** parsed payload |
| T17 | `src/lenticularis/api/routers/thermal_forecast.py` | **NEW router**, modelled on `wind_forecast.py` |
| T18 | `src/lenticularis/api/main.py` + `routers/pages.py` | Register router; add `/thermal-forecast` **page route in `pages.py`, never `main.py`** |
| T19 | `static/thermal-forecast.html` + `.js` | Map page, cloned from `wind-forecast.*` |
| T20 | All 14 `static/*.html` | Nav link (nav markup is duplicated per page — every file) |

### Phase 4 — Docs & release

| # | File | Change |
|---|---|---|
| T21 | `pyproject.toml` | `version = "1.21.0"` — **mandatory**, static assets changed (`04-constraints.md`) |
| T22 | `.ai/context/architecture.md` | Both measurements, both endpoints, derived-metric table |
| T23 | `.ai/context/features.md` | v1.21 milestone entry |
| T24 | `.ai/context/lsmfapi-thermal-grid.md` | **Correct the payload table** — it claims ~0.5 MB at `stride_km=10`; measured **28.6 MB** (§2.5). Also drop the "serves FGA stations only" note (§3.3) and the "Integration ideas" section, now superseded by this plan |
| T25 | `.ai/instructions/01-project-overview.md` | Add thermal grid to the SwissMeteo data-sources row |
| T26 | `README.md` | Feature + API sections |

---

### 7.1 The collector — `collectors/forecast_thermal_swissmeteo.py`

Does **not** subclass `BaseForecastCollector`: that ABC is per-station
(`collect_for_station(station_id, ...)`), and this endpoint is one spatial request for everything.
Model it on `ForecastGridSwissMeteoCollector` (`collectors/forecast_grid_swissmeteo.py`), which is
a plain class for the same reason.

```python
class ForecastThermalSwissMeteoCollector:
    SOURCE = "swissmeteo"
    MODEL  = "icon-ch"

    def __init__(self, base_url: str = "https://lsmfapi-dev.lg4.ch") -> None: ...

    async def fetch(self) -> dict | None:
        """ONE request. httpx timeout=300, Accept-Encoding: gzip.

        Returns the parsed payload, or None on 503 cache_warming (log WARNING, no raise —
        a warming upstream is expected, not exceptional).
        """

    def build_station_points(
        self, payload: dict, stations: list[WeatherStation],
    ) -> list[ThermalForecastPoint]:
        """Nearest grid point per station, then derive. Pure/sync — call via asyncio.to_thread."""

    def build_grid_points(self, payload: dict) -> list[ThermalGridForecastPoint]:
        """Phase 3. Same payload — never re-fetch."""
```

**Mandatory implementation notes:**

1. **`asyncio.to_thread` for parse + build.** 28.6 MB of JSON and 62 799 Pydantic objects will
   stall the event loop otherwise. `04-constraints.md` "Blocking the async event loop" is explicit,
   and lsmfapi itself shipped v0.3.5 to fix exactly this class of bug. `response.json()` counts as
   blocking work here.
2. **Nearest-point mapping: reuse `haversine_m()` from `services/dedup.py`.** Never redefine a
   distance helper (`01-project-overview.md:54`). Compute the mapping **once per run** —
   519 × 1272 = 660 k evaluations, ~1 s in pure Python, negligible next to the 4.1 s fetch. Do not
   recompute per frame. `scheduler.py:810 _build_station_grid_mapping` is the existing precedent
   for the shape of this.
3. **Skip stations with no `latitude`/`longitude`** — same guard as
   `forecast_swissmeteo.py:73`.
4. **Null-check per field per frame** (§3.2). Suggested shape:
   ```python
   def _at(frame: dict, field: str, j: int) -> float | None:
       arr = frame.get(field)
       if not arr or j >= len(arr):
           return None
       v = arr[j]
       return float(v) if v is not None else None
   ```
5. **Emit nothing for an all-null frame.** If every raw field at index `j` is null, skip the point
   entirely — do not write a row carrying only `init_time`.
6. **Log the non-null frame count once per run** at INFO, e.g.
   `[Lenti:thermal-collector] init=%s model=%s frames=%d usable=%d points=%d`. Per
   `08-operability.md`, a silently degraded upstream must be visible.
7. **Re-collection guard (optional, worth having).** Skip the write when
   `(init_time, model, usable_frame_count)` is identical to the last successful run — the model
   refreshes 4×/day but the job runs hourly, so this drops ~75 % of writes. Include
   `usable_frame_count` in the key, **not just `init_time`**: §3.2 shows frames arriving late for
   an unchanged `init_time`, and keying on `init_time` alone would freeze a partial grid in place
   for the rest of the cycle.

### 7.2 InfluxDB methods — `database/influx.py`

`write_thermal_forecast(points: list[ThermalForecastPoint])` — copy the structure of
`write_forecast` (`influx.py:808`) exactly:

- tags `station_id` / `network` / `source` / `model` / `init_date`
- `.time(int(fp.valid_time.timestamp()), "s")`
- **skip `None` fields** — `if value is not None: p = p.field(...)`
- write `init_time` as an ISO **string** field last
- **chunk at 5000** like `write_forecast_grid` (`influx.py:911`) — 62 799 points in one write
  request will time out. `write_forecast` does *not* chunk; that is fine for its smaller batches
  but not for this one.
- ⚠️ **Never set the same field key twice on one point** — Flux silently drops one
  (`04-constraints.md` T19).

`query_thermal_forecast_for_stations(station_ids, horizon_hours=120)` — return the **same shape**
as `query_forecast_for_stations` (`influx.py:1101`): `{station_id: {valid_time_iso: {field: val}}}`.
Dedup to the latest `init_time` per `valid_time` in Python, and reuse the same 3-day `init_date`
cutoff filter (`influx.py:1145`) so old runs are not pulled.

`query_thermal_forecast_snapshot_for_stations(station_ids, valid_time)` — mirror
`query_forecast_snapshot_for_stations` (`influx.py:280`), ±30 min window.

⚠️ **Flux injection.** Station IDs reaching these methods must already be allowlist-validated at
the router (`04-constraints.md` T01). Interpolate through the existing `_flux_str()` helper
(`influx.py:32`) — never raw f-string a caller-supplied id.

### 7.3 Scheduler — `scheduler.py`

Add `_run_thermal_forecast_collector()` modelled on `_run_grid_forecast_collector`
(`scheduler.py:704`):

- health key `"forecast_thermal"`, registered in `self._collector_health` with the same dict shape
- `IntervalTrigger(minutes=60)`, `id="forecast_thermal"`, `misfire_grace_time` consistent with the
  neighbouring jobs
- **`base_url`**: reuse the `swissmeteo` forecast-collector config exactly as
  `scheduler.py:726-746` does — walk `forecast_collectors` for `name == "swissmeteo"` and read
  `config.base_url`. **No new config key.** Never read `os.environ` (`04-constraints.md`).
- wrap the Influx write in `run_in_executor` / `asyncio.to_thread` (`scheduler.py:779` precedent)
- add to the manual-trigger map alongside `scheduler.py:377`'s `forecast_grid` entry
- on failure: `logger.error(..., exc_info=True)` and record it in health — **never** swallow
  (`04-constraints.md` T18)

There is **no Open-Meteo fallback** for thermal data; if lsmfapi is down, the run records an error
and the next hour retries. Do not invent a fallback source.

### 7.4 Rules integration

**`models/rules.py`** — extend `FieldName` (currently line 13):

```python
FieldName = Literal[
    "wind_speed", "wind_gust", "wind_direction",
    "temperature", "humidity", "pressure", "pressure_delta",
    "precipitation", "snow_depth",
    "foehn_active",
    # thermal (forecast-only — see note below)
    "thermal_ceiling", "cloud_base_agl", "thermal_strength",
    "overdevelopment_risk", "blue_thermal", "turbulence",
    "cape", "cloud_cover", "solar", "freezing_level",
]
```

**`rules/evaluator.py`** — extend `FIELD_MAP` (line 81):

```python
    "thermal_ceiling":      "thermal_ceiling_m",
    "cloud_base_agl":       "cloud_base_agl_m",
    "thermal_strength":     "thermal_strength",
    "overdevelopment_risk": "overdevelopment_risk",
    "blue_thermal":         "blue_thermal",
    "turbulence":           "turbulence_index",
    "cape":                 "cape",
    "cloud_cover":          "cloud_cover",
    "solar":                "solar",
    "freezing_level":       "freezing_level",
```

**The clean seam**: `_eval_condition` (`evaluator.py:164`) reads `station_data[station_id][field]`.
Merge the thermal dict into that per-station dict at fetch time and **no evaluator logic changes
at all** beyond `FIELD_MAP`. Three sites build `station_data`:

| Site | Path | Merge |
|---|---|---|
| `evaluator.py:350-361` | live evaluation | Thermal is inherently a forecast quantity. Fetch `query_thermal_forecast_snapshot_for_stations(ids, now)` and merge over the live dict |
| `evaluator.py:509-524` | forecast snapshot | Same snapshot method at the target `valid_time` |
| `evaluator.py:668, 687` | forecast horizon sweep | `query_thermal_forecast_for_stations(...)`, merge per `valid_time` bucket |

⚠️ **Merge direction matters.** Thermal field names are disjoint from observation field names, so
`{**live, **thermal}` is safe today — but write it as an explicit key-by-key update of only the
thermal field names so a future name collision cannot silently shadow an observation.

⚠️ **Batch, never loop.** One `query_thermal_*_for_stations(all_ids)` call per evaluation, never
per-station in a loop (`04-constraints.md` T09).

⚠️ **Note for the implementer**: a thermal condition on a *live* decision is answered from the
forecast row valid at the current hour — there is no observed CAPE or LCL. Make that explicit in
the docstring and in `help.html` so it is not read as a bug later.

### 7.5 Station thermal API — `api/routers/stations.py`

```
GET /api/stations/{station_id}/thermal-forecast?hours=120
```

```jsonc
{
  "station_id": "meteoswiss-INT",
  "elevation_m": 577,
  "init_time": "2026-07-31T00:00:00+00:00",
  "model": "icon-ch1+ch2",
  "hours": [
    { "valid_time": "...", "solar": 612.0, "lcl": 2350.0, "lcl_min": 2100.0, "lcl_max": 2600.0,
      "cape": 340.0, "cape_max": 780.0, "cin": null, "cloud_cover": 35.0, "tke": 1.2,
      "freezing_level": 3800.0, "thermal_ceiling_m": 2350.0, "cloud_base_agl_m": 1773.0,
      "thermal_strength": 4, "overdevelopment_risk": 2, "blue_thermal": 0, "turbulence_index": 1 }
  ],
  "day_windows": [ { "date": "2026-07-31", "start": "10:00", "end": "17:00" } ]
}
```

- Validate `station_id` against the allowlist **before** it reaches `influx.py` (T01 pattern:
  `^[\w\-.]{1,64}$`) — 404 on mismatch.
- Declare this route **before** any catch-all `/{station_id}` route in the file — the
  `forecast-accuracy-ranking` endpoint already had to be ordered this way
  (`.ai/context/forecast-analysis-wip.md`).
- `async def` + `await asyncio.to_thread(influx....)` — never a bare sync Influx call in an async
  handler (`04-constraints.md` T07).
- `day_windows` computed here per §6.7.
- Errors via `HTTPException` / `AppException` so the `{"error":{code,message,details}}` envelope
  holds (`04-constraints.md` T12).

### 7.6 Station-detail thermal panel

`static/station-detail.html` already has a `chartsGrid` of `chart-card` divs (lines 272–320). Add
four cards following that exact markup pattern:

| Card id | Content |
|---|---|
| `card-thermal_ceiling` | `thermal_ceiling_m` line + `lcl_min`/`lcl_max` band + `freezing_level` reference line + station elevation baseline |
| `card-thermal_strength` | `thermal_strength` 0–5 bars, coloured; `overdevelopment_risk` overlay |
| `card-convection` | `cape` line + `cape_max` band + `cin` on a second axis (render `null` cin as "no cap") |
| `card-solar` | `solar` line + `sunshine` bars + `cloud_cover` |

Follow `03-frontend-conventions.md` throughout:
- vanilla JS in `<script type="module">`, self-hosted Chart.js from `static/vendor/` — **no CDN,
  no npm, no build step** (`04-constraints.md`)
- dark-theme colour tokens, not hardcoded hex
- `fetchAuth()` for the API call
- the mandatory console-logging policy — log fetch start/end and point counts
- `textContent`, never `innerHTML`, for any server-supplied string (T03)
- every visible string via a `thermal.*` i18n key in **all four** locales

---

## 8. Constitution check (`.ai/instructions/00-ai-usage.md`)

| # | Principle | Status | Note |
|---|---|---|---|
| 1 | Read before acting | ✅ | Every claim cites `file:line` or a live probe dated 2026-07-31 |
| 2 | Plan before building | ✅ | This document; `tasks.md` next |
| 3 | Minimal scope | ✅ | `vertical_wind` excluded (no data); ensemble spread limited to 5 fields; no lsmfapi changes; no refactor of the existing `wind_forecast_grid` init_date quirk |
| 4 | Tool-agnostic instructions | ✅ | Nothing written outside `.ai/` and `specs/` |
| 5 | Keep docs in sync | ✅ | T22–T26, incl. correcting a wrong figure in an existing context file |
| 6 | No secrets committed | ✅ | No new config key; reuses the existing `swissmeteo` block |
| 7 | Prod is off-limits | ✅ | Ships as a normal image. Research used **read-only** SSH (`docker exec … httpx.get`) per `05-user-profile.md` |

### Constraint compliance (`04-constraints.md`)

| Constraint | Compliance |
|---|---|
| Static assets → version bump | T21: 1.20.0 → **1.21.0** (mandatory — T12/T13/T19/T20 all touch `static/`) |
| i18n all four locales | T14 |
| No npm / no CDN | Chart.js + Leaflet from `static/vendor/` |
| No Alembic | No SQLite change at all — thermal data is InfluxDB-only |
| No `print` | `logging` throughout |
| No `os.environ` | `base_url` via the existing `swissmeteo` collector config |
| Page routes in `pages.py` | T18 |
| Flux injection | `_flux_str()` + router-level allowlist |
| Event loop | `to_thread` on fetch/parse/build/write |
| Batch Influx | One `query_thermal_*_for_stations` per evaluation |
| Duplicate field keys | Called out explicitly in §7.2 |
| Swallowed exceptions | `logger.exception` + health record; 503 `cache_warming` is the one *expected* non-error path |
| Error envelope | `HTTPException` / `AppException` only |

---

## 9. Testing (`.ai/instructions/06-testing-conventions.md`)

pytest, in-memory SQLite, `FakeInflux`, **no network**. Run with
`.venv/Scripts/python.exe -m pytest`; lint with `ruff check --isolated`
(plain `ruff check` breaks on the pyproject caret — see `.ai/` dev-commands reference).

**T06 — `services/thermal.py` unit tests.** The highest-value tests in this change set; pure
functions, no fixtures needed.
- Each §6 function at every band boundary (exactly-on-threshold and either side).
- `cin=None` → treated as `0.0`, and specifically **not** as a strong cap.
- `solar=None` → `thermal_strength is None`.
- `cape=None` but `solar` present → `thermal_strength` computed without the cape bonus.
- `thermal_ceiling_m` with one of the two inputs null, and with both null.
- `cloud_base_agl_m` clamps to 0 when `lcl < elevation_m`.
- Clamping: a stacked-penalty case cannot go below 0; a stacked-bonus case cannot exceed 5.

**T07 — collector tests.** Build a **small hand-written fixture payload** (say 4 grid points ×
5 frames) — do **not** commit a 28.6 MB capture. It must include:
- a fully-null frame → **zero** points emitted for it
- a partially-null frame (`solar` null, `cin`/`lcl` present) → point emitted, `solar` field absent
- `cin: null` → field absent from the written point, but derived metrics computed as if 0
- a station whose nearest grid point is unambiguous → assert the chosen index
- a station with `latitude=None` → skipped entirely

**T10 — rules tests.** `SimpleNamespace` duck-typing per `06-testing-conventions.md`:
- a `thermal_strength >= 3 → green` condition matching and not matching
- a thermal condition where the station has **no** thermal row → `(False, None)`, same as any
  other missing-data field
- one test per merge site (live / snapshot / horizon sweep) proving the thermal field is visible
- a mixed ruleset (wind + thermal conditions) evaluating correctly end-to-end

**Regression guard:** existing `weather_forecast` behaviour must be untouched. Assert that a
ruleset with no thermal conditions produces a byte-identical decision before and after.

---

## 10. Ordered task list

Ship Phase 1, verify data lands, then continue. Do not start Phase 2 against an empty measurement.

```
Phase 1 — ingestion  (no user-visible change; verify in InfluxDB)
  T01  services/thermal.py + constants
  T06  unit tests for T01                          ← write with T01, not after
  T02  Pydantic models
  T04  write_thermal_forecast + 2 query methods
  T03  forecast_thermal_swissmeteo.py collector
  T07  collector tests
  T05  scheduler job registration
  ── STOP. User syncs + restarts. Confirm rows in weather_forecast_thermal. ──

Phase 2 — rules + station detail
  T08  FieldName
  T09  FIELD_MAP + 3 merge sites
  T10  rules tests
  T11  GET /api/stations/{id}/thermal-forecast
  T14  i18n (before T12/T13 so the markup has keys to reference)
  T12  station-detail thermal panel
  T13  ruleset-editor field dropdown
  ── STOP. Sync + verify in the browser. ──

Phase 3 — map layer
  T15  grid write + query
  T16  grid points from the same payload
  T17  routers/thermal_forecast.py
  T18  register router + page route in pages.py
  T19  static/thermal-forecast.{html,js}
  T20  nav link in all 14 HTML files

Phase 4 — release
  T21  version bump 1.21.0            ← REQUIRED, static assets changed
  T22–T26  docs sync (.ai/ + README)
```

**Deployment**: per `.ai/instructions/05-user-profile.md`, stop after each phase and report what
changed. **Never** rsync, scp, or restart containers — the user syncs and restarts manually.

---

## 11. Open questions for the user

Not blocking — the plan states a default for each. Confirm or override.

1. **Thermal condition semantics on live decisions.** A thermal condition on a *live* traffic
   light is necessarily answered from the forecast row valid at the current hour, since CAPE/LCL
   are not observed anywhere. **Default: allow it**, documented in `help.html`. The alternative is
   restricting thermal fields to forecast-mode rulesets only.
2. **`stride_km`.** **Default: 10** (1272 points, 28.6 MB, 4.1 s). `stride_km=5` quadruples points
   and would need a bbox split to stay under lsmfapi's `_MAX_RESPONSE_CELLS = 10 000 000` cap.
3. **Ensemble spread selection.** **Default: the 5 fields in §5.1.** Say the word if `cape_min`,
   `tke_max`, or the full 24 are wanted.
4. **`thermal_strength` weights.** §6.3's bands are a defensible first cut, not a validated model.
   They are named constants specifically so they can be retuned against real flying days.
5. **Phase 3 scope.** A whole new map page is the bulk of the frontend work. An alternative is
   adding a thermal *layer toggle* to the existing `/wind-forecast` page — cheaper, and arguably
   better UX since wind and thermals are read together. **Default: separate page** (matches the
   existing one-page-per-concern layout), but worth a decision before T17.
