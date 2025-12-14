```markdown
# Lenticularis

Weather data tracking and analysis system for paragliding decision support.

## Overview

Lenticularis collects weather data from multiple Swiss weather networks (MeteoSwiss, Holfuy, SLF, Windline, Ecovitt), normalizes the data, stores it in InfluxDB, and applies customizable rule-based analysis to create traffic light decisions (GREEN/ORANGE/RED) for paragliding launch sites.

## Features

- **Multi-source data collection**: Fetch weather data from multiple Swiss weather networks
- **Automated scheduling**: Periodic data collection (e.g., MeteoSwiss every 10 minutes)
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
├── config.yaml.example  # Configuration template
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
   cp config.yaml.example config.yaml
   # Edit config.yaml with your settings
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

See [config.yaml.example](config.yaml.example) for all configuration options.

### Bring Your Own Database

To use an existing InfluxDB instance, update `config.yaml`:

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

This project is in active development. Current progress:

- [x] Project structure and configuration
- [ ] Weather data collectors
- [ ] Data normalization
- [ ] InfluxDB integration
- [ ] Rule engine
- [ ] Web API
- [ ] Interactive map GUI
- [ ] Docker deployment

## Contributing

This is a learning project - contributions and suggestions welcome!

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Weather data provided by MeteoSwiss, Holfuy, SLF, Windline, and Ecovitt
- Built as a learning project to understand Python through practical application
```