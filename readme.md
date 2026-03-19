```markdown
# Lenticularis

Weather data aggregation and rule-based decision support for outdoor activities in Switzerland.

## Overview

Lenticularis collects weather data from multiple Swiss weather networks (MeteoSwiss, SLF, METAR, Holfuy, Windline, Ecovitt), normalizes the data, stores it in InfluxDB, and applies customizable rule-based analysis to produce traffic light decisions (Status Ok / Warning / Stop) for user-defined sites. The system supports multiple site types — starting with paragliding launches and landing zones, with more types to follow.

## Features

- **Multi-source data collection**: Fetch weather data from multiple Swiss weather networks
- **Automated scheduling**: Periodic data collection (currently MeteoSwiss 10 min, SLF 30 min, METAR 15 min in dev)
- **Time-series storage**: InfluxDB integration for historical weather data
- **Rule-based analysis**: Customizable rules for wind speed, direction, pressure deltas, etc.
- **Traffic light decisions**: Status Ok (green), Warning (orange), Stop (red) per site
- **Web interface**: Interactive Switzerland map with station visualization
- **REST API**: Full API with automatic documentation (FastAPI)
- **Docker deployment**: Easy deployment with docker-compose

## Tech Stack

- **Python 3.11+**
- **FastAPI** - Web framework with automatic OpenAPI docs
- **Poetry** - Modern dependency management
- **Pydantic** - Data validation with type hints
- **InfluxDB** - Time-series database
- **SQLite** - Relational database for metadata
- **APScheduler** - Job scheduling
- **Leaflet.js** - Interactive maps

## Project Structure

```
lenticularis/
├── src/lenticularis/
│   ├── collectors/      # Weather data collectors
│   ├── database/        # InfluxDB and SQLite clients
│   ├── models/          # Pydantic data models
│   ├── rules/           # Rule engine
│   └── api/             # FastAPI routes
├── static/              # Web frontend
├── config.yml.example   # Configuration template
└── pyproject.toml       # Dependencies
```

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Poetry (install from https://python-poetry.org/)
- InfluxDB 2.x (optional - can use existing instance)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/lenticularis.git
   cd lenticularis
   ```

2. **Install dependencies**
   ```bash
   poetry install
   ```

3. **Configure the application**
   ```bash
   cp config.yml.example config.yml
   # Edit config.yml with your settings
   ```

4. **Run with Docker (recommended)**
   ```bash
   docker-compose up -d
   ```

   Or run directly:
   ```bash
   poetry run uvicorn lenticularis.api.main:app --reload
   ```

5. **Access the application**
   - Web interface: http://localhost:8000
   - API documentation: http://localhost:8000/docs

## Configuration

See [config.yml.example](config.yml.example) for all configuration options.

### Bring Your Own Database

To use an existing InfluxDB instance, update `config.yml`:

```yaml
influxdb:
  enabled: true
  url: "https://your-influxdb-instance.com"
  token: "your-token"
  org: "your-org"
  bucket: "weather_data"
```

And comment out the InfluxDB service in `docker-compose.yml`.

## Development Status

### v0.1 — ✅ Deployed
- Project structure, config, InfluxDB client, MeteoSwiss collector, APScheduler, FastAPI REST API, live station table

### v0.2 — ✅ Deployed
- Leaflet.js map (`index.html`) with station markers, click-to-popup with latest measurement

### v0.3 — ✅ Deployed
- JWT auth (register/login/refresh), SQLite via SQLAlchemy, `get_current_user` / `require_admin` FastAPI dependencies

### v0.4 — ✅ Deployed (folded into rulesets — no separate launch_sites table)
- Site identity embedded in rulesets (`site_type`, `lat`, `lon`, `altitude_m`); launch site markers on map

### v0.5 — ✅ Deployed
- SLF collector (30 min), METAR collector (15 min, AviationWeather), Ecovitt collector (personal weather stations), full APScheduler wiring

### v0.6 — ✅ Deployed
- Graphical rule set editor (`/ruleset-editor`): per-row station picker, field/operator/value inputs, AND/OR (Condition Group) nesting, direction-range compass, pressure-delta two-station mode, combination logic selector

### v0.7 — ✅ Deployed
- Rules evaluator (`rules/evaluator.py`): evaluates live InfluxDB data per condition, writes `rule_decisions` to InfluxDB
- `GET /api/rulesets/{id}/evaluate` — launch + linked landing evaluation; returns `landing_decisions` + `best_landing_decision`
- Map: vivid gust-scaled wind arrow markers, launch ▲ / landing ⛑ markers with coloured halo (best landing decision)
- Rulesets list (`/rulesets`): live GREEN/ORANGE/RED decision badges + landing decision badges per card
- Map auto-refresh; Ecovitt collector

### v0.8 — ✅ Deployed
- **Weather data replay**: time-range selector (6h / 24h / 7d / custom), speed multipliers (10× 50× 100× 200× 500×), play/pause/scrub controls on both map and station table; `GET /api/stations/data-bounds` + `GET /api/stations/replay` endpoints; `replay.js` `ReplayEngine` class
- **Ruleset analysis page** (`/ruleset-analysis`): current evaluation table (Station / Field / Condition / Actual / Status), decision history with timeline strip, Chart.js scatter chart, grouped state-change table (click to expand condition detail at transition point)
- **Map popup condition breakdown**: clicking a launch/landing marker shows per-condition evaluation with thresholds, actual values, and coloured status
- **Decision history API**: `GET /api/rulesets/{id}/history?hours=N` backed by `rule_decisions` InfluxDB measurement; `ConditionResult` extended with `operator`, `value_a`, `value_b`
- Rulesets list cards navigate to analysis page on click

### v0.9 — ✅ Deployed (current)
- **Statistics Dashboard** (`/stats`, `static/stats.html`): three-tab page — Ruleset Stats, Weather Stats, Service Stats
  - *Ruleset Stats*: aggregate green/orange/red % overview, site comparison bars, hourly pattern chart, flyable days; period selector 7d / 30d / 90d / 1y
  - *Weather Stats*: extremes leaderboard (highest wind/gust, hottest/coldest, highest/lowest pressure, most precipitation, deepest snow, humidity) with period selector (Now / Today / Yesterday / Last 7 Days / Tomorrow / Choose Date); network coverage table; station freshness table
  - *Service Stats*: user/ruleset/collector summary cards, collector health table, InfluxDB storage section (weather record count 365d, forecast record count 30d, total InfluxDB disk size via `/metrics`, daily ingestion line chart 30d)
- **New backend**: `services/stats.py`, `services/weather_stats.py`, `api/routers/stats.py`
- **New InfluxDB methods**: `query_decision_history_multi`, `query_extremes_for_period`, `query_measurement_count`, `query_daily_ingestion`, `query_storage_bytes`
- **Map time navigation bar** (replaces old hidden replay bar): always-visible two-row bar at bottom of map
  - *Day row*: −3 Days through +5 Days + custom date picker; past days neutral, future days amber-tinted, Now green (live auto-refresh)
  - *Hour row*: appears for any day except Now; 07:00–19:00 buttons + "Play day" button animating through hours at 600 ms each
  - Future days request appropriate `forecast_hours` (up to 120 h); station popups show "📡 Forecast" (amber) for future-timestamped data
  - `replay.js`: added `forecast_hours` and `include_forecast` params to `load()`
- **Station detail forecast overlay** (`station-detail.html`): "📡 + Forecast" toggle shows last 48 h observations as solid line + 120 h forecast as dashed amber line; amber background zone + "Now" boundary line; `GET /api/stations/{id}/forecast` endpoint
- **Forecast pressure fix**: Open-Meteo collector now requests `pressure_msl` (sea-level reduced QNH) instead of `surface_pressure` (terrain-level QFE) — values now match station QNH observations regardless of elevation
- **Cursor guide fix** (`station-detail.js`): guide line now tracks raw mouse position via `afterEvent` hook instead of snapping to nearest data point — moves freely across observations and forecast zone

### Planned — v0.10 — Launchsite Registry + Clubs + Ruleset Types
- Separate `launch_sites` table; clubs with GeoJSON area polygons; map overlays for both
- `ruleset_type` field: `'risk'` (default) vs `'opportunity'` (green = good conditions)

### Planned — v0.11 — AI-Assisted Ruleset Creation
- Natural language → condition JSON via Claude API; preview in editor before saving

### Planned — v0.12 — Station Deduplication
- Admin-defined station groups; API surfaces only the freshest data source per group

### Planned — v1.0 — Organisations + Admin Backend
- Commercial customer organisations (VKPI, Jungfraubahn); customer role + subscription tiers; full admin UI

### Planned — v1.2 / v1.3 — XContest + OGN Integration
- Flight statistics per launchsite from XContest API; live OGN glider overlay on map

## Contributing

This is a learning project - contributions and suggestions welcome!

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Weather data provided by MeteoSwiss, Holfuy, SLF, Windline, and Ecovitt
- Built as a learning project to understand Python through practical application
```