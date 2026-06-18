# LSMFAPI Thermal Grid — Integration Context

## Endpoint

```
GET https://lsmfapi.lg4.ch/api/forecast/thermal-grid
```

## Query parameters

| param | type | default | constraint |
|---|---|---|---|
| `bbox` | string | `"45.8,47.9,5.9,10.6"` | `"lat_min,lat_max,lon_min,lon_max"` — all floats, lat_min < lat_max, lon_min < lon_max, within lat 43–50 / lon 3–17 |
| `stride_km` | int | `10` | one of: `1`, `2`, `5`, `10` |

## Error responses

```ts
{ error: { code: string, message: string } }
// code: "invalid_stride" → 400
// code: "invalid_bbox"   → 400
// code: "cache_warming"  → 503 — retry after 5 min; data not yet ready after server cold start
```

## TypeScript types

```ts
interface GridPoint {
  lat: number;  // degrees, 5 decimal places
  lon: number;  // degrees, 5 decimal places
}

interface ThermalGridFrame {
  valid_time: string;             // ISO 8601 UTC datetime

  // All value arrays are parallel to ThermalGridResponse.grid (length = grid.length)
  // null = ICON fill value or missing data — always null-guard before use

  solar:         (number | null)[];  // W/m² — direct + diffuse SW radiation, ensemble median
  solar_min:     (number | null)[];  // W/m² — ensemble min
  solar_max:     (number | null)[];  // W/m² — ensemble max

  sunshine:      (number | null)[];  // min/h — sunshine duration per hour (0–60), ensemble median
  sunshine_min:  (number | null)[];
  sunshine_max:  (number | null)[];

  cloud_cover:   (number | null)[];  // % total cloud cover (0–100), ensemble median
  cloud_cover_min: (number | null)[];
  cloud_cover_max: (number | null)[];
  cloud_low:     (number | null)[];  // % low cloud, ensemble median
  cloud_low_min: (number | null)[];
  cloud_low_max: (number | null)[];
  cloud_mid:     (number | null)[];  // % mid cloud, ensemble median
  cloud_mid_min: (number | null)[];
  cloud_mid_max: (number | null)[];
  cloud_high:    (number | null)[];  // % high cloud, ensemble median
  cloud_high_min:(number | null)[];
  cloud_high_max:(number | null)[];

  freezing_level:     (number | null)[];  // m ASL — 0 °C isotherm height, ensemble median
  freezing_level_min: (number | null)[];
  freezing_level_max: (number | null)[];

  cape:     (number | null)[];  // J/kg — mixed-layer CAPE (0 = stable), ensemble median
  cape_min: (number | null)[];
  cape_max: (number | null)[];

  // cin: negative J/kg; null means ICON fill value = no convective inhibition layer (treat as 0)
  cin:     (number | null)[];
  cin_min: (number | null)[];
  cin_max: (number | null)[];

  lcl:     (number | null)[];  // m ASL — Lifted Condensation Level (cloud base), ensemble median
  lcl_min: (number | null)[];
  lcl_max: (number | null)[];

  lfc:     (number | null)[];  // m ASL — Level of Free Convection, ensemble median
  lfc_min: (number | null)[];
  lfc_max: (number | null)[];

  tke:     (number | null)[];  // J/kg — Turbulent Kinetic Energy, ensemble median
  tke_min: (number | null)[];
  tke_max: (number | null)[];
}

interface ThermalGridResponse {
  init_time:  string;           // ISO 8601 UTC — model run start time
  model:      string;           // "icon-ch1", "icon-ch2", or "icon-ch1+ch2"
  stride_km:  number;           // echoes request param
  grid:       GridPoint[];      // N points, row-major: lat descending, lon ascending
  frames:     ThermalGridFrame[]; // one per forecast hour, h+0 → h+120 (up to 120 entries)
}
```

## Data contracts

- `frames[i].{field}[j]` is the value for `grid[j]` at `frames[i].valid_time`. The index `j` into all field arrays maps 1-to-1 to `grid[j]`.
- Grid is row-major: iterate lat descending (north→south), lon ascending (west→east). Suitable for direct 2D array / image indexing.
- All numeric values rounded to 1 decimal place.
- `_min` / `_max` are ensemble extremes across all members (~10 for CH1, ~21 for CH2). Use for uncertainty bands.
- `model` is `"icon-ch1+ch2"` when both model slices are cached and merged (h0–h33 from CH1, h34–h120 from CH2). If only one slice is available (e.g. after first cold-start), `model` will be `"icon-ch1"` or `"icon-ch2"` and `frames` covers only that slice.
- Data refreshes ~4×/day. Client should re-fetch when `init_time` changes, not on a fixed timer.

## Field semantics for thermal/gliding use

```
solar          W/m²    Primary thermal trigger. Active thermals typically start >300 W/m².
sunshine       min/h   Sun-on-ground. High variance = patchy cloud = uneven thermals.
cloud_cover    %       Overall cover. Sustained >80% suppresses thermals.
cloud_low      %       Low cloud (fog/stratus). High = ceiling below usable altitude.
cloud_mid      %       Mid cloud. High = potential overdevelopment cap.
cloud_high     %       Cirrus. Indirect synoptic instability indicator.
freezing_level m ASL   Hard upper thermal ceiling (ice forms above). Max soarable altitude proxy.
cape           J/kg    0=stable, 0–300=weak, 300–1000=moderate thermals, >1000=storm risk.
cin            J/kg    Inhibition cap; null=no cap (free convection). < -100 = thermals unlikely.
lcl            m ASL   Cloud base = thermal ceiling height. KEY FIELD for pilots.
lfc            m ASL   Level of free convection. lfc≈lcl → strong cumulus. lfc>>lcl → blue thermals.
tke            J/kg    Boundary layer turbulence. >2 = rough air.
```

## Null semantics

| Field | null means |
|---|---|
| `cin` | No convective inhibition layer present (ICON fill −999.9 clipped). Render as "no cap" / 0. |
| All others | Data unavailable for this point/hour. Do not render; skip or show as "—". |

## Payload size guide

| stride_km | ~Points (Switzerland bbox) | ~Frames | ~Payload (JSON, uncompressed) |
|---|---|---|---|
| 10 | ~200 | 120 | ~0.5 MB |
| 5  | ~800 | 120 | ~2 MB   |
| 2  | ~5 000 | 120 | ~12 MB  |
| 1  | ~20 000 | 120 | ~48 MB  |

Use `stride_km=10` as default. Use `stride_km=5` for zoomed detail views. Enable gzip on the HTTP client (`Accept-Encoding: gzip`).

## Minimal fetch pattern (JavaScript — no-build-step, matches Lenticularis conventions)

```js
async function fetchThermalGrid(strideKm = 10, bbox = null) {
  const params = new URLSearchParams({ stride_km: String(strideKm) });
  if (bbox) params.set('bbox', bbox);
  const res = await fetch(`https://lsmfapi.lg4.ch/api/forecast/thermal-grid?${params}`);
  if (res.status === 503) throw new Error('cache_warming');
  if (!res.ok) throw new Error(`lsmfapi_error_${res.status}`);
  return res.json();
}

// Nearest grid point index for a given lat/lon
function nearestGridIndex(grid, lat, lon) {
  let best = 0, bestDist = Infinity;
  for (let j = 0; j < grid.length; j++) {
    const d = (grid[j].lat - lat) ** 2 + (grid[j].lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = j; }
  }
  return best;
}

// Extract single-point time series from a full grid response
function pointSeries(data, lat, lon) {
  const j = nearestGridIndex(data.grid, lat, lon);
  return data.frames.map(f => ({
    time:          f.valid_time,
    solar:         f.solar[j],
    lcl:           f.lcl[j],
    lcl_min:       f.lcl_min[j],
    lcl_max:       f.lcl_max[j],
    lfc:           f.lfc[j],
    freezing_level:f.freezing_level[j],
    cape:          f.cape[j],
    cin:           f.cin[j],    // null = no cap
    cloud_cover:   f.cloud_cover[j],
    tke:           f.tke[j],
    sunshine:      f.sunshine[j],
  }));
}
```

## Integration ideas for Lenticularis

- **Thermal forecast map layer** — overlay on `/wind-forecast` or new `/thermal` page. Colour grid by `solar` or `lcl` at selected hour. Toggle with existing time-nav controls.
- **Station-detail thermal panel** — below wind chart: cloud base (lcl), freezing level, CAPE, sunshine bar. Uses `nearestGridIndex` to extract the station's point series.
- **Ruleset conditions** — new condition types: `lcl > X m`, `cape < Y J/kg`, `cloud_cover < Z%`. Requires storing thermal fields in InfluxDB (`weather_forecast_thermal` measurement or extend `weather_forecast`).
- **Thermal suitability badge** — traffic-light badge per station: green (solar >300, cape <1000, cin null/small, lcl high), orange, red. Could surface on map popup.
- **Wind-forecast grid extension** — add thermal fields to existing `wind_forecast_grid` InfluxDB measurement so the collector writes them alongside wind. Avoids a second measurement.

## Notes on lsmfapi

- User-owned service, same Docker network as Lenticularis dev/prod, no rate limits.
- Currently serves FGA stations (station forecast) and ICON-CH1/CH2 wind grid.
- Thermal grid is a new endpoint — may still be in active development; verify schema stability before storing in InfluxDB.
- No authentication required (internal network).
