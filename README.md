# Lenticularis (Lenti)

**An intelligent weather forecasting decision support system for paragliding launches.**

## 🎯 Project Vision

Lenticularis is a flexible weather analysis tool designed to support paragliding launch decisions by providing a traffic light system (green/orange/red) based on customizable rule sets for each launch site. The system continuously monitors weather stations, applies your personal flying rules, and logs decisions in a time-series database for future analysis.

### Core Problem Statement
Paraglider pilots develop mental rules for each launch site based on wind strength, wind direction, barometric pressure changes, humidity, and temperature. This project codifies those rules into an automated decision-making system that:
- Continuously monitors relevant weather stations
- Applies launch-specific rules
- Provides clear go/caution/no-go signals
- Records historical data for analysis
- Enables comparison with actual flight logs

### Scope & Extensibility
- **Initial Focus**: Switzerland (comprehensive MeteoSwiss coverage + additional sources)
- **Design Philosophy**: Fully reusable for any region or country
- **Multi-user Ready**: Each user can define their own launch sites and rules
- **Data Source Agnostic**: Modular collectors allow adding any weather data provider

## 🚀 Key Features

### Current Phase Features
- ✅ **Multi-source Weather Data Fetching**: Modular collector system supporting Holfuy, MeteoSwiss, SLF, and Windline stations
- 🔄 **Data Normalization**: Unified data format across different weather sources
- 🔄 **Time-series Storage**: InfluxDB for weather measurements and decisions
- 🔄 **Rule Engine**: Configurable rules per launch site
- 🔄 **Web GUI**: Dashboard showing current conditions and decisions

### Planned Features
- 📱 **Alert Integrations**: Signal and Discord notifications
- 📊 **Statistics & Analytics**: Historical flight condition analysis
- 📷 **Webcam Integration**: Live webcam feeds on launch dashboards
- 🔍 **Flight Log Analysis**: Compare historic weather data with actual flights (XContest integration)
- 📈 **Rule Validation**: Identify flights outside your rule parameters

## 🏗️ Architecture

### Technology Stack
- **Backend**: Python 3.x (FastAPI)
- **Relational DB**: SQLite3 (launch sites, rules, configuration)
- **Time-series DB**: InfluxDB (weather measurements, decisions)
- **Web Framework**: FastAPI with Jinja2 templates (or alternative: Streamlit/Dash)
- **Data Collection**: Modular collector services
- **Deployment**: Docker containers (primary deployment method)

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                    Web GUI Layer                        │
│  (Dashboards, Launch Management, Rule Configuration)    │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│                  Rule Engine API                        │
│  (Evaluate conditions, Generate traffic light signals)  │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┴───────────┐
          │                      │
┌─────────▼──────────┐  ┌───────▼─────────┐
│   SQLite3 DB       │  │   InfluxDB      │
│ (Static Data)      │  │ (Time-series)   │
│ - Launch sites     │  │ - Weather data  │
│ - Rules            │  │ - Decisions     │
│ - Weather stations │  │ - Alerts        │
└────────────────────┘  └─────────────────┘
          ▲                      ▲
          │                      │
┌─────────┴──────────────────────┴─────────────────────┐
│           Data Collector Services                     │
│  (Modular fetchers for different weather sources)    │
│  - Holfuy        - MeteoSwiss                        │
│  - SLF           - Windline                          │
│  - [Extensible for new sources]                      │
└──────────────────────────────────────────────────────┘
```

## 📋 Development Roadmap

### Phase 1: Foundation (Weeks 1-2) ✅ IN PROGRESS
- [x] Project structure setup
- [x] Basic data collectors (Holfuy, MeteoSwiss, SLF, Windline)
- [ ] Data normalization layer
- [ ] SQLite3 schema for launches and rules
- [ ] InfluxDB integration and schema
- [ ] Basic API structure (FastAPI)

### Phase 2: Rule Engine (Weeks 3-4)
- [ ] Rule definition data model
- [ ] Rule evaluation engine
  - Wind speed thresholds (green/orange/red)
  - Wind direction preferences
  - Pressure trend analysis
  - Humidity and temperature rules
  - Multi-station comparison logic
- [ ] Traffic light decision algorithm
- [ ] Background scheduler for continuous evaluation

### Phase 3: Web Interface (Weeks 5-6)
- [ ] Launch site management UI
  - Create/edit/delete launch sites
  - Associate weather stations with launches
- [ ] Rule configuration UI
  - Visual rule builder
  - Threshold sliders and input fields
- [ ] Dashboard views
  - Current conditions per launch
  - Traffic light indicators
  - Historical decision timeline
  - Weather station data visualization

### Phase 4: Data Visualization (Week 7)
- [ ] Time-series charts (wind, pressure, temperature)
- [ ] Launch-specific dashboards
- [ ] Webcam integration framework
- [ ] Export functionality (CSV, JSON)

### Phase 5: Alerting System (Week 8)
- [ ] Alert rule configuration
- [ ] Signal messenger integration
- [ ] Discord webhook integration
- [ ] Notification preferences (frequency, conditions)

### Phase 6: Analytics & Insights (Weeks 9-10)
- [ ] Statistics dashboard
  - Flyable days per month/season
  - Most common no-go reasons
  - Launch site comparisons
- [ ] Historical data import
- [ ] XContest API integration
- [ ] Flight log upload and parsing
- [ ] Actual vs. predicted conditions analysis

### Phase 7: Polish & Deployment (Week 11-12)
- [ ] Docker containerization (app services)
- [ ] Docker Compose orchestration (connects to existing InfluxDB)
- [ ] Configuration management (environment variables)
- [ ] Logging and monitoring
- [ ] Documentation
- [ ] Backup and restore functionality
- [ ] Performance optimization

## 🗂️ Project Structure

```
Lenticularis/
├── app/
│   ├── core/           # Core utilities, config
│   ├── db/             # Database connections and models
│   ├── models/         # Data models (Pydantic)
│   ├── routers/        # API endpoints (FastAPI)
│   ├── static/         # CSS, JS, images
│   └── templates/      # HTML templates (Jinja2)
├── collectors/
│   ├── sources/        # Individual weather source fetchers
│   │   ├── holfuy.py
│   │   ├── meteoswiss.py
│   │   ├── slf.py
│   │   └── windline.py
│   ├── base.py         # Base collector interface
│   └── scheduler.py    # Collection orchestration
├── rules/
│   ├── engine.py       # Rule evaluation logic
│   ├── models.py       # Rule data structures
│   └── evaluators/     # Specific rule type evaluators
├── integrations/
│   ├── signal.py       # Signal messenger
│   ├── discord.py      # Discord webhooks
│   └── xcontest.py     # XContest API
├── tests/              # Unit and integration tests
├── migrations/         # Database migrations
├── config/             # Configuration files
├── docker/             # Docker configurations
├── docs/               # Additional documentation
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
└── main.py             # Application entry point
```

## 🎯 Key Decision Parameters

### Primary Factors
- **Wind Strength**: Speed thresholds (green < X m/s, orange X-Y m/s, red > Y m/s)
- **Wind Direction**: Favorable directions per launch (e.g., Interlaken prefers W-NW)
- **Wind Gusts**: Gust factor relative to average speed

### Secondary Factors
- **Barometric Pressure Trends**: Rapid changes indicate instability
- **Humidity Changes**: Sudden increases may indicate incoming weather
- **Temperature**: Thermal activity indicators, cold front detection
- **Multi-station Comparison**: Valley vs. ridge readings, gradient analysis

### Launch-Specific Customization
Each launch site can have unique rule sets tailored to:
- Local wind patterns (valley winds, föhn effects, rotors)
- Preferred wind directions and launch orientation
- Thermal characteristics and timing windows
- Elevation-specific considerations
- Nearby terrain and obstruction factors

## 🚦 Traffic Light System

- 🟢 **GREEN (Flyable)**: All parameters within safe ranges
- 🟠 **ORANGE (Caution)**: Some parameters in warning range, experienced pilots only
- 🔴 **RED (No-Go)**: Critical parameters exceeded, unsafe conditions

## 📊 Data Model Overview

### Launches Table (SQLite3)
```sql
- id, name, location, elevation
- latitude, longitude
- description, notes
- preferred_wind_directions
- associated_weather_stations
- webcam_urls
```

### Rules Table (SQLite3)
```sql
- id, launch_id, rule_type
- parameter_name, operator, threshold_value
- severity (green_max, orange_max, red_min)
- active, priority
```

### Weather Measurements (InfluxDB)
```
measurement: weather_data
tags: station_id, source, location
fields: wind_speed, wind_direction, gust, 
        pressure, humidity, temperature, rain
time: timestamp
```

### Launch Decisions (InfluxDB)
```
measurement: launch_decisions
tags: launch_id, launch_name
fields: status (green/orange/red), 
        contributing_factors, 
        station_readings
time: timestamp
```

## 🔧 Getting Started (Planned)

### Prerequisites
- Docker & Docker Compose
- InfluxDB 2.x (can be existing instance)
- Python 3.10+ (for development)

### Installation

#### Docker Deployment (Recommended)
```bash
# Clone the repository
git clone https://github.com/Think4dvantage/Lenticularis.git
cd Lenticularis

# Configure environment
cp .env.example .env
# Edit .env with your InfluxDB connection and API keys

# Build and run
docker-compose up -d

# Access the web interface
http://localhost:8000
```

#### Local Development
```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env

# Initialize databases
python init_db.py

# Run the application
python main.py
```

## 🌍 Multi-User & Regional Support

While initially focused on Swiss weather stations, Lenticularis is designed to be region-agnostic:
- Add weather stations from any provider worldwide
- Create launch sites in any location
- Define custom rules per launch
- Each user maintains their own launch library and rule sets

## 🤝 Contributing

Contributions welcome! Particularly interested in:
- Additional weather data source collectors
- Regional weather station databases
- UI/UX improvements
- Rule engine enhancements

## 📝 License

See LICENSE file for details.

## 🎓 Learning Journey

This project serves as a practical Python learning experience, demonstrating real-world application architecture, API integration, time-series data handling, and rule engine design.

---

**Status**: Active Development | **Started**: December 2025 | **Initial Release Target**: Q1 2026
