# Lenticularis Architecture & Repository Structure

## Proposed Repository Structure

### Current Structure Analysis
The existing structure has a good foundation but could be improved for better separation of concerns and scalability.

### Recommended Structure

```
Lenticularis/
├── .github/
│   └── workflows/          # CI/CD pipelines
├── app/                    # Main application (FastAPI)
│   ├── __init__.py
│   ├── main.py            # FastAPI app entry point
│   ├── config.py          # Configuration management
│   ├── dependencies.py    # Dependency injection
│   │
│   ├── api/               # API layer (renamed from routers)
│   │   ├── __init__.py
│   │   ├── v1/            # API versioning
│   │   │   ├── __init__.py
│   │   │   ├── launches.py      # Launch management endpoints
│   │   │   ├── rules.py         # Rule management endpoints
│   │   │   ├── stations.py      # Weather station endpoints
│   │   │   ├── decisions.py     # Launch decision endpoints
│   │   │   └── analytics.py     # Statistics and insights
│   │   └── deps.py        # API dependencies
│   │
│   ├── core/              # Core utilities
│   │   ├── __init__.py
│   │   ├── config.py      # Settings and configuration
│   │   ├── security.py    # Authentication (future)
│   │   └── logging.py     # Logging configuration
│   │
│   ├── db/                # Database layer
│   │   ├── __init__.py
│   │   ├── sqlite/        # SQLite (relational data)
│   │   │   ├── __init__.py
│   │   │   ├── connection.py
│   │   │   ├── models.py        # SQLAlchemy models
│   │   │   └── schemas.py       # Table definitions
│   │   ├── influx/        # InfluxDB (time-series)
│   │   │   ├── __init__.py
│   │   │   ├── connection.py
│   │   │   ├── client.py
│   │   │   └── queries.py
│   │   └── base.py        # Base database interface
│   │
│   ├── models/            # Pydantic models (API schemas)
│   │   ├── __init__.py
│   │   ├── launch.py
│   │   ├── rule.py
│   │   ├── station.py
│   │   ├── weather.py
│   │   └── decision.py
│   │
│   ├── services/          # Business logic layer
│   │   ├── __init__.py
│   │   ├── launch_service.py
│   │   ├── rule_service.py
│   │   ├── decision_service.py
│   │   └── analytics_service.py
│   │
│   ├── static/            # Static assets
│   │   ├── css/
│   │   ├── js/
│   │   └── images/
│   │
│   └── templates/         # Jinja2 templates
│       ├── base.html
│       ├── launches/
│       ├── dashboard/
│       └── rules/
│
├── collectors/            # Data collection services
│   ├── __init__.py
│   ├── base.py           # Abstract base collector
│   ├── scheduler.py      # Collection orchestration
│   ├── normalizer.py     # Data normalization
│   │
│   └── sources/          # Weather data sources
│       ├── __init__.py
│       ├── base_source.py      # Source interface
│       ├── holfuy.py           # Holfuy stations
│       ├── meteoswiss.py       # MeteoSwiss (all stations)
│       ├── slf.py              # SLF avalanche/mountain stations
│       ├── windline.py         # Windline network
│       └── README.md           # Guide for adding new sources
│
├── rules/                # Rule engine
│   ├── __init__.py
│   ├── engine.py         # Main evaluation engine
│   ├── models.py         # Rule data structures
│   │
│   ├── evaluators/       # Rule type implementations
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── wind.py            # Wind speed/direction rules
│   │   ├── pressure.py        # Barometric pressure rules
│   │   ├── temperature.py     # Temperature rules
│   │   ├── humidity.py        # Humidity rules
│   │   ├── trend.py           # Trend analysis rules
│   │   └── composite.py       # Multi-station comparisons
│   │
│   └── operators.py      # Comparison operators (>, <, between, etc.)
│
├── integrations/         # External service integrations
│   ├── __init__.py
│   ├── base.py
│   ├── signal.py         # Signal messenger notifications
│   ├── discord.py        # Discord webhooks
│   ├── telegram.py       # Telegram bot (alternative)
│   └── xcontest.py       # XContest flight log API
│
├── utils/                # Shared utilities
│   ├── __init__.py
│   ├── datetime_helpers.py
│   ├── geo_helpers.py    # Coordinate calculations, distances
│   └── validators.py
│
├── tests/                # Test suite
│   ├── __init__.py
│   ├── conftest.py       # Pytest configuration
│   ├── unit/
│   │   ├── test_collectors/
│   │   ├── test_rules/
│   │   └── test_services/
│   ├── integration/
│   │   ├── test_api/
│   │   └── test_db/
│   └── fixtures/         # Test data
│
├── migrations/           # Database migrations
│   ├── sqlite/
│   │   └── versions/
│   └── influx/
│       └── schemas/
│
├── config/               # Configuration files
│   ├── stations/         # Pre-configured station lists
│   │   ├── meteoswiss_stations.json
│   │   ├── holfuy_stations.json
│   │   └── README.md
│   └── examples/         # Example configurations
│       ├── launch_example.json
│       └── rules_example.json
│
├── docker/               # Docker configurations
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .dockerignore
│   └── nginx/            # Reverse proxy config (if needed)
│
├── scripts/              # Utility scripts
│   ├── init_db.py
│   ├── seed_stations.py  # Import all MeteoSwiss stations
│   ├── migrate.py
│   └── backup.py
│
├── docs/                 # Documentation
│   ├── api/
│   ├── setup/
│   ├── user_guide/
│   └── development/
│
├── .env.example         # Environment variable template
├── .gitignore
├── requirements.txt     # Production dependencies
├── requirements-dev.txt # Development dependencies
├── pytest.ini
├── README.md
├── ARCHITECTURE.md      # This file
├── CONTRIBUTING.md
├── LICENSE
└── setup.py             # Package configuration

```

## Key Structure Improvements

### 1. **Separation of Concerns**
- **`app/api/`**: API endpoints only (no business logic)
- **`app/services/`**: Business logic layer (testable, reusable)
- **`app/db/`**: Database interactions separated by database type
- **`app/models/`**: Pydantic models for API contracts

### 2. **Scalable Collectors**
- **`collectors/base.py`**: Abstract interface for all collectors
- **`collectors/sources/`**: Each source is a plugin
- **`collectors/normalizer.py`**: Centralized data normalization
- **`collectors/scheduler.py`**: Manages collection timing and orchestration

### 3. **Flexible Rule Engine**
- **`rules/evaluators/`**: Each rule type is isolated
- **`rules/operators.py`**: Reusable comparison logic
- **`rules/engine.py`**: Orchestrates rule evaluation

### 4. **Database Strategy**
```
SQLite3 (Relational):
- Launch sites (static locations)
- Weather stations (registry)
- Rules (configuration)
- Users (future)
- Launch-Station associations

InfluxDB (Time-series):
- Weather measurements (continuous data)
- Launch decisions (historical decisions)
- Alert events
- Collector health metrics
```

### 5. **Configuration Management**
- **`config/stations/`**: Pre-built station databases (all MeteoSwiss, etc.)
- **`.env`**: Secrets and environment-specific settings
- **`app/core/config.py`**: Typed configuration with Pydantic

### 6. **Testing Strategy**
- Unit tests: Test individual components in isolation
- Integration tests: Test API endpoints and database interactions
- Fixtures: Reusable test data

### 7. **Docker Architecture**
```yaml
# Proposed docker-compose.yml structure
services:
  lenticularis-app:
    build: .
    depends_on:
      - influxdb  # Link to existing InfluxDB
    environment:
      - INFLUXDB_URL=http://influxdb:8086
      - INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
    volumes:
      - ./data/sqlite:/app/data
    ports:
      - "8000:8000"
  
  # Reference to existing InfluxDB
  influxdb:
    external: true  # Uses existing container
```

## Data Flow

### 1. Weather Data Collection Flow
```
Scheduler → Collector Sources → Normalizer → InfluxDB
                                          ↓
                                    SQLite (station metadata)
```

### 2. Decision Making Flow
```
Trigger (scheduled/on-demand) → Decision Service
                                     ↓
                              Rule Engine ← Rules (SQLite)
                                     ↓
                              Weather Data (InfluxDB)
                                     ↓
                              Evaluate → Traffic Light
                                     ↓
                              Store Decision (InfluxDB)
                                     ↓
                              Alert Service (if thresholds met)
```

### 3. API Request Flow
```
Client → FastAPI Router → Service Layer → DB Layer → Database
                             ↓
                        Business Logic
                             ↓
                        Validation (Pydantic)
```

## Design Patterns

### 1. **Repository Pattern** (Database Access)
```python
class LaunchRepository:
    def get_all(self) -> List[Launch]: ...
    def get_by_id(self, id: int) -> Launch: ...
    def create(self, launch: LaunchCreate) -> Launch: ...
```

### 2. **Strategy Pattern** (Rule Evaluators)
```python
class RuleEvaluator(ABC):
    @abstractmethod
    def evaluate(self, data: WeatherData) -> RuleResult: ...

class WindSpeedEvaluator(RuleEvaluator): ...
class PressureTrendEvaluator(RuleEvaluator): ...
```

### 3. **Factory Pattern** (Collector Creation)
```python
class CollectorFactory:
    @staticmethod
    def create(source_type: str) -> BaseCollector:
        if source_type == "meteoswiss":
            return MeteoSwissCollector()
        elif source_type == "holfuy":
            return HolfuyCollector()
```

### 4. **Observer Pattern** (Alerts)
```python
class DecisionSubject:
    def notify_observers(self, decision: Decision):
        for observer in self.observers:
            observer.update(decision)

class SignalAlertObserver(Observer): ...
class DiscordAlertObserver(Observer): ...
```

## API Design

### RESTful Endpoints Structure
```
GET    /api/v1/launches              # List all launches
POST   /api/v1/launches              # Create new launch
GET    /api/v1/launches/{id}         # Get launch details
PUT    /api/v1/launches/{id}         # Update launch
DELETE /api/v1/launches/{id}         # Delete launch

GET    /api/v1/launches/{id}/rules   # Get rules for launch
POST   /api/v1/launches/{id}/rules   # Add rule to launch

GET    /api/v1/launches/{id}/decision        # Current decision
GET    /api/v1/launches/{id}/decision/history # Historical decisions

GET    /api/v1/stations              # List weather stations
GET    /api/v1/stations/{id}/data    # Latest weather data

GET    /api/v1/analytics/flyable-days        # Statistics
GET    /api/v1/analytics/launches/compare    # Compare launches
```

## Why This Structure?

### Advantages
1. **Modularity**: Each component is independent and replaceable
2. **Testability**: Services and evaluators are easily unit-tested
3. **Scalability**: Easy to add new collectors, rules, or integrations
4. **Maintainability**: Clear separation makes debugging easier
5. **Multi-user Ready**: Structure supports future user management
6. **Docker-friendly**: Clear separation of concerns for containerization

### Following Best Practices
- **Dependency Injection**: Services receive dependencies, not hard-coded
- **Interface Segregation**: Small, focused interfaces
- **Single Responsibility**: Each module has one clear purpose
- **DRY (Don't Repeat Yourself)**: Shared utilities and base classes

## Migration Path

To migrate from current structure:
1. Keep existing `collectors/sources/*.py` files (move content to new structure)
2. Create `app/services/` layer to hold business logic
3. Separate API routes from business logic
4. Create `rules/` directory structure
5. Add `integrations/` for future alert systems
6. Implement proper configuration management

---

**Note**: This structure is a recommendation based on Python/FastAPI best practices. Adjust as needed based on project evolution.
