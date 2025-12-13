# Lenticularis - Quick Reference

## 🚀 Common Commands

### Development
```bash
# Activate virtual environment
venv\Scripts\activate

# Install/update dependencies
pip install -r requirements.txt

# Run locally
python main.py

# Initialize database
python init_db.py

# Seed weather stations
python seed_stations.py

# Test collectors
python test_collectors.py
```

### Docker
```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Restart
docker-compose restart

# Stop
docker-compose down

# Rebuild after code changes
docker-compose up -d --build
```

## 🌐 API Endpoints

Base URL: `http://localhost:8000`

### Launch Sites
```bash
# List all launches
GET /api/v1/launches/

# Create launch
POST /api/v1/launches/
Body: {
  "name": "Beatenberg",
  "location": "Interlaken Region",
  "latitude": 46.6973,
  "longitude": 7.7909,
  "elevation": 1200,
  "description": "NW launch with great thermals",
  "preferred_wind_directions": "NW,W,N",
  "webcam_urls": "https://example.com/webcam.jpg",
  "active": true
}

# Get specific launch
GET /api/v1/launches/{id}

# Update launch
PUT /api/v1/launches/{id}
Body: {partial fields}

# Delete launch
DELETE /api/v1/launches/{id}
```

### Weather Stations
```bash
# List all stations
GET /api/v1/stations/

# Get specific station
GET /api/v1/stations/{station_id}
```

### Rules
```bash
# Get rules for a launch
GET /api/v1/rules/launch/{launch_id}

# Create rule
POST /api/v1/rules/launch/{launch_id}
```

### Decisions
```bash
# Get current decision
GET /api/v1/decisions/launch/{launch_id}

# Get decision history
GET /api/v1/decisions/launch/{launch_id}/history
```

### Documentation
```bash
# Interactive API docs (Swagger UI)
http://localhost:8000/docs

# Alternative docs (ReDoc)
http://localhost:8000/redoc
```

## 📁 Project Structure

```
Lenticularis/
├── app/                      # Main application
│   ├── api/v1/              # API endpoints
│   │   ├── launches.py      # Launch CRUD
│   │   ├── stations.py      # Station management
│   │   ├── rules.py         # Rule management
│   │   └── decisions.py     # Decision endpoints
│   ├── core/                # Core utilities
│   │   ├── config.py        # Settings (env vars)
│   │   └── logging.py       # Logging setup
│   ├── db/                  # Database layer
│   │   ├── sqlite/          # SQLite (metadata)
│   │   └── influx/          # InfluxDB (time-series)
│   ├── models/              # Pydantic models
│   └── main.py              # FastAPI app
├── collectors/              # Data collection
│   ├── base.py             # Base collector
│   └── sources/            # Collector implementations
│       ├── meteoswiss.py   # MeteoSwiss (done)
│       ├── holfuy.py       # Holfuy (done)
│       ├── slf.py          # SLF (todo)
│       └── windline.py     # Windline (todo)
├── rules/                   # Rule engine (todo)
├── integrations/           # Alerts (todo)
├── .env                    # Your config (don't commit!)
├── docker-compose.yml      # Docker orchestration
└── main.py                 # Entry point
```

## 🔧 Configuration (.env)

Key settings in `.env` file:

```bash
# Application
DEBUG=True                   # Enable debug mode
PORT=8000                    # API port

# InfluxDB (your homelab)
INFLUXDB_URL=http://your-influx-host:8086
INFLUXDB_TOKEN=your-token-here
INFLUXDB_ORG=lenticularis
INFLUXDB_BUCKET=weather_data

# Weather APIs
HOLFUY_API_KEY=              # If you have one

# Alerts (future)
DISCORD_WEBHOOK_URL=         # For Discord notifications
SIGNAL_PHONE_NUMBER=         # For Signal alerts
TELEGRAM_BOT_TOKEN=          # For Telegram bot
```

## 🐛 Troubleshooting

### "Module not found" errors
```bash
# Make sure you're in the right directory
cd c:\git\Lenticularis

# Activate virtual environment
venv\Scripts\activate

# Reinstall dependencies
pip install -r requirements.txt
```

### InfluxDB connection issues
```bash
# Check your .env file
# Ensure INFLUXDB_URL is accessible from your machine
# Verify INFLUXDB_TOKEN is correct

# Test connection
python -c "from app.db.influx.connection import influx; print(influx.client.ping())"
```

### Database initialization fails
```bash
# Remove existing database
Remove-Item data\lenticularis.db

# Reinitialize
python init_db.py
```

### Docker container won't start
```bash
# Check logs
docker-compose logs lenticularis

# Ensure .env file exists
# Ensure ports aren't in use
netstat -ano | findstr :8000
```

## 📊 Data Models

### Launch Site
- name, location
- latitude, longitude, elevation
- preferred_wind_directions
- webcam_urls

### Weather Station
- station_id, source
- name, location
- active status

### Rule
- launch_id
- rule_type (wind_speed, wind_direction, etc.)
- operator (>, <, between, etc.)
- threshold_value
- severity (green, orange, red)

### Weather Data (InfluxDB)
- station_id, source, timestamp
- wind_speed, wind_direction, gust_speed
- temperature, humidity, pressure

### Decision (InfluxDB)
- launch_id, timestamp
- status (green/orange/red)
- contributing_factors
- message

## 🎯 Example: Create Your First Launch

```bash
# 1. Start the application
python main.py

# 2. Open another terminal and use curl or Postman
curl -X POST http://localhost:8000/api/v1/launches/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Beatenberg",
    "location": "Interlaken",
    "latitude": 46.6973,
    "longitude": 7.7909,
    "elevation": 1200,
    "preferred_wind_directions": "NW,W",
    "active": true
  }'

# 3. View in browser
# http://localhost:8000/docs
```

## 📈 Next Development Steps

1. **Implement Rule Engine** - `rules/engine.py`
2. **Add Scheduler** - Continuous data collection
3. **Build Web UI** - User-friendly interface
4. **Add Alerts** - Signal/Discord integration
5. **Analytics** - Historical data analysis

## 🆘 Need Help?

- Check [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design
- Check [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for progress
- Check [GETTING_STARTED.md](GETTING_STARTED.md) for setup
- Check API docs at `/docs` when running

---

**Happy paragliding! 🪂**
