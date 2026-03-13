```markdown
# Lenticularis

Weather data tracking and analysis system for paragliding decision support.

## Overview

Lenticularis collects weather data from multiple weather networks (MeteoSwiss, SLF, METAR, Holfuy, Windline, Ecovitt), normalizes the data, stores it in InfluxDB, and applies customizable rule-based analysis to create traffic light decisions (GREEN/ORANGE/RED) for paragliding launch sites.

## Features

- **Multi-source data collection**: Fetch weather data from multiple Swiss weather networks
- **Automated scheduling**: Periodic data collection (currently MeteoSwiss 10 min, SLF 30 min, METAR 15 min in dev)
- **Time-series storage**: InfluxDB integration for historical weather data
- **Rule-based analysis**: Customizable rules for wind speed, direction, pressure deltas, etc.
- **Traffic light decisions**: GREEN (go), ORANGE (caution), RED (no-go) for launch sites
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

### MVP 0.1 — ✅ Deployed

- [x] Project structure and configuration (`config.py`, Pydantic models)
- [x] `WeatherStation` + `WeatherMeasurement` models (3 pressure variants, snow depth)
- [x] `BaseCollector` abstract class with HTTP helpers and station-ID namespacing
- [x] InfluxDB 2.x client (`write_measurements`, `query_latest`, `query_history`, `has_measure`)
- [x] MeteoSwiss collector — 8 GeoJSON endpoints, LV95→WGS84 coordinate transform, 154 stations, 299 measurements/cycle
- [x] APScheduler wiring with per-job jitter
- [x] FastAPI app with lifespan, station registry, static file serving
- [x] `/api/stations` REST endpoints (list, single, latest, history)
- [x] Live data table dashboard (sortable, filterable, auto-refreshing)
- [x] `Dockerfile` + `docker-compose.yml` + `docker-compose.dev.yml` (dev overlay with Traefik labels)
- [x] Remote deployment to homelab Fedora host via `scripts/remote.ps1`
- [x] Live at `https://lenti-dev.lg4.ch` behind Traefik v3 + pocket-id OIDC auth

### 0.2 — In Progress
- [ ] Holfuy collector (free REST API — wind + temperature + pressure)
- [x] SLF collector (free JSON API — alpine stations, 30 min)
- [x] METAR collector (AviationWeather no-auth API, Swiss ICAOs, 15 min)
- [ ] Ecovitt collector (personal weather stations)
- [ ] Leaflet.js map view with station pins (color = data freshness)
- [ ] Click-to-popup with sparkline (Chart.js last 24 h)

### 1.0 — Planned
- [ ] SQLAlchemy ORM — launch sites, rule sets, condition tree
- [ ] Rule engine evaluator (AND/OR tree → GREEN/ORANGE/RED)
- [ ] Traffic light layer on the map
- [ ] Rule set editor UI
- [ ] Scheduler triggers rule evaluation after each collection run

## Contributing

This is a learning project - contributions and suggestions welcome!

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Weather data provided by MeteoSwiss, Holfuy, SLF, Windline, and Ecovitt
- Built as a learning project to understand Python through practical application
```