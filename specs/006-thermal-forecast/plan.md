# Implementation Plan: Thermal Forecasting from LSMFAPI

**Phase**: 2 — Plan · **Date**: 2026-07-31 · **Revised**: 2026-08-02
**Target version**: v1.22.2 → **v1.23.0**
**Tasks**: [tasks.md](./tasks.md)
**Audience**: this document is written to be implemented by an agent that has *not* seen the
research session. Everything needed is inline. Read `.ai/instructions/` first anyway.

> **Revision note (2026-08-02).** Authored against v1.20.0; re-verified against v1.22.2 after
> 007 and 008 shipped. §3.5 lists what was stale and is now corrected — most importantly the
> `station_data` merge sites went from 3 to **6**. §12 lists four additions from the same review.
> The original target of v1.21.0 was taken by 007; this now ships as **v1.23.0**.

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

### 3.4 The thermal grid and the wind grid are the SAME 1272 points (verified 2026-08-02)

Verified in the lsmfapi source, not inferred. `/api/forecast/grid` and `/api/forecast/thermal-grid`
share one default bbox constant (`LSMFAPI forecast.py:45`, from `_icon_eps_base.py:71-75`:
lat 45.8–47.9, lon 5.9–10.6), both default `stride_km=10`, and both build their point list with
**character-for-character identical** code (`forecast.py:196-202` vs `:288-294`), rounding to 5 dp
identically (`:225-228` vs `:317-320`). Result: 24 lats × 53 lons = **1272 points**, first
`(47.9, 5.9)`, emitted row-major lat-DESC / lon-ASC in both. Formatting all 1272 to `.4f` gives
1272 **collision-free** `grid_id`s (spacing 0.0901° ≫ 0.0001°).

Lenticularis' wind collector sends neither `bbox` nor `stride_km`
(`collectors/forecast_grid_swissmeteo.py:51`), so it receives exactly that default grid, and
`wind_forecast.py:122` already canonicalises to the same lat-DESC/lon-ASC order.

**Consequence: `thermal_forecast_grid` and `wind_forecast_grid` join on `(grid_id, valid_time)`,
and index `j` refers to the same physical cell in both.** This is load-bearing for the feature
being *useful* — see §6.9. Requirements it imposes:

1. The thermal collector **must** use `f"{lat:.4f}_{lon:.4f}"` (§5.2 already prescribes this).
   Any other format silently breaks the join with zero errors.
2. Do **not** join on `init_date` across the two measurements — the caches fill separately and
   carry different `init_time`s (§3.1). Join on `grid_id` + `valid_time` only.
3. If the Open-Meteo grid fallback ever runs (`forecast_grid.py:164` — 171 points, 2 dp, 0.25°
   lattice), those `grid_id`s will not join and will produce zero matches, silently. Phase 3 must
   tolerate a wind lookup miss rather than assume it.

### 3.5 The plan predates v1.21.0 and v1.22.x — re-verified 2026-08-02

This document was written on 2026-07-31 against **v1.20.0**. Since then `007-replay-aware-ruleset-decisions`
(v1.21.0) and `008-progressive-map-loading` (v1.22.0) shipped, plus fixes through v1.22.2. Corrections
applied throughout this document on 2026-08-02:

| Was | Now |
|---|---|
| 3 `station_data` merge sites | **6** — `run_forecast_evaluation_at` (new in 007) and `public_map.py` were missing (§7.4) |
| station `elevation_m` | `WeatherStation.elevation: Optional[int]`; coords are `latitude`/`longitude` (`models/weather.py:14-43`) |
| "All 14 `static/*.html`" | **17** files exist; ~10 need editing — 4 pages inject nav from `bootstrap.js:19-33` (T20) |
| "clone `wind-forecast.*`" | There is no `wind-forecast.js` — the page is one 597-line HTML with a 348-line inline module (T19) |
| mirror `query_forecast_*` clients | Both new query methods use `_slow_query_api`, per the v1.22.2 fix (§7.2) |

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

**Updated 2026-08-02: 005 is shelved**, not in-flight. It is blocked on an unresolved engine
decision (D1: InfluxDB 3 licensing/monetisation; D2, added 2026-08-02: whether to target
Postgres/TimescaleDB instead) and never reached `tasks.md`. There is no in-progress query style to
match. **Action for the implementer**: write T04/T11 in the current InfluxDB 2.x Flux style, same
as every other measurement in `influx.py`. Re-check this section if 005 is reactivated before 006
ships.

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
`thermal_strength`, `overdevelopment_risk`, `blue_thermal`, `turbulence_index`,
`ceiling_spread_m`

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
    ceiling_spread_m: Optional[float] = None   # §6.8 — ensemble confidence


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

> ⚠️ The pure function's parameter is named `elevation_m`, but the attribute on the station object
> is **`WeatherStation.elevation`** (`Optional[int]`, `models/weather.py:20`) — there is no
> `elevation_m` and no `altitude` on that model. Read it as
> `getattr(station, "elevation", None)`. **`scheduler.py:840` gets this wrong today**
> (`getattr(station, "altitude", None)`, always `None`, so every station silently falls through to
> `level_hpa = 950`); do not copy that line. Logged as a separate pre-existing bug — not fixed here.

### 6.3 `thermal_strength` — integer 0–5

```python
THERMAL_SOLAR_BANDS = ((150, 0), (300, 1), (500, 2), (700, 3), (850, 4))  # W/m² → base score
THERMAL_CAPE_BONUS_JKG      = 300     # cape at/above this → +1
THERMAL_CLOUD_PENALTY_PCT   = 70      # cloud_cover above this → -1
THERMAL_CLOUD_HEAVY_PCT     = 90      # cloud_cover above this → -2 (instead of -1)
THERMAL_CIN_PENALTY_JKG     = -100    # cin at/below this → -1
THERMAL_SUNSHINE_PATCHY_MIN = 30      # min/h — sun out less than half the hour → -1
```

```
base   = 0 if solar < 150, 1 if < 300, 2 if < 500, 3 if < 700, 4 if < 850, else 5
score  = base
score += 1  if cape is not null and cape >= 300
score -= 2  if cloud_cover is not null and cloud_cover > 90
       else -1  if cloud_cover is not null and cloud_cover > 70
score -= 1  if cin_effective <= -100          # cin_effective = 0.0 when cin is null (§2.4)
score -= 1  if sunshine is not null and sunshine < 30
            and (cloud_cover is null or cloud_cover <= 70)      # see note below
result = clamp(score, 0, 5)
null   if solar is null      # solar is the trigger; without it there is no forecast to give
```

A missing *modifier* (`cape`, `cloud_cover`, `sunshine`) simply contributes nothing — that is
intended. A missing `solar` makes the whole index null.

> **Why the `sunshine` term is guarded on `cloud_cover`.** §2.3 documents sunshine as a real signal
> ("high variance = patchy = uneven thermals") and the original draft of this plan then never used
> it — an internal contradiction, fixed here. But `sunshine` and `cloud_cover` are strongly
> correlated, so an unconditional penalty would double-count the same overcast sky already punished
> by the cloud term. The guard makes it *additive-only*: it fires when the sun is out less than half
> the hour **and** total cloud cover did not already flag it — i.e. thin or broken cloud that
> `cloud_cover` alone misses. `sunshine_min`/`sunshine_max` are not stored (§5.1), so true ensemble
> variance is unavailable; this is the median-only approximation of the same idea.

### 6.4 `overdevelopment_risk` — integer 0–3

```python
OVERDEV_CAPE_BANDS    = ((300, 0), (800, 1), (1500, 2))   # J/kg → risk band
OVERDEV_UNCAPPED_CIN  = -25   # cin above (weaker than) this = convection fires freely
OVERDEV_CLOUD_MID_PCT = 50    # mid-level cloud above this, with CAPE present → +1
```

```
risk   = 0 if cape < 300, 1 if < 800, 2 if < 1500, else 3
risk  += 1  if cape >= 300 and cin_effective > -25     # high CAPE + no cap = fires without warning
risk  += 1  if cape >= 300 and cloud_mid is not null and cloud_mid > 50
result = clamp(risk, 0, 3)
null   if cape is null
```
Uses `cape` (median). `cape_max` is stored separately so the UI can show the ensemble worst case —
the *rule-facing* number stays the median so a single outlier member cannot red-light a whole day.

> **Why `cloud_mid` is now a term.** §2.3 calls `cloud_mid` the "overdevelopment cap indicator" and
> the original draft then computed overdevelopment risk from `cape` + `cin` only, never touching it
> — the same documented-but-unused contradiction as `sunshine` in §6.3. Mid-level cloud on a day
> that already has CAPE is a classic pre-overdevelopment signal (spreading altocumulus / early
> anvil). Gated on `cape >= 300` so that mid cloud on a stable day — which is just shade, not
> overdevelopment — contributes nothing. `null` `cloud_mid` contributes nothing, per §3.2 rule 2.

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

### 6.7 `ceiling_spread_m` — ensemble confidence

```
inputs:  lcl_min, lcl_max   (m ASL)
result:  lcl_max - lcl_min
         null if either is null
```

The single number that tells a pilot whether to trust the cloud base. "2400 m ± 150 m" is a plan;
"2400 m ± 900 m" is a coin flip, and today those two forecasts are indistinguishable in every UI
we have. Raw metres, not a band — the UI and rules can threshold it (`ceiling_spread < 400`), and
unlike a 0–3 index it needs no retuning.

> This is the only derived metric that uses the ensemble at all. §5.1 stores five `_min`/`_max`
> fields and, without this, would derive nothing from any of them.

### 6.8 Day-window metrics — API layer, not stored

`thermal_window_start` / `thermal_window_end` (first/last hour of a local day with
`thermal_strength >= 2`) are a **presentation** concern computed in the router from the stored
hourly series. Do not add them as InfluxDB fields — they would need recomputing on every
timezone/threshold change.

The window bounds alone under-serve the actual question, which is "is Saturday worth the drive?".
Compute the full per-day summary in the router (§7.5 shows the shape):

| Key | Derivation over the local day's hours |
|---|---|
| `start` / `end` | first / last local hour with `thermal_strength >= 2` |
| `hours` | count of hours with `thermal_strength >= 2` (**not** `end - start`; the window can have holes) |
| `best_hour` | local hour of max `thermal_strength`; ties → earliest |
| `best_strength` | that maximum |
| `peak_cloud_base_agl_m` | max `cloud_base_agl_m` across the window hours |
| `peak_ceiling_m` | max `thermal_ceiling_m` across the window hours |
| `max_overdevelopment_risk` | max `overdevelopment_risk` across the window hours |
| `median_ceiling_spread_m` | median `ceiling_spread_m` across the window hours (§6.7) |

All are `null` when the day has no qualifying hour. Emit **no** `day_windows` entry for a day with
zero usable frames, rather than an entry full of nulls.

> ⚠️ **Known limitation, accepted for now.** A day that is *partly* null (§3.2) produces a window
> computed only over the hours that exist — so a day whose morning is missing reports
> `start: "14:00"`, which reads as "thermals start at 2pm" when the truth is "we don't know about
> the morning". Surfacing a per-day `coverage` count in the API was considered and **deferred**
> (§11 decision 6). Revisit at T11 if the Phase 1 gate (§10) shows the null hole persists.

### 6.9 Wind × thermal — no new code, but design for it

A thermal number on its own is not a flying decision: 2400 m of cloud base under 45 km/h at ridge
height is a no-go, and nothing in §6.1–6.8 knows that. Wind is already in the stack, so this
requires no new collector, measurement, or fetch — only that we build deliberately rather than
discover it by accident:

| Layer | What is already true | What this plan must do |
|---|---|---|
| **Rules (Phase 2)** | Once thermal fields are merged into `station_data` (§7.4), wind and thermal fields sit in the **same flat dict**. `thermal_strength >= 3 AND wind_speed < 25` therefore works with zero evaluator changes | Cover it with an explicit test (T10) so it is a supported capability, not an accident |
| **Map (Phase 3)** | `thermal_forecast_grid` and `wind_forecast_grid` are the same 1272 cells (§3.4) | Overlay the existing wind arrows on the thermal layer — same index `j`, no join code, no second fetch. Tolerate a lookup miss (§3.4 note 3) |

Deliberately **not** doing: a combined `flyability` metric in the collector. That would couple two
collectors with different `init_time`s (§3.1) and bake one pilot's wind tolerance into stored data.
The composition belongs in the ruleset, where each pilot sets their own threshold.

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
| T08 | `src/lenticularis/models/rules.py` | Extend `FieldName` — 11 new keys incl. `ceiling_spread` (§7.4) |
| T09 | `src/lenticularis/rules/evaluator.py` **+ `services/public_map.py`** | Extend `FIELD_MAP`; merge thermal into `station_data` at **5 of the 6 sites** in §7.4 (A, B, C, D, F — **not** E). Site F is in `public_map.py`, not `evaluator.py`, and skipping it silently drops showcase rule sets from the anonymous map |
| T10 | `tests/backend/test_rules_thermal.py` | **NEW.** Thermal conditions across all six merge sites, the public-map regression, and the wind × thermal composition (§9) |
| T11 | `src/lenticularis/api/routers/stations.py` | `GET /api/stations/{station_id}/thermal-forecast` (§7.5), incl. the 8-key `day_windows` summary (§6.8) |
| T12 | `static/station-detail.html` + `.js` | Thermal panel: 4 new chart cards (§7.6). Reuse `fcDatasets()` / `renderSimpleChart()` — no new charting code |
| T13 | `static/ruleset-editor.html` + 2 more | New fields in the editor's `FIELDS` array (`ruleset-editor.html:704-715`) **with units**. Also the two duplicated `FIELD_UNIT`/`FIELD_LABEL` copies at `index.html:637-653` and `ruleset-analysis.html:350-369`, else the map popup and analysis page render the new fields unlabelled. Consider extending `ai.py:65`'s prompt field list (already stale — it omits `foehn_active`) |
| T14 | `static/i18n/{en,de,fr,it}.json` | `thermal.*` block + `editor.fields.*` for the 11 new fields — **all four locales**, which are line-for-line parallel today (727/727/727/728) |

### Phase 3 — Map layer

| # | File | Change |
|---|---|---|
| T15 | `src/lenticularis/database/influx.py` | `write_thermal_forecast_grid()`, `query_thermal_forecast_grid()` |
| T16 | `src/lenticularis/collectors/forecast_thermal_swissmeteo.py` | Emit grid points from the **same** parsed payload |
| T17 | `src/lenticularis/api/routers/thermal_forecast.py` | **NEW router**, modelled on `wind_forecast.py` |
| T18 | `src/lenticularis/api/main.py` + `routers/pages.py` | Register router; add `/thermal-forecast` **page route in `pages.py`, never `main.py`** |
| T19 | `static/thermal-forecast.html` + `.js` | Map page, modelled on `wind-forecast.html`. ⚠️ There is **no** `wind-forecast.js` — that page is one 597-line HTML with a 348-line inline `<script type="module">` (lines 247-594). Extract the new page's logic to a real `.js` like `index.html`/`map.js` does, rather than cloning the inline pattern |
| T20 | Nav link — **13 places, not 14 files** | `static/` holds **17** HTML files, but 4 (`foehn`, `ruleset-editor`, `rulesets`, `stats`) inject nav from `bootstrap.js:19-33`, so editing `_NAV_HTML` covers all four at once. Then 9 inline navs: `admin:137`, `forecast-accuracy:285`, `forecast-analysis:274`, `help:196`, `index:250`, `ruleset-analysis:189`, `station-detail:227`, `stations:202`, `wind-forecast:190`. **Skip** `login`/`register` (3-link auth nav), `org-dashboard` (no `.nav-links`), `oauth-callback` (no nav) |

### Phase 4 — Docs & release

| # | File | Change |
|---|---|---|
| T21 | `pyproject.toml` | `version = "1.23.0"` — **mandatory**, static assets changed (`04-constraints.md`) |
| T22 | `.ai/context/architecture.md` | Both measurements, both endpoints, derived-metric table |
| T23 | `.ai/context/features.md` | v1.21 milestone entry |
| T24 | `.ai/context/lsmfapi-thermal-grid.md` + 2 more | **Correct the payload table** — it claims ~0.5 MB at `stride_km=10`; measured **28.6 MB** (§2.5). Drop the "serves FGA stations only" note (§3.3) and the "Integration ideas" section, superseded by this plan. **Also fix two unrelated doc bugs found 2026-08-02:** (a) `.ai/context/architecture.md:65` documents `wind_forecast_grid.init_date` as `YYYY-MM-DDTHH`, but `influx.py:898` writes `%Y-%m-%d`; (b) `.ai/context/features.md:258` and `.ai/context/forecast-analysis-wip.md:18,50` describe a `_ranking_client` / `ranking_query_timeout: 300000` that **does not exist** — `influx.py:49-66` has only `_client` and `_slow_client` |
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
2. **Nearest-point mapping: reuse `haversine_m()` from `services/dedup.py:44`.** Never redefine a
   distance helper (`01-project-overview.md:54`). Compute the mapping **once per run** —
   519 × 1272 = 660 k evaluations, ~1 s in pure Python, negligible next to the 4.1 s fetch. Do not
   recompute per frame. `scheduler.py:810 _build_station_grid_mapping` is the existing precedent
   for the shape of this — but note it calls a locally-defined `_haversine_sq` (`scheduler.py:650`),
   a second unreconciled distance implementation. Import the `dedup.py` one; do not add a third.
3. **Read station attributes by their real names.** `WeatherStation` (`models/weather.py:14-43`) has
   `latitude` / `longitude` (required floats) and **`elevation`** (`Optional[int]`) — there is no
   `elevation_m` and no `altitude`. Still guard on lat/lon being present, same as
   `forecast_swissmeteo.py:73`, since registry entries are built by collectors. Get the station list
   the same way `scheduler.py:638-643` does, including its empty-registry guard (`:645-655`) — the
   registry is populated asynchronously and an early run can legitimately see zero stations.
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
6. **Log the non-null frame count AND per-hour coverage once per run** at INFO:
   ```
   [Lenti:thermal-collector] init=%s model=%s frames=%d usable=%d points=%d
   [Lenti:thermal-collector] coverage today=%d/12 d1=%d/12 (local 08-19, non-null solar)
   ```
   Per `08-operability.md`, a silently degraded upstream must be visible. The second line is not
   decoration — it **is** the Phase 1 exit gate (§10.1), because the §3.2 null hole lands squarely
   on the flyable hours and a row-count check cannot see it.
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

⚠️ **Both new query methods must use `_slow_query_api` (60 s), not `_query_api` (10 s).**
Copy the *structure* of the two wind methods, not their client choice:

- `query_forecast_snapshot_for_stations` already uses `_slow_query_api` (`influx.py:303`) — it was
  switched in **v1.22.2** (the most recent commit) precisely because 10 s timeouts were making
  forecast evaluations spuriously report red via the v1.20.0 no-data fail-safe. Inherit that fix,
  do not re-earn it.
- `query_forecast_for_stations` still uses `_query_api` (`influx.py:1150`). **Do not mirror that.**
  The thermal horizon query is strictly larger (519 stations × 121 h × ~24 fields) and would hit
  the same 10 s wall.

> Note: `.ai/context/features.md:258` and `.ai/context/forecast-analysis-wip.md:18,50` describe a
> third `_ranking_client` with a 300 s `ranking_query_timeout`. **It does not exist in the code** —
> `influx.py:49-66` defines only `_client` (10 s) and `_slow_client` (60 s), and `config.py:8-15`
> has no `ranking_query_timeout`. Those docs are wrong; corrected in T24.

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
    "overdevelopment_risk", "blue_thermal", "turbulence", "ceiling_spread",
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
    "ceiling_spread":       "ceiling_spread_m",
    "cape":                 "cape",
    "cloud_cover":          "cloud_cover",
    "solar":                "solar",
    "freezing_level":       "freezing_level",
```

Add `"ceiling_spread"` to the `FieldName` Literal above as well — `FieldName` (`models/rules.py:13-18`)
and `FIELD_MAP` (`evaluator.py:81-94`) are duplicated lists with **no cross-check**, so a key added
to one and not the other fails silently at `_eval_condition:178` with only a `logger.warning`.

**The clean seam**: `_eval_condition` (`evaluator.py:164-200`) reads
`station_data[station_id][influx_field]`, `float()`-coerces it, and returns `(False, None)` when the
station or field is absent. Merge the thermal dict into that per-station dict at fetch time and
**no evaluator decision logic changes at all** beyond `FIELD_MAP` — the four duplicated decision
blocks are field-agnostic and need zero edits.

**There are SIX sites that build `station_data`, not three.** The original draft listed three and
was written before `run_forecast_evaluation_at` shipped in v1.21.0; `public_map.py` was missed
entirely. Verified 2026-08-02:

| # | Site | Path | Merge |
|---|---|---|---|
| A | `evaluator.py:350-363` (`run_evaluation`) | live evaluation | Thermal is inherently a forecast quantity. Fetch `query_thermal_forecast_snapshot_for_stations(ids, now)` and merge over the live dict |
| B | `evaluator.py:509-526` (`run_evaluation_at`) | observed snapshot at `at_time` | Same snapshot method at `at_time` |
| C | `evaluator.py:652-662` (`run_forecast_evaluation_at`) | **forecast snapshot — new in 007/v1.21.0** | Same snapshot method at the target `valid_time`. This is the function the replay-aware map calls on every scrub frame |
| D | `evaluator.py:736-740` (`run_forecast_evaluation`) | forecast horizon sweep | `query_thermal_forecast_for_stations(...)` once outside the loop (it is already hoisted at `:717`), merge per `valid_time` bucket |
| E | `evaluator.py:885-898` (`run_history_backfill`) | historical backfill | **Deliberately no thermal merge.** No thermal data exists before this feature starts collecting, so a merge would be uniformly empty. Must not crash — covered by a test (T10) |
| F | `services/public_map.py:96-120` (`_fetch_station_data`) | anonymous showcase map | **Must merge.** See the warning below |

⚠️ **Site F is a shipping bug if skipped.** `public_map.py` evaluates curated showcase rule sets for
anonymous visitors. If a showcase rule set carries a thermal condition and F does not fetch thermal
data, `_eval_condition` returns `(False, None)` → under v1.20.0 green-requirement semantics an unmet
GREEN becomes **red**, and per v1.19.0 the no-data rule set is **omitted from the public map
entirely**. A curated rule set would silently vanish with no error anywhere. Either merge at F, or
explicitly reject thermal conditions on showcase sets — merging is the smaller change.

⚠️ **Site C is the hot path.** 007 made the map call `run_forecast_evaluation_at` on every replay
frame through a coalescing queue. Adding a second Influx round-trip there doubles that path's query
count — one more reason both new query methods use the 60 s client (§7.2).

⚠️ **Merge direction matters.** Thermal field names are disjoint from observation field names, so
`{**live, **thermal}` is safe today — but write it as an explicit key-by-key update of only the
thermal field names so a future name collision cannot silently shadow an observation.

⚠️ **Batch, never loop.** One `query_thermal_*_for_stations(all_ids)` call per evaluation, never
per-station in a loop (`04-constraints.md` T09).

⚠️ **Note for the implementer**: a thermal condition on a *live* decision is answered from the
forecast row valid at the current hour — there is no observed CAPE or LCL (§11 decision 1). Make
that explicit in the docstring and in `help.html` so it is not read as a bug later.

> `help.html` is **not internationalised** — `data-i18n` appears on exactly 8 lines, all nav links,
> and the locale files have no `help` key at all. Add the thermal FAQ as plain English in a new
> `<div class="help-section" id="thermal">` plus a `.jump-link` in the jump bar (`help.html:211-224`),
> matching the 12 existing sections. Do **not** invent `help.*` i18n keys for it.

**Precedent worth knowing:** `ForecastPoint` carries no `snow_depth` and no `foehn_active`, so those
two fields already silently never match in forecast evaluation. Fields that exist in only one of the
two worlds are an established (if undocumented) condition in this codebase — thermal being
forecast-only is not a new class of problem.

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
    { "valid_time": "...", "solar": 612.0, "sunshine": 55.0, "lcl": 2350.0,
      "lcl_min": 2100.0, "lcl_max": 2600.0,
      "cape": 340.0, "cape_max": 780.0, "cin": null, "cloud_cover": 35.0, "cloud_mid": 20.0,
      "tke": 1.2, "freezing_level": 3800.0,
      "thermal_ceiling_m": 2350.0, "cloud_base_agl_m": 1773.0, "ceiling_spread_m": 500.0,
      "thermal_strength": 4, "overdevelopment_risk": 2, "blue_thermal": 0, "turbulence_index": 1 }
  ],
  "day_windows": [
    { "date": "2026-07-31", "start": "10:00", "end": "17:00", "hours": 7,
      "best_hour": "14:00", "best_strength": 4,
      "peak_cloud_base_agl_m": 1773.0, "peak_ceiling_m": 2350.0,
      "max_overdevelopment_risk": 2, "median_ceiling_spread_m": 320.0 }
  ]
}
```

- Validate `station_id` against the allowlist **before** it reaches `influx.py` (T01 pattern:
  `^[\w\-.]{1,64}$`) — 404 on mismatch.
- Declare this route **before** any catch-all `/{station_id}` route in the file — the
  `forecast-accuracy-ranking` endpoint already had to be ordered this way
  (`.ai/context/forecast-analysis-wip.md`).
- `async def` + `await asyncio.to_thread(influx....)` — never a bare sync Influx call in an async
  handler (`04-constraints.md` T07).
- `elevation_m` in the response is read from `WeatherStation.elevation` (§6.2 warning).
- `day_windows` computed here per §6.8 — all eight keys, and no entry at all for a day with zero
  usable frames.
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

Concrete anchors (verified 2026-08-02):
- Card markup pattern — `station-detail.html:291-295`; convention is `card-<field>` / `chart-<field>`,
  each starting `display:none` and revealed by `showCard()` (`station-detail.js:782-785`).
- **The ensemble band already exists — reuse it, do not rebuild.** `fcDatasets()`
  (`station-detail.js:118-155`) emits the exact 3-dataset min/max/probable pattern for
  `lcl_min`/`lcl_max` and `cape_max`. Dataset order is load-bearing (`fill: '-1'`), the legend must
  filter `(min)` (`:604`), and the tooltip must use `makeForecastFilter()` (`:107-116`), which folds
  the range onto the probable line by **timestamp lookup, not index**.
- `renderSimpleChart()` (`station-detail.js:~715-777`) already handles line/bar plus
  `yMin`/`yMax`/`yTickLabels`/`barThickness` — enough for the 0–5 and 0–3 index bars without new
  chart code.
- `CHART_DEFAULTS` at `:157-221`; palette `CHART_COLORS` at `:11-20`; forecast amber at `:103`.

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
| Static assets → version bump | T21: 1.22.2 → **1.23.0** (mandatory — T12/T13/T19/T20 all touch `static/`) |
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
- **`sunshine` guard (§6.3):** `sunshine=20, cloud_cover=85` → **no** sunshine penalty (the cloud
  term already fired); `sunshine=20, cloud_cover=40` → penalty applies; `sunshine=20,
  cloud_cover=None` → penalty applies; `sunshine=None` → no penalty, score otherwise unchanged.
- **`cloud_mid` gate (§6.4):** `cape=100, cloud_mid=90` → **no** bump (stable day, mid cloud is just
  shade); `cape=500, cloud_mid=90` → +1; `cape=500, cloud_mid=None` → no bump.
- **`ceiling_spread_m` (§6.7):** normal case; either input null → `None`; and `lcl_min == lcl_max`
  → `0.0`, which must stay distinguishable from `None`.

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
- **one test per merge site — all six of §7.4**, proving the thermal field is visible at A/B/C/D/F
  and that E (`run_history_backfill`) runs without thermal data and does not crash
- **site F specifically**: a showcase rule set with a thermal condition still appears on the public
  map with a correct decision — the regression guard for the silent-disappearance failure in §7.4
- **wind × thermal (§6.9)**: a mixed rule set (`thermal_strength >= 3` AND `wind_speed < 25`)
  evaluating correctly end-to-end, and flipping to red when only the wind leg fails. This is the
  test that makes the composition a supported feature rather than an accident
- `FieldName` / `FIELD_MAP` parity: assert the two lists have identical key sets, so the silent
  `logger.warning` path at `_eval_condition:178` can never be reached by a typo

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
  ── STOP. User syncs + restarts. See the coverage gate below. ──

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
  T21  version bump 1.23.0            ← REQUIRED, static assets changed
  T22–T26  docs sync (.ai/ + README)
```

### 10.1 The Phase 1 exit gate — coverage, not row count

**"Confirm rows exist in `weather_forecast_thermal`" is not a sufficient gate.** §3.2 measured the
00Z run as null across h+8…h+33 — from a 00Z init that is **08:00 today through 09:00 tomorrow**,
i.e. the entire flyable window of today and tomorrow morning. Rows *will* exist (h+0–7 and
h+34–120 are populated), so a row-count gate passes green while the feature shows nothing for the
only hours a pilot cares about. Phases 2 and 3 would then be built against a hollow measurement.

**Gate: non-null `thermal_strength` for local hours 08:00–19:00, for today and D+1.**

To make that observable, the collector's per-run INFO log (§7.1 note 6) must report **per-hour**
coverage, not just a usable-frame total:

```
[Lenti:thermal-collector] init=%s model=%s frames=%d usable=%d points=%d
[Lenti:thermal-collector] coverage today=%d/12 d1=%d/12 (local 08-19, non-null solar)
```

Outcomes:

| Result | Action |
|---|---|
| Both days ≥ 10/12 | Gate passes. Proceed to Phase 2 |
| Either day badly short | **Stop.** The hole is still open upstream. Report to the lsmfapi side and hold Phases 2–3 — the ingestion and its diagnostic are still worth having merged, but there is no point building UI against it yet |

This is why the collector, not a probe script, is the diagnostic: it runs hourly and will show the
hole closing (or not) without anyone re-probing by hand.

---

**Deployment**: per `.ai/instructions/05-user-profile.md`, stop after each phase and report what
changed. **Never** rsync, scp, or restart containers — the user syncs and restarts manually.

---

## 11. Decisions

Questions 1–5 were the original open list, all confirmed at their stated default on 2026-08-02.
Question 6 arose from the same-day review.

1. **Thermal condition semantics on live decisions.** ✅ **Allow it.** A thermal condition on a
   *live* traffic light is answered from the forecast row valid at the current hour, since CAPE/LCL
   are not observed anywhere. Must be documented in `help.html` (see §7.4 implementer note) so it
   is never mistaken for a bug.
2. **`stride_km`.** ✅ **10** (1272 points, 28.6 MB, 4.1 s). No bbox-splitting needed.
3. **Ensemble spread selection.** ✅ **The 5 fields in §5.1** (`lcl_min`, `lcl_max`, `cape_max`,
   `cloud_cover_max`, `solar_min`). No additions.
4. **`thermal_strength` weights.** ✅ **Accept §6.3's bands as-is for Phase 1.** Ship as named
   constants; retune later against real flying days if needed. (The `sunshine` term added in the
   2026-08-02 review is a *new* term, not a retune of these bands — see §12.)
5. **Phase 3 scope.** ✅ **Separate `/thermal-forecast` page** (matches the existing
   one-page-per-concern layout). T17–T20 proceed as written.
6. **Surfacing null coverage in the API/UI.** ✅ **Deferred.** The Phase 1 gate (§10.1) uses
   collector logging to detect the §3.2 null hole, and the API does **not** carry a `coverage`
   block. Accepted cost: a partially-null day produces a misleading `day_windows` entry (§6.8
   warning). Revisit at T11 if the gate shows the hole persists.

---

## 12. Review addenda — 2026-08-02

Added after re-verifying the plan against the v1.22.2 codebase. The plan was authored against
v1.20.0; §3.5 lists the staleness corrections. These four are *additions*, not corrections:

| # | Addition | Where | Why |
|---|---|---|---|
| 1 | **Wind × thermal made deliberate** | §3.4, §6.9, T10, T19 | Verified the thermal and wind grids are the same 1272 cells, so they join on `(grid_id, valid_time)` for free. A thermal number alone is not a flying decision; the composition already works in the rules engine and must be tested, and the Phase 3 map gets a wind overlay at no cost |
| 2 | **`ceiling_spread_m`** | §5.1, §5.3, §6.7, §7.4 | 7th derived metric, `lcl_max - lcl_min`. Without it the five stored ensemble fields feed nothing, and a ±150 m cloud base is indistinguishable from a ±900 m one |
| 3 | **`sunshine` and `cloud_mid` wired in** | §6.3, §6.4 | §2.3 documented both as meaningful signals and §6 then ignored both. Resolved by adding a guarded `sunshine` penalty to `thermal_strength` and a CAPE-gated `cloud_mid` term to `overdevelopment_risk` |
| 4 | **Richer `day_windows`** | §6.8, §7.5 | Start/end alone does not answer "is Saturday worth the drive?". Eight keys now, all router-computed from stored hourly data at zero storage cost |

### Follow-up recorded, not scheduled

**Thermal forecast verification via the JFB elevation ladder.** Thermal forecasts look unverifiable
(nothing observes CAPE or LCL), but `.ai/context/features.md` already notes the JFB collector gives
an 799 m → 3955 m temperature+humidity ladder within ~10 km — a measured lapse rate, and a
temperature−dewpoint spread that converts directly to observed cloud base (~125 m per K). That is
the observed counterpart to forecast `lcl`, and would let the existing `/forecast-analysis`
machinery score thermal accuracy. Out of scope here; worth a spec of its own once Phase 1 has
accumulated data. Exclude `jfb-hollandiahutte-sac` (upstream metadata bug).
