# Plan: Structure Lenticularis with FastAPI, Poetry, YAML Config, and Scheduler

Create a modern Python project using Poetry, `src/` layout, FastAPI with automatic OpenAPI docs, Pydantic models for type safety, YAML-based configuration, and APScheduler for periodic weather data collection. Build incrementally: project documentation → project skeleton → config system → first weather collector → scheduled data fetching → normalization → InfluxDB integration → rules engine → web GUI with map → Docker deployment.

## Steps

### 0. Create project context documentation
**Goal**: Document tech stack decisions and architecture for future AI sessions and contributors.

**Files to create**:
- `.cursorrules` or `.github/copilot-instructions.md`

**Content**:
- Tech stack decisions (FastAPI, Poetry, APScheduler, InfluxDB, YAML config)
- Module architecture (collectors, database, models, rules, api)
- Coding conventions (async/await, type hints, Pydantic)
- Learning approach (guided with PowerShell parallels)

**PowerShell parallel**: Like creating a README with team conventions, but specifically for AI assistants.

---

### 1. Bootstrap project foundation
**Goal**: Set up Poetry project and create directory structure.

**Commands**:
```bash
poetry init
poetry add fastapi uvicorn pydantic influxdb-client pyyaml httpx apscheduler
poetry add --group dev black ruff
```

**Directory structure to create**:
```
src/lenticularis/
├── __init__.py
├── collectors/
│   └── __init__.py
├── database/
│   └── __init__.py
├── models/
│   └── __init__.py
├── rules/
│   └── __init__.py
└── api/
    └── __init__.py
```

**Files to create**:
- `.gitignore` (Python-specific: `*.pyc`, `__pycache__/`, `.env`, `venv/`, `.venv/`, `poetry.lock`)
- `README.md` (project description, setup instructions)
- `config.yaml.example` (template configuration file)
- `pyproject.toml` (managed by Poetry)

**PowerShell parallel**: Like creating a PowerShell module structure with folders for Public/Private functions, but Python uses packages (folders with `__init__.py`).

---

### 2. Implement configuration system
**Goal**: Load and validate YAML configuration with proper defaults.

**File**: `src/lenticularis/config.py`

**What to implement**:
- Function to load `config.yaml` using PyYAML
- Pydantic model `Config` with nested models for:
  - `InfluxDBConfig` (url, token, org, bucket, optional=True for BYO-DB)
  - `CollectorConfig` (name, enabled, interval_minutes)
  - `LoggingConfig` (level, format)
- Validation logic (required fields, sensible defaults)
- Singleton pattern to load config once

**PowerShell parallel**: 
```powershell
# PowerShell would do:
$config = Get-Content config.yaml | ConvertFrom-Yaml
# Python with Pydantic validates types automatically and raises errors
```

**Example config.yaml.example structure**:
```yaml
influxdb:
  url: "http://localhost:8086"
  token: "your-token-here"
  org: "lenticularis"
  bucket: "weather_data"
  
collectors:
  - name: "meteoswiss"
    enabled: true
    interval_minutes: 10
    
logging:
  level: "INFO"
  format: "json"
```

---

### 3. Build collector framework
**Goal**: Create abstract base class and first concrete collector (MeteoSwiss).

**File**: `src/lenticularis/collectors/base.py`

**What to implement**:
- Abstract base class `BaseCollector` using `ABC` (Abstract Base Class)
- Abstract methods:
  - `async def fetch_data()` → returns raw API response
  - `async def get_stations()` → returns list of available stations
- Common functionality:
  - Error handling wrapper
  - Rate limiting logic
  - Logging

**PowerShell parallel**:
```powershell
# PowerShell abstract class:
class BaseCollector {
    [void] FetchData() { throw "Must override" }
}
# Python uses ABC module with @abstractmethod decorator
```

**File**: `src/lenticularis/collectors/meteoswiss.py`

**What to implement**:
- Class `MeteoSwissCollector` inheriting from `BaseCollector`
- Implement `fetch_data()`:
  - Use `httpx.AsyncClient` to call Meteoswiss API
  - Handle authentication if needed
  - Parse JSON response
  - Return raw data
- Implement `get_stations()`:
  - Query stations endpoint
  - Return list of station metadata

**Meteoswiss API hints**:
- Base URL: Research actual Meteoswiss API endpoints
- Look for open data portal or public APIs
- May need to handle authentication or rate limits

---

### 4. Create Pydantic models and normalization
**Goal**: Define unified data schema that all collectors transform into.

**File**: `src/lenticularis/models/weather.py`

**Models to create**:

**`WeatherStation`** (Pydantic model):
- `station_id: str` (unique identifier)
- `name: str` (human-readable name)
- `network: str` (e.g., "meteoswiss", "holfuy")
- `latitude: float`
- `longitude: float`
- `elevation: int` (meters above sea level)
- `canton: Optional[str]` (Swiss canton)

**`WeatherMeasurement`** (Pydantic model):
- `station_id: str`
- `timestamp: datetime` (UTC)
- `temperature: Optional[float]` (Celsius)
- `wind_speed: Optional[float]` (km/h)
- `wind_direction: Optional[int]` (degrees 0-360)
- `wind_gust: Optional[float]` (km/h)
- `barometric_pressure: Optional[float]` (hPa)
- `humidity: Optional[float]` (percentage)
- `precipitation: Optional[float]` (mm)

**PowerShell parallel**:
```powershell
# PowerShell class with typed properties:
class WeatherStation {
    [string]$StationId
    [double]$Latitude
    [ValidateRange(0,360)][int]$WindDirection
}
# Pydantic does this validation automatically
```

**File**: `src/lenticularis/collectors/meteoswiss.py` (add normalization method)

**What to add**:
- Method `normalize_data(raw_data)`:
  - Takes raw API response
  - Maps API fields to `WeatherMeasurement` model
  - Handles unit conversions if needed
  - Returns list of `WeatherMeasurement` objects

---

### 5. Build InfluxDB integration
**Goal**: Write normalized weather data to InfluxDB time series database.

**File**: `src/lenticularis/database/influx.py`

**What to implement**:

**Class `InfluxDBClient`**:
- `__init__()`: Initialize connection using config
- `async def connect()`: Establish connection, test with ping
- `async def write_measurement(measurement: WeatherMeasurement)`: Write single measurement
- `async def write_measurements(measurements: List[WeatherMeasurement])`: Batch write
- `async def close()`: Close connection properly

**Data structure**:
- **Measurement name**: `weather_data`
- **Tags** (indexed, for filtering):
  - `station_id`
  - `network`
  - `canton`
- **Fields** (values):
  - `temperature`
  - `wind_speed`
  - `wind_direction`
  - `wind_gust`
  - `barometric_pressure`
  - `humidity`
  - `precipitation`
- **Timestamp**: From `WeatherMeasurement.timestamp`

**PowerShell parallel**:
```powershell
# PowerShell might use REST API directly:
$body = @{ measurement = "weather"; tags = @{ station = "ABC" } }
Invoke-RestMethod -Uri $influxUrl -Method POST -Body $body
# Python client library handles protocol details
```

**Error handling**:
- Handle connection failures gracefully
- Retry logic for transient errors
- Log failed writes

**BYO-DB support**:
- Check if InfluxDB config exists
- If not configured, skip writes and log warning
- Allow app to run without InfluxDB for testing

---

### 6. Implement scheduler service
**Goal**: Automatically fetch weather data at configured intervals.

**File**: `src/lenticularis/scheduler.py`

**What to implement**:

**Class `WeatherScheduler`**:
- `__init__(config, collectors, influx_client)`: Initialize with dependencies
- `start()`: Start scheduler
- `stop()`: Gracefully stop scheduler
- `_schedule_collector(collector, interval_minutes)`: Add job for specific collector

**Using APScheduler**:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Create job that:
# 1. Calls collector.fetch_data()
# 2. Calls collector.normalize_data()
# 3. Calls influx_client.write_measurements()
# 4. Handles errors and logs results
```

**PowerShell parallel**:
```powershell
# PowerShell scheduled task:
$trigger = New-ScheduledTaskTrigger -Every (New-TimeSpan -Minutes 10)
Register-ScheduledTask -Action { Invoke-FetchWeather } -Trigger $trigger
# APScheduler is similar but embedded in your app
```

**Features**:
- Configurable intervals per collector (Meteoswiss every 10 min)
- Error handling per job (one collector failure doesn't stop others)
- Logging of collection stats (records fetched, errors)
- Graceful startup (stagger initial runs to avoid thundering herd)

---

### 7. Create rules engine architecture
**Goal**: Modular system for evaluating weather conditions and making decisions.

**File**: `src/lenticularis/models/common.py`

**What to create**:
```python
from enum import Enum

class TrafficLight(Enum):
    GREEN = "green"   # Go, flyable
    ORANGE = "orange" # Caution advised
    RED = "red"       # No-go, not flyable
```

**File**: `src/lenticularis/rules/base.py`

**What to implement**:

**Abstract class `BaseRule`**:
- `name: str` (rule identifier)
- `description: str` (human-readable explanation)
- `async def evaluate(context: dict) -> TrafficLight`: Abstract method
- `get_reasoning() -> str`: Explain why rule returned specific result

**File**: `src/lenticularis/rules/wind.py`

**Example rules to implement**:

**`WindSpeedRule`**:
- Parameters: `max_green: float`, `max_orange: float`
- Logic: If wind_speed < max_green → GREEN, elif < max_orange → ORANGE, else RED
- Example: max_green=20 km/h, max_orange=35 km/h

**`WindDirectionRule`**:
- Parameters: `green_range: tuple`, `orange_range: tuple`
- Logic: Check if direction falls in acceptable ranges
- Example: green_range=(180, 270) for south-west winds

**`WindGustRule`**:
- Parameters: `max_gust_ratio: float`
- Logic: Compare gust speed to average wind speed
- Example: If gust > wind_speed * 1.5 → RED (too gusty)

**File**: `src/lenticularis/rules/pressure.py`

**`BarometricPressureDeltaRule`**:
- Parameters: `station_1_id: str`, `station_2_id: str`, `max_delta_orange: float`, `max_delta_red: float`
- Logic: Query both stations, calculate pressure difference
- Example: Föhn detection - if delta > 4 hPa between valley and mountain station
- Must fetch current data from InfluxDB

**File**: `src/lenticularis/rules/ruleset.py`

**What to implement**:

**Class `RuleSet`**:
- `name: str` (e.g., "Interlaken Launch Site")
- `launch_site_id: str`
- `rules: List[BaseRule]`
- `combination_logic: str` ("all_must_be_green", "majority", "any_red_blocks")
- `async def evaluate() -> TrafficLight`: Run all rules and combine results
- `get_full_reasoning() -> dict`: Collect reasoning from all rules

**Combination logic examples**:
- **all_must_be_green**: All rules GREEN → overall GREEN; any ORANGE → overall ORANGE; any RED → overall RED
- **majority**: Most common result wins
- **any_red_blocks**: Any single RED → overall RED, otherwise use majority

**PowerShell parallel**:
```powershell
# PowerShell scriptblock evaluation:
$rules = @(
    { param($data) if($data.Wind -lt 20) { "Green" } else { "Red" } }
)
$results = $rules | ForEach-Object { & $_ $weatherData }
# Python uses class inheritance and polymorphism
```

**Logging to InfluxDB**:
- Create measurement `rule_decisions`
- Tags: `launch_site_id`, `ruleset_name`
- Fields: `decision` (green/orange/red), `reasoning` (JSON), `timestamp`

---

### 8. Develop FastAPI application
**Goal**: Web API and GUI for viewing stations, managing launch sites, and evaluating rules.

**File**: `src/lenticularis/api/main.py`

**What to implement**:

**FastAPI app initialization**:
```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Lenticularis",
    description="Weather tracking for paragliding decisions",
    version="0.1.0"
)

# Startup event: Initialize scheduler, database connections
# Shutdown event: Stop scheduler, close connections
```

**File**: `src/lenticularis/api/routers/stations.py`

**Endpoints**:
- `GET /api/stations`: List all weather stations
- `GET /api/stations/{station_id}`: Get specific station details
- `GET /api/stations/{station_id}/latest`: Get latest measurement from InfluxDB

**File**: `src/lenticularis/api/routers/launch_sites.py`

**Endpoints**:
- `GET /api/launch-sites`: List all launch sites
- `POST /api/launch-sites`: Create new launch site
  - Body: `{ name, latitude, longitude, default_landing_zones: [...] }`
- `GET /api/launch-sites/{id}`: Get launch site details
- `PUT /api/launch-sites/{id}`: Update launch site
- `DELETE /api/launch-sites/{id}`: Delete launch site

**File**: `src/lenticularis/api/routers/rules.py`

**Endpoints**:
- `GET /api/rulesets`: List all rulesets
- `POST /api/rulesets`: Create new ruleset
- `GET /api/rulesets/{id}`: Get ruleset details
- `PUT /api/rulesets/{id}`: Update ruleset
- `POST /api/rulesets/{id}/evaluate`: Evaluate ruleset NOW and return decision

**File**: `src/lenticularis/api/routers/evaluation.py`

**Endpoints**:
- `POST /api/evaluate`: Evaluate specific ruleset
- `GET /api/evaluations/history`: Get historical decisions from InfluxDB

**Static files for map GUI**:
- `static/index.html`: Main page with Leaflet.js map
- `static/app.js`: JavaScript for map interaction
- `static/style.css`: Styling

**Map features**:
- Display Switzerland basemap (OpenStreetMap)
- Show all weather stations as markers
- Click map to create launch site
- Click map to create landing zone
- Visual indicators for current traffic light status per launch site

**PowerShell parallel**:
```powershell
# PowerShell web server (Polaris module):
New-PolarisRoute -Path "/api/stations" -Method GET -ScriptBlock {
    Get-WeatherStations | ConvertTo-Json
}
# FastAPI handles routing, validation, docs automatically
```

**Automatic docs**:
- FastAPI generates interactive docs at `/docs` (Swagger UI)
- Alternative docs at `/redoc`

---

### 9. Package in Docker
**Goal**: Containerize application for easy deployment to homelab.

**File**: `Dockerfile`

**Multi-stage build**:

**Stage 1 - Builder**:
```dockerfile
FROM python:3.11-slim as builder
WORKDIR /app
RUN pip install poetry
COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt > requirements.txt
```

**Stage 2 - Runtime**:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /app/requirements.txt .
RUN pip install -r requirements.txt
COPY src/ ./src/
COPY config.yaml.example ./config.yaml
CMD ["uvicorn", "src.lenticularis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**File**: `docker-compose.yml`

**What to include**:

**Service 1 - lenticularis** (main app):
```yaml
services:
  lenticularis:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./data:/app/data
    environment:
      - INFLUXDB_URL=http://influxdb:8086
    depends_on:
      - influxdb  # Optional, comment out for BYO-DB
```

**Service 2 - influxdb** (optional, comment out for BYO-DB):
```yaml
  influxdb:
    image: influxdb:2.7
    ports:
      - "8086:8086"
    volumes:
      - influxdb-data:/var/lib/influxdb2
    environment:
      - DOCKER_INFLUXDB_INIT_MODE=setup
      - DOCKER_INFLUXDB_INIT_USERNAME=admin
      - DOCKER_INFLUXDB_INIT_PASSWORD=changeme
      - DOCKER_INFLUXDB_INIT_ORG=lenticularis
      - DOCKER_INFLUXDB_INIT_BUCKET=weather_data
```

**Volume declarations**:
```yaml
volumes:
  influxdb-data:
```

**PowerShell parallel**:
```powershell
# PowerShell Docker commands:
docker build -t lenticularis .
docker run -p 8000:8000 -v ${PWD}/config.yaml:/app/config.yaml lenticularis
# docker-compose simplifies multi-container orchestration
```

**Health checks**:
- Add health check endpoint `GET /health`
- Docker health check in compose file
- Restart policy: `restart: unless-stopped`

**Documentation**:
- Update README with:
  - `docker-compose up -d` to start
  - How to configure BYO-DB (comment out influxdb service, set external URL)
  - How to access web GUI (http://localhost:8000)
  - How to view API docs (http://localhost:8000/docs)

---

## Summary of Learning Path

1. **Step 0-1**: Foundation (docs, structure, Poetry setup)
2. **Step 2-3**: First data flow (config → collector → raw data)
3. **Step 4-5**: Normalization and persistence (Pydantic models → InfluxDB)
4. **Step 6**: Automation (scheduler runs collectors periodically)
5. **Step 7**: Decision logic (rules engine with traffic lights)
6. **Step 8**: User interface (FastAPI + web map)
7. **Step 9**: Deployment (Docker for homelab)

Each step builds on the previous one, allowing you to learn Python concepts incrementally while building a real, useful application.

## PowerShell → Python Key Concepts

- **Functions** → `def function_name():`
- **Scriptblocks** → `lambda` or regular functions
- **Pipeline** → List comprehensions or generator expressions
- **Objects** → Classes with `__init__` constructor
- **Modules** → Import system with `from module import Class`
- **Error handling** → `try/except` instead of `try/catch`
- **Async** → `async def` and `await` for concurrent operations
- **Type hints** → `variable: str` similar to `[string]$variable`
- **Validation** → Pydantic models instead of `[ValidateNotNull()]`
