# Lenticularis - Getting Started

## Quick Start

### 1. Clone and Setup

```bash
cd c:\git\Lenticularis
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
copy .env.example .env
# Edit .env with your InfluxDB connection details
```

### 3. Initialize Database

```bash
python init_db.py
```

### 4. Seed Weather Stations (Optional)

```bash
python seed_stations.py
```

### 5. Test Collectors

```bash
python test_collectors.py
```

### 6. Run the Application

```bash
python main.py
```

Visit: http://localhost:8000

API Documentation: http://localhost:8000/docs

### 7. Docker Deployment

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## Project Structure Overview

- `app/` - FastAPI application
  - `api/v1/` - API endpoints
  - `core/` - Configuration and utilities
  - `db/` - Database connections
  - `models/` - Pydantic models
- `collectors/` - Weather data collectors
  - `sources/` - Individual source implementations
- `rules/` - Rule engine (to be implemented)
- `integrations/` - Alert integrations (to be implemented)

## Next Steps

1. Create your first launch site via API or future web UI
2. Define rules for that launch
3. Set up data collection schedule
4. Configure alerts (Signal/Discord/Telegram)

See ARCHITECTURE.md for detailed information.
