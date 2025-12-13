# Lenticularis - AI Instructions & Project Context

## Project Overview

**Lenticularis** is a weather decision support system for paragliding launches. It provides a traffic light system (green/orange/red) based on customizable rules for each launch site, continuously monitors weather stations, and logs decisions for historical analysis.

### Core Purpose
Enable paraglider pilots to codify their personal flying rules into an automated system that:
- Monitors relevant weather stations continuously
- Applies launch-specific decision rules
- Provides clear go/caution/no-go signals
- Records historical data for analysis and learning
- Supports comparison with actual flight logs

### Design Philosophy
- **Reusable**: Works for any region, not just Switzerland
- **Multi-user**: Each user defines their own launches and rules
- **Modular**: Easy to add/remove weather data sources
- **Extensible**: New rule types can be added without breaking existing code
- **Type-safe**: Heavy use of Pydantic for validation

## Current Implementation Status

### ✅ Phase 1: Foundation (90% Complete)

#### Completed
1. **Project Structure** - Clean, modular architecture
2. **Core Application** - FastAPI with proper separation of concerns
3. **Data Models** - Pydantic models for all entities
4. **Database Layer**
   - SQLite3: Launches, stations, rules, associations
   - InfluxDB: Time-series weather data and decisions
5. **Data Collectors**
   - Base collector interface (abstract class)
   - MeteoSwiss: Fully functional (wind, temp, humidity, pressure)
   - Holfuy: Complete implementation structure
6. **API Endpoints**
   - Launches: Full CRUD operations
   - Stations, Rules, Decisions: Stubs ready for implementation
7. **Configuration** - Pydantic Settings with environment variables
8. **Deployment** - Docker + docker-compose for homelab

#### Pending in Phase 1
- Collector scheduler (APScheduler integration)
- Complete stations API implementation
- Background task management

### 🎯 Phase 2: Rule Engine (Next Priority - 0% Complete)

**Goal**: Implement the decision-making logic that evaluates weather conditions against user-defined rules.

#### Architecture Requirements

**Location**: `rules/` directory

**Core Components**:

1. **`rules/engine.py`** - Main evaluation engine
   ```python
   class RuleEngine:
       def evaluate_launch(self, launch_id: int) -> Decision:
           """
           Evaluates all rules for a launch and returns a decision.
           
           Process:
           1. Fetch all active rules for launch
           2. Get latest weather data for associated stations
           3. Evaluate each rule using appropriate evaluator
           4. Aggregate results into traffic light decision
           5. Store decision in InfluxDB
           6. Return decision object
           """
   ```

2. **`rules/evaluators/`** - Individual rule type implementations
   - `base.py` - Abstract evaluator interface
   - `wind.py` - Wind speed and direction evaluation
   - `pressure.py` - Barometric pressure and trends
   - `temperature.py` - Temperature rules
   - `humidity.py` - Humidity rules
   - `trend.py` - Temporal trend analysis (pressure changes over time)
   - `composite.py` - Multi-station comparisons (valley vs ridge)

3. **`rules/operators.py`** - Comparison operators
   ```python
   class Operator(Enum):
       GREATER_THAN = ">"
       LESS_THAN = "<"
       EQUAL = "="
       BETWEEN = "between"
       NOT_IN_RANGE = "not_in_range"
   
   def evaluate_operator(operator: Operator, value: float, 
                        threshold: float, threshold_max: float = None) -> bool:
       """Evaluate a value against threshold using operator"""
   ```

#### Rule Evaluation Logic

**Traffic Light Aggregation**:
- If ANY rule returns RED → Overall RED
- Else if ANY rule returns ORANGE → Overall ORANGE  
- Else → Overall GREEN

**Priority Consideration**:
- Rules have priority 1-10
- Higher priority rules weigh more in the decision
- Could implement weighted scoring system

**Example Rule**:
```python
Rule(
    launch_id=1,
    rule_type="wind_speed",
    station_id="INT",  # Interlaken station
    operator=">",
    threshold_value=8.0,  # 8 m/s
    severity="red",
    priority=10,
    description="Wind speed above 8 m/s is unsafe"
)
```

**Wind Direction Rules**:
```python
# Special handling needed for circular direction values (0-360°)
# Must handle cases like: acceptable range 330-30 (crosses 0)

def is_direction_in_range(direction: int, 
                          acceptable_directions: List[str]) -> bool:
    """
    acceptable_directions example: ["N", "NW", "W"]
    Convert to ranges: N=337.5-22.5, NW=292.5-337.5, W=247.5-292.5
    """
```

#### Data Flow for Decision Making

```
1. Trigger (scheduled or on-demand)
   ↓
2. RuleEngine.evaluate_launch(launch_id)
   ↓
3. Fetch rules from SQLite
   ↓
4. For each rule:
   a. Get station_id (or all associated stations)
   b. Query latest weather data from InfluxDB
   c. Instantiate appropriate evaluator
   d. Evaluate rule → returns (passed: bool, severity: Severity)
   ↓
5. Aggregate rule results:
   - Collect all triggered severities
   - Apply traffic light logic
   - Build contributing_factors dict
   ↓
6. Create Decision object
   ↓
7. Store in InfluxDB (launch_decisions measurement)
   ↓
8. Return Decision
```

### 🔄 Phase 3: Scheduler & Background Tasks (After Rule Engine)

**Goal**: Continuous data collection and decision evaluation

**Components**:
- `collectors/scheduler.py` - APScheduler integration
- Background collector runs every 10 minutes (configurable)
- Background decision evaluator runs after each collection
- Health monitoring for collectors

**Implementation**:
```python
from apscheduler.schedulers.background import BackgroundScheduler

class CollectorScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.collectors = self._initialize_collectors()
    
    def start(self):
        """Start scheduled collection"""
        self.scheduler.add_job(
            self.collect_all,
            'interval',
            minutes=settings.COLLECTOR_INTERVAL_SECONDS // 60,
            id='weather_collection'
        )
        self.scheduler.start()
    
    def collect_all(self):
        """Collect from all enabled sources"""
        for collector in self.collectors:
            try:
                data = collector.collect()
                # Store in InfluxDB
                influx.write_weather_data(data)
            except CollectorError as e:
                logger.error(f"Collection failed: {e}")
```

### 🎨 Phase 4: Web UI (After Rule Engine)

**Goal**: User-friendly interface for managing launches and rules

**Components**:
- Dashboard: Current conditions for all launches
- Launch Management: Create/edit launches
- Rule Builder: Visual interface for creating rules
- History View: Historical decisions and weather data
- Webcam Integration: Display webcam images

**Technology**: 
- FastAPI + Jinja2 templates
- Or consider: Streamlit / Dash for rapid development
- Frontend: Vanilla JS or Alpine.js for interactivity

**Key Views**:
1. Dashboard (`/`) - Overview of all launches
2. Launch Detail (`/launch/{id}`) - Detailed view with rules and webcams
3. Rule Editor (`/launch/{id}/rules`) - Manage rules
4. History (`/launch/{id}/history`) - Charts and statistics

### 📢 Phase 5: Alerting System

**Goal**: Notify users when conditions become flyable or change status

**Integrations**:
- `integrations/signal.py` - Signal messenger
- `integrations/discord.py` - Discord webhooks
- `integrations/telegram.py` - Telegram bot

**Alert Triggers**:
- Status changes: RED→ORANGE, ORANGE→GREEN, etc.
- Specific conditions met (e.g., wind drops below threshold)
- User-defined alert rules

### 📊 Phase 6: Analytics & Insights

**Goal**: Learn from historical data and flight logs

**Features**:
- Flyable days statistics per launch
- Most common no-go reasons
- Correlation analysis between stations
- XContest integration for flight log comparison
- "Would have flown" analysis (flights outside rule parameters)

## Technical Guidelines

### Code Style
- **Type hints everywhere**: All functions have parameter and return type hints
- **Docstrings**: All classes and public methods have docstrings
- **Error handling**: Specific exceptions, proper logging
- **Testing**: Write tests for business logic (collectors, rules, evaluators)

### Database Strategy

**SQLite3 (Relational Data)**:
- Metadata that doesn't change frequently
- Launches, stations, rules, user config
- Foreign keys for relationships
- Transactions for data integrity

**InfluxDB (Time-Series Data)**:
- Weather measurements (high volume, continuous)
- Launch decisions (historical tracking)
- Alert events
- Collector health metrics

### API Design Principles
- RESTful endpoints
- Consistent response formats
- Proper HTTP status codes
- Pagination for list endpoints (future)
- API versioning (`/api/v1/`)

### Collector Requirements

When adding new collectors:

1. **Inherit from BaseCollector**
2. **Implement required methods**:
   - `fetch_data()` - Get raw data from source
   - `normalize_data()` - Convert to standard format
3. **Standard format**:
   ```python
   {
       "station_id": str,
       "source": str,
       "timestamp": datetime,
       "wind_speed": float,      # m/s
       "wind_direction": int,     # degrees 0-360
       "gust_speed": float,       # m/s
       "gust_direction": int,     # degrees
       "temperature": float,      # Celsius
       "humidity": float,         # %
       "pressure": float,         # hPa
       "rain": float             # mm
   }
   ```
4. **Error handling**: Raise `CollectorError` on failures
5. **Logging**: Use `self.logger` for all logging

### Adding New Rule Types

1. Create evaluator in `rules/evaluators/`
2. Inherit from base evaluator
3. Implement `evaluate()` method
4. Add to `RuleType` enum
5. Register in rule engine factory
6. Add tests

Example:
```python
class WindSpeedEvaluator(BaseEvaluator):
    def evaluate(self, weather_data: WeatherData, 
                 rule: Rule) -> Tuple[bool, str]:
        """
        Returns: (passed, message)
        passed=True means rule did NOT trigger (condition OK)
        passed=False means rule triggered (condition violated)
        """
        wind_speed = weather_data.wind_speed
        
        if rule.operator == ">":
            passed = wind_speed <= rule.threshold_value
            message = f"Wind {wind_speed} m/s"
        
        return passed, message
```

## Environment Configuration

**Required Environment Variables**:
```bash
# InfluxDB (Critical - must be configured)
INFLUXDB_URL=http://your-influx:8086
INFLUXDB_TOKEN=your-token
INFLUXDB_ORG=lenticularis
INFLUXDB_BUCKET=weather_data

# Optional API Keys
HOLFUY_API_KEY=

# Optional Alerts
DISCORD_WEBHOOK_URL=
SIGNAL_PHONE_NUMBER=
TELEGRAM_BOT_TOKEN=
```

## Development Workflow

### When Working on New Features

1. **Understand the context**: Read this file and relevant architecture docs
2. **Check existing code**: Look at similar implementations
3. **Follow patterns**: Use established patterns (e.g., base classes, Pydantic models)
4. **Type hints**: Always include type hints
5. **Error handling**: Catch specific exceptions, log appropriately
6. **Testing**: Write tests for business logic
7. **Documentation**: Update docstrings and docs as needed

### Testing Strategy

- **Unit tests**: Test individual components (evaluators, collectors)
- **Integration tests**: Test API endpoints, database interactions
- **Manual tests**: Use `test_collectors.py` for quick validation

### Common Patterns

**Repository Pattern** (Database Access):
```python
class LaunchRepository:
    def get_by_id(self, launch_id: int) -> Launch:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM launches WHERE id = ?", (launch_id,))
            row = cursor.fetchone()
            return self._row_to_model(row)
```

**Service Layer** (Business Logic):
```python
class LaunchService:
    def __init__(self):
        self.repo = LaunchRepository()
        self.rule_engine = RuleEngine()
    
    def get_current_decision(self, launch_id: int) -> Decision:
        # Business logic here
        return self.rule_engine.evaluate_launch(launch_id)
```

## Key Decisions & Rationale

### Why SQLite + InfluxDB?
- **SQLite**: Simple, embedded, perfect for configuration data
- **InfluxDB**: Optimized for time-series, supports downsampling, retention policies
- **Separation**: Query patterns are different (metadata vs. measurements)

### Why FastAPI?
- Modern Python web framework
- Automatic API documentation
- Type hints for validation
- Async support for future scalability
- Large ecosystem

### Why Docker?
- Consistent deployment
- Easy to run in homelab
- Isolates dependencies
- Connects to existing InfluxDB easily

### Weather Station Strategy
- Collect ALL available stations initially (e.g., all MeteoSwiss)
- Users choose which stations to associate with their launches
- Future: Support user-adding custom stations

## User Workflow (Target Experience)

1. **Setup**: User deploys with Docker, connects to InfluxDB
2. **Create Launch**: User adds their launch sites (lat/lon, elevation)
3. **Associate Stations**: User links nearby weather stations to launch
4. **Define Rules**: User creates rules (e.g., "wind > 8 m/s = red")
5. **Monitor**: System continuously collects data and evaluates rules
6. **Decide**: User checks dashboard or receives alerts
7. **Learn**: User reviews historical decisions vs. actual flights

## Future Considerations

### Authentication & Multi-User
- Currently single-user system
- Future: Add user authentication
- Each user has their own launches and rules
- Shared weather data (same InfluxDB bucket)

### Mobile App
- After web UI is stable
- Native app or PWA
- Push notifications instead of Signal/Discord

### Machine Learning
- Learn from user's actual flight decisions
- Suggest rule adjustments
- Predict flyable windows

### Community Features
- Share launch configurations
- Community-vetted rule sets
- Launch rating/popularity

## Critical Implementation Notes

### Wind Direction Handling
Wind direction is circular (0° = 360°). Special care needed:
```python
def normalize_direction(deg: int) -> int:
    """Normalize to 0-360 range"""
    return deg % 360

def direction_difference(dir1: int, dir2: int) -> int:
    """Calculate smallest angle between two directions"""
    diff = abs(dir1 - dir2)
    if diff > 180:
        diff = 360 - diff
    return diff
```

### Pressure Trends
Need historical data to calculate trends:
- Query last 3 hours of pressure data
- Calculate rate of change (hPa/hour)
- Rapid drops indicate weather changes

### Time Zones
- Store all timestamps in UTC
- Convert for display based on launch location
- Be careful with InfluxDB query ranges

### Data Quality
- Handle missing measurements gracefully
- Station might not report all parameters
- Old data (>1 hour) should be flagged

## Files to Reference

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Detailed architecture design
- **[README.md](README.md)** - Project overview
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Commands and API reference
- **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)** - Current progress

## Common Tasks

### Adding a New Weather Source

1. Create `collectors/sources/newsource.py`
2. Inherit from `BaseCollector`
3. Implement `fetch_data()` and `normalize_data()`
4. Test with `test_collectors.py`
5. Document API endpoints and requirements

### Adding a New Rule Type

1. Add to `RuleType` enum in `app/models/rule.py`
2. Create evaluator in `rules/evaluators/newtype.py`
3. Register in rule engine
4. Add tests
5. Update API documentation

### Debugging Data Collection

```bash
# Test a specific collector
python -c "from collectors.sources.meteoswiss import MeteoSwissCollector; \
           import json; c = MeteoSwissCollector(); \
           data = c.collect(); print(json.dumps(data[0], indent=2, default=str))"

# Check database
python -c "from app.db.sqlite.connection import db; \
           import sqlite3; \
           with db.get_connection() as conn: \
               c = conn.cursor(); \
               c.execute('SELECT COUNT(*) FROM stations'); \
               print(f'Stations: {c.fetchone()[0]}')"
```

## Success Metrics

**Phase 2 Complete When**:
- Rule engine evaluates all rule types correctly
- Decisions stored in InfluxDB
- API returns current decision for any launch
- Background evaluation runs on schedule

**Phase 3 Complete When**:
- Data collection runs automatically every 10 minutes
- Health monitoring tracks collector status
- Errors logged and handled gracefully

**Production Ready When**:
- User can manage launches and rules via web UI
- Receives alerts when conditions change
- Has 30+ days of historical data
- Can compare past decisions with flight logs

---

## Current AI Context (December 2025)

**What's Done**: Foundation is complete - app structure, collectors, database, basic API

**Next Task**: Implement rule engine (Phase 2)

**User Profile**: Paraglider pilot since 2018, fluent in PowerShell, learning Python, runs homelab with InfluxDB, focuses on Interlaken/Mürren/Grindelwald regions but system is designed to be region-agnostic.

**Deployment Target**: Docker container connecting to existing InfluxDB homelab instance

**Current Focus**: Get to MVP where user can create launches, define rules, and see traffic light decisions based on real weather data.

---

*This file should be updated as the project evolves and new patterns emerge.*
