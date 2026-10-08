# Prompt: Add a New Weather Collector

Use this prompt when adding a new weather data network (a new source of live station
observations). Work through **every** step — the frontend and docs steps are the ones that
get forgotten, and a collector that writes data but never appears in the UI looks broken.

> Placeholders: `<net>` = the short network id (lowercase, e.g. `jfb`), used as the
> `NETWORK` constant, the `station_id` prefix (`<net>-<slug>`), the config `name`, and the
> `.network-<net>` CSS class. `<Label>` = the human-readable name (e.g. `Jungfraubahn`).

---

## 1. Collector class — `src/lenticularis/collectors/<net>.py`

Subclass `BaseCollector` (`collectors/base.py`). Model it on the closest existing collector
(`fga.py` for a fixed station set + no auth; `holfuy.py` for API-key + per-station fetch).

- `NETWORK = "<net>"`.
- Implement `get_stations()` and `collect() -> list[WeatherMeasurement]`.
- Namespace ids with `self.station_id(self.NETWORK, slug)` → `<net>-<slug>`.
- Import `to_float` and `normalize_wind_dir` from `collectors/utils.py` — **never** redefine
  local copies.
- Read the endpoint URL (and any key) from `self.config`, never hardcode secrets, never read
  `os.environ`.
- **Normalise units at the boundary.** `WeatherMeasurement` is km/h, °C, %, hPa. Convert
  foreign units in the collector (e.g. knots × 1.852) — never store a foreign unit.
- **Map only fields the schema already has.** If a source field has no `WeatherMeasurement`
  field, drop it — do not widen the model for one source. (See how `jfb.py` drops `TD`/
  `DIFFTD`/`G1h`.)
- **Never synthesise an unmeasured field** (e.g. do not compute QFF from QFE + elevation —
  that is QNH; leave `pressure_qff=None`, as `fga.py` does).
- **Staleness guard:** skip and `WARNING` any reading older than ~2 h. Some APIs return
  hours-old data with `200 OK` and no error (see the `currentDateTime` note in `jfb.py`).
- Log every fetch: URL, elapsed ms, station count in/emitted/skipped. Never swallow
  exceptions — `logger.exception(...)` and re-raise.
- Use `asyncio.to_thread()` for any synchronous InfluxDB write.

See `.ai/instructions/02-backend-conventions.md` → "Collector Conventions".

## 2. Register in the scheduler — `src/lenticularis/scheduler.py`

- Add the import next to the other collectors.
- Add one entry to `_COLLECTOR_REGISTRY`: `"<net>": <Net>Collector,`.

## 3. Dedup priority — `src/lenticularis/services/dedup.py`

Add `"<net>"` to `NETWORK_PRIORITY`. Position it by trust: higher = wins when two networks'
stations are co-located within 50 m. Unknown networks already sort last, so appending is the
safe default.

## 3b. Public MCP allowlist — `config.py` `McpConfig.verified_networks` + `config.yml.example`

The public MCP server (`/mcp`) exposes only networks listed in `mcp.verified_networks` — an allowlist,
so a new network is **hidden by default**. Add `"<net>"` there only if it is institutional or
professional/semi-professional. **Never** add privately operated personal-station networks (like
`wunderground` / `ecowitt`). Mirror the change in the server's `config.yml` if it overrides `mcp:`.

## 4. Config — `config.yml.example` (committed) **and** the local `config.yml` (gitignored)

Mirror an existing block:
```yaml
  - name: "<net>"
    enabled: true
    interval_minutes: 10
    config:
      url: "https://…"        # omit `config:` entirely if the collector has a default
```
Comment which stations it serves and any known quirks.

## 5. Frontend — THE STEP THAT GETS FORGOTTEN

A new network must be added to the Stations overview **and** styled on every page that
renders a network badge. Badges are built dynamically as `network-${s.network}`, so a
missing `.network-<net>` rule silently falls through to the grey `unknown` style.

- **`static/stations.html`** — add the filter option to `#networkFilter`:
  ```html
  <option value="<net>"><Label></option>
  ```
  and add the colour rule to the `<style>` block:
  ```css
  .network-<net>  { background: #742a2a; color: #feb2b2; }   /* pick an unused colour */
  ```
- **Add the same `.network-<net>` rule** to the other pages that define network-badge
  colours (each has its own inline `<style>` block — grep `\.network-meteoswiss` to find
  them): `static/index.html`, `static/station-detail.html`, `static/forecast-accuracy.html`,
  `static/forecast-analysis.html`.
- **Only if the new network is a *personal* weather station** (like Wunderground/Ecowitt),
  add `<net>` to `PERSONAL_NETWORKS` in `static/map.js` so it respects the personal-station
  toggle. Public networks: leave alone.
- No i18n key is needed for the option label (the existing options are hardcoded), but do
  add any *other* new user-visible strings to all four locale files.

**Verify the whole set:** every page listed under "badge colours" must contain `.network-<net>`.
```
grep -l "network-<net>" static/stations.html static/index.html static/station-detail.html \
  static/forecast-accuracy.html static/forecast-analysis.html
```

## 6. Tests — `tests/backend/test_<net>_collector.py`

Pure-logic tests against a captured JSON/XML fixture — no network, no InfluxDB (see
`test_jfb_collector.py`). Cover: unit conversion, direction normalisation, dropped fields,
any excluded stations, timestamp handling, and the staleness skip. **Build fixture
timestamps relative to `datetime.now(timezone.utc)`** — never hardcode a clock time, or the
test passes/fails depending on the hour it runs.

Run: `poetry run pytest tests/backend/test_<net>_collector.py -q` then the full suite.

## 7. Live smoke test

Drive the real endpoint once and eyeball the output before shipping:
```
poetry run python -c "
import asyncio, logging; logging.basicConfig(level=logging.INFO)
from lenticularis.collectors.<net> import <Net>Collector
for m in asyncio.run(<Net>Collector().collect()):
    print(m.station_id, m.timestamp, m.wind_speed, m.wind_direction, m.temperature)
"
```
Confirm the expected station count and **fresh timestamps** (not hours stale).

## 8. Docs

- `.ai/instructions/01-project-overview.md` — add a row to the **Data Sources** table.
- `.ai/context/architecture.md` — append `<net>` to the dedup priority chain under
  "Virtual Station Deduplication".
- `.ai/context/features.md` — add a shipped entry; record any upstream data quirks.
- `README.md` — add the network to the collectors bullet under **Features**.

## 9. Release & deploy

- **Bump `version` in `pyproject.toml`.** Step 5 changed static assets, and the version is
  the `?v=` cache key (see `03-frontend-conventions.md` → Static Asset Caching). HTML itself
  revalidates by ETag, but bump anyway for image-tag hygiene.
- Commit, then tag `v<x.y.z>` and push the tag — that triggers the `ghcr.io` image build
  (`.github/workflows/docker-publish.yml`); one tag produces `:latest` automatically.
- **Manual, not in the repo:** the collector does nothing until its config block (step 4) is
  added to the **server's** `config.yml` (gitignored) and the container restarted. Per the
  deployment rules, the user does this — do not push to prod.

---

## Checklist

- [ ] `collectors/<net>.py` — BaseCollector subclass, units normalised, staleness guard
- [ ] `scheduler.py` — import + `_COLLECTOR_REGISTRY` entry
- [ ] `services/dedup.py` — `NETWORK_PRIORITY`
- [ ] MCP: decide whether `<net>` goes in `mcp.verified_networks` (default: no; never for personal stations)
- [ ] `config.yml.example` + local `config.yml`
- [ ] `stations.html` — filter `<option>` **and** `.network-<net>` colour
- [ ] `.network-<net>` colour on index / station-detail / forecast-accuracy / forecast-analysis
- [ ] `map.js PERSONAL_NETWORKS` — only if a personal-station network
- [ ] `tests/backend/test_<net>_collector.py` — passing, time-relative fixtures
- [ ] live smoke test — expected count, fresh timestamps
- [ ] docs: 01-overview, architecture, features, README
- [ ] version bump + tag + push
