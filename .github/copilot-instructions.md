# Lenticularis - AI Coding Agent Instructions

## Project Overview
Lenticularis is a weather decision support system for paragliding launches. It provides a traffic light system (green/orange/red) based on customizable rules for each launch site by continuously monitoring weather stations and logging decisions in time-series databases.

**Stack**: FastAPI (Python 3.x), SQLite3 (metadata), InfluxDB (time-series), Docker

## Architecture Essentials

### Dual Database Pattern
- **SQLite3** (`app/db/sqlite/`): Static data (launches, stations, rules, associations)
- **InfluxDB** (`app/db/influx/`): Time-series data (weather measurements, decisions)

Critical: Weather data flows through collectors → InfluxDB. Launch/rule metadata stays in SQLite. Always use the appropriate database for the data type.

### Collector System
All collectors inherit from `collectors/base.py:BaseCollector` and implement:
- `fetch_data()`: Get raw data from source API
- `normalize_data()`: Convert to standard format (see base.py docstring for schema)
- Always return normalized data with: `station_id`, `source`, `timestamp`, `wind_speed` (m/s), `wind_direction` (degrees), etc.

Example: [collectors/sources/meteoswiss.py](collectors/sources/meteoswiss.py) shows complete implementation pattern.

### Configuration Pattern
Settings use `pydantic-settings` with environment variables. See [app/core/config.py](app/core/config.py):
- All settings defined in `Settings` class with defaults
- Load from `.env` file (see `.env.example` for template)
- Access via `from app.core.config import settings`
- Never hardcode URLs, credentials, or intervals

### API Structure
FastAPI endpoints in `app/api/v1/` follow this pattern:
```python
# Use direct SQLite access for CRUD (no ORM)
with db.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM launches WHERE id = ?", (launch_id,))
    row = cursor.fetchone()
```
See [app/api/v1/launches.py](app/api/v1/launches.py) for reference. No SQLAlchemy - keep it lightweight.

## Development Workflows

### Initial Setup
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env  # Edit with your InfluxDB details
python init_db.py       # Initialize SQLite schema
python seed_stations.py # Populate Swiss stations
python main.py          # Run app on http://localhost:8000
```

### Testing Collectors
```powershell
python test_collectors.py  # Test all data collectors
```
Check output for normalized data structure compliance.

### Docker Deployment
```powershell
docker-compose up -d
docker-compose logs -f lenticularis
```
The docker-compose.yml connects to an **existing InfluxDB instance** (homelab setup). Not included in compose file.

## Project-Specific Conventions

### Data Normalization
All weather data MUST be normalized to:
- Wind speeds: **m/s** (not km/h). Use `BaseCollector.kmh_to_ms()` helper
- Wind direction: **degrees 0-360** (0 = North, 90 = East)
- Temperature: **Celsius**
- Pressure: **hPa**
- Timestamp: **datetime object** (UTC preferred)

### Database Connections
- SQLite: Use context manager `with db.get_connection() as conn:`
- InfluxDB: Use lazy-initialized properties `influx.write_api` or `influx.query_api`
- Both connections auto-commit on context exit or handle rollbacks

### Error Handling
- Collectors raise `CollectorError` (custom exception in base.py)
- Log errors with module-specific loggers: `logger = logging.getLogger(__name__)`
- Use log levels: INFO (collection success), ERROR (failures), DEBUG (API calls)

### Code Style
- **Type hints**: Required on all function signatures
- **Docstrings**: Required on classes and public methods (see base.py for style)
- **Async vs sync**: API endpoints are `async def`, collectors are sync (blocking I/O acceptable)

## Key Integration Points

### Adding New Weather Sources
1. Create file in `collectors/sources/` (see [meteoswiss.py](collectors/sources/meteoswiss.py))
2. Inherit from `BaseCollector`
3. Implement `fetch_data()` and `normalize_data()`
4. Add API key to Settings if needed
5. Register in future scheduler

### APScheduler Integration (For Background Collection)
Implement background data collection using APScheduler (already in requirements.txt):

```python
# collectors/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from app.db.influx.connection import influx
from collectors.sources.meteoswiss import MeteoSwissCollector

scheduler = BackgroundScheduler()

def collect_all_weather_data():
    """Run all collectors and store in InfluxDB"""
    collectors = [MeteoSwissCollector(), ...]  # Add all collectors
    for collector in collectors:
        try:
            normalized_data = collector.collect()
            influx.write_weather_data(normalized_data)
        except CollectorError as e:
            logger.error(f"Collection failed: {e}")

# Start in main.py or app startup
@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(
        collect_all_weather_data,
        'interval',
        seconds=settings.COLLECTOR_INTERVAL_SECONDS,
        id='weather_collection',
        replace_existing=True
    )
    scheduler.start()

@app.on_event("shutdown")
async def shutdown_scheduler():
    scheduler.shutdown()
```

Critical: Use BackgroundScheduler (not AsyncIOScheduler) since collectors are sync. Run collection in intervals, not cron, for consistent monitoring.

### Rule Engine (Phase 2 - Not Yet Implemented)
Future location: `rules/engine.py` and `rules/evaluators/`
- Rules stored in SQLite `rules` table
- Evaluated against latest InfluxDB weather data
- Return traffic light decision (green/orange/red)
- Decision logic: ANY red rule → red; ANY orange → orange; else green

See [AI_INSTRUCTIONS.md](AI_INSTRUCTIONS.md#phase-2-rule-engine) for detailed architecture plan.

## Critical Files

- [README.md](README.md): Project vision, roadmap, feature overview
- [ARCHITECTURE.md](ARCHITECTURE.md): Detailed system design, proposed structure
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): What's built vs pending
- [AI_INSTRUCTIONS.md](AI_INSTRUCTIONS.md): Comprehensive AI context (566 lines of detailed specifications)
- [app/core/config.py](app/core/config.py): All configurable settings
- [collectors/base.py](collectors/base.py): Collector interface and data format
- [init_db.py](init_db.py): SQLite schema initialization

## Current Status (Phase 1: ~90% Complete)

✅ **Working**: FastAPI app, SQLite/InfluxDB integration, MeteoSwiss collector, Launch CRUD API, Docker deployment
⏳ **Pending**: Rule engine, collector scheduler (APScheduler), web UI, alerts

When implementing new features, always check [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) first to understand what exists.
