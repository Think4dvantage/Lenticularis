# Lenticularis

Paragliding weather decision-support system for Switzerland. Collects data from multiple weather networks, stores it in InfluxDB, and lets each pilot build graphical rule sets that evaluate weather conditions across multiple stations to produce GREEN / ORANGE / RED traffic-light decisions per launch site.

## Features

- **Multi-source weather collection** — MeteoSwiss (10 min), SLF (30 min), METAR/AviationWeather (15 min), Wunderground/Ecowitt personal stations, Holfuy (5 min), Windline, FGA/Meteo Oberwallis (Valais), Jungfraubahn (Jungfrau region, 10 min)
- **ICON-CH ensemble forecast** — 120 h station forecasts from SwissMeteo (`lsmfapi`), with probable + ensemble min/max spread per field; Open-Meteo retained as fallback
- **Wind forecast grid map** — ICON-CH1 wind at 8 altitude levels (500–5000 m) on an interactive grid overlay with time navigation; cloud icons where humidity ≥ 90 %
- **Forecast accuracy analysis** — per-station/field MAE + bias ranking over 90 days, D+1/D+2/D+3 lead-time buckets, worst-forecast station tables
- **Station deduplication** — union-find over co-located stations (50 m GPS + manual override pairs); one canonical station per physical site, chosen by network priority
- **Interactive map** — Leaflet.js with station markers, launch-site traffic lights, time-navigation replay (past + 5-day forecast), personal-station toggle
- **Rule editor** — graphical condition builder: per-row station picker, field / operator / value, AND/OR nesting, direction compass, pressure-delta two-station mode, live preview
- **Traffic light evaluation** — live + 120 h forecast evaluation; per-condition decision history with `ruleset-analysis.html`
- **Flyability statistics** — hourly heatmap, monthly/seasonal breakdown, condition trigger leaderboard, site comparison, best consecutive-GREEN windows
- **Föhn monitor** — virtual föhn stations computed from N–S pressure deltas; dedicated `foehn.html` dashboard with live/historical/forecast modes and editable thresholds
- **Webcam links** — attach webcam URLs (incl. Roundshot with bearing) to any ruleset; shown as cards in the analysis view with a Roundshot badge
- **Preset launch sites** — admin-curated ruleset templates; pilots pick a preset when creating a new site and customise thresholds freely; admin panel "Preset Sites" tab to manage
- **Forecast accuracy dashboard** — compare past forecasts against observed values per station and field; overlaid Chart.js lines (actual + per-init_date model runs); accessible from station-detail via "📊 Accuracy" button
- **Multi-tenant org system** — `Organisation` model with `org_admin` / `org_pilot` roles; each org gets a subdomain (`vkpi.lenti.cloud`); public traffic-light dashboard + authenticated condition breakdown + 24h history strip; org-scoped ruleset editor (personal and org rules fully isolated)
- **AI rule suggestions** — natural-language condition input powered by local Ollama; backend pre-processing pipeline resolves Swiss German wind terminology (Südkomponente, Windböen, etc.) to explicit degrees/units before the LLM call; fuzzy station name matching; geographic station lookup by location name (50+ Swiss sites); multilanguage (DE/FR/IT/EN)
- **Help / FAQ** — `/help` page with 12 accordion sections and anchor deep-links; contextual `?` tooltip buttons on rule editor, Föhn page, and stats page
- **Admin panel** — user management (roles: pilot / customer / admin / org_admin / org_pilot), organisation management (create org, assign users), collector status and runtime control, Föhn config editor, preset site management
- **Multilanguage UI** — EN / DE / FR / IT; auto-detected from browser, switchable from nav, persisted to `localStorage`
- **Auth** — JWT register/login + Google OAuth; pilot-owned sites and rule sets; admin role for user/collector management; `org_id` embedded in JWT for org-scoped access
- **Email alerts** — per-ruleset notification on traffic-light transitions (`notify_on` colours)
- **Zero external dependencies at runtime** — Leaflet and Chart.js are self-hosted under `static/vendor/`; no CDN, no npm, no build step. Static assets are served immutable and cache-busted by app version
- **Docker deployment** — single `docker-compose up -d` deploys app + InfluxDB; dev overlay with live volume mounts; Traefik multi-router label pattern for org subdomains; images published to `ghcr.io` on every `v*` tag

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry |
| Time-series DB | InfluxDB 2.x |
| Relational DB | SQLite via SQLAlchemy |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose`, passwords via `bcrypt`, Google OAuth |
| Config | YAML (`config.yml`) validated by Pydantic |
| Frontend | Vanilla JS + Leaflet.js + Chart.js — self-hosted, no build step |
| Testing | pytest + pytest-asyncio (`tests/backend/`) |
| CI/CD | GitHub Actions — pytest on every push; Docker image published to `ghcr.io` on `v*` tags |
| Container | Docker + docker-compose |

## Getting Started

### Prerequisites

- Python 3.11+
- Poetry (`pip install poetry`)
- Docker + docker-compose

### Quick start (Docker)

```bash
git clone https://github.com/Think4dvantage/Lenticularis.git
cd lenticularis
cp config.yml.example config.yml
# Edit config.yml — set InfluxDB token, collector API keys, JWT secret
docker-compose up -d
```

- Web interface: http://localhost:8000
- API docs: http://localhost:8000/docs

### Development overlay

```bash
# Mounts src/ and static/ live; exposes via lenti-dev.lg4.ch behind Traefik
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

## Project Structure

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan; security headers, GZip, CSP
│   ├── dependencies.py      # get_current_user, require_pilot, require_admin, require_org_*
│   ├── errors.py            # AppException + _envelope() — {"error":{code,message,details}}
│   └── routers/             # auth, stations, rulesets, stats, foehn, org, ai,
│                            #   wind_forecast, admin, health, pages,
│                            #   public (the only unauthenticated API surface)
├── collectors/              # One file per network: meteoswiss, slf, metar, holfuy,
│                            #   windline, ecowitt, wunderground, fga, jfb, foehn,
│                            #   forecast_swissmeteo, forecast_grid_swissmeteo, …
│   ├── base.py              # BaseCollector ABC + bounded-concurrency helper
│   └── utils.py             # Shared to_float() / normalize_wind_dir()
├── database/
│   ├── models.py            # SQLAlchemy ORM
│   ├── db.py                # init_db(), get_db(), column migrations, WAL pragmas
│   └── influx.py            # InfluxDB client (write + all query methods)
├── models/                  # Pydantic schemas (weather, auth, rules)
├── rules/evaluator.py       # Live + forecast rule evaluation engine
├── services/                # Auth helpers, weather stats, station dedup,
│                            #   public_map (batched + cached public map payload)
├── config.py                # YAML config loader (singleton)
├── scheduler.py             # APScheduler: collector + forecast + föhn jobs
└── foehn_detection.py       # Föhn region definitions + pressure logic
static/
├── index.html + map.js      # Map dashboard + station/ruleset markers
├── rulesets.html            # Rule set cards + forecast strip (supports ?org= mode)
├── ruleset-editor.html      # Condition builder (supports ?org= mode)
├── ruleset-analysis.html    # Per-condition analysis (history + forecast)
├── org-dashboard.html       # Public traffic-light + authenticated org detail view
├── wind-forecast.html       # ICON-CH1 wind grid map (8 altitude levels)
├── forecast-accuracy.html   # Per-station forecast vs. actual
├── forecast-analysis.html   # Worst-forecast station ranking (MAE + bias)
├── stats.html               # Flyability statistics dashboard
├── foehn.html               # Föhn monitor
├── admin.html               # Admin panel (users, orgs, collectors, föhn config)
├── stations.html + station-detail.html
├── bootstrap.js             # renderNav() + bootstrapPage() — shared page bootstrap
├── i18n.js + i18n/          # Translation engine + EN/DE/FR/IT JSON files
├── shared.css               # Nav CSS + mobile-responsive overrides
├── vendor/                  # Self-hosted Leaflet + Chart.js (no CDN, no npm)
└── auth.js + login.html + register.html
tests/backend/               # pytest suite (auth, rules, dedup, security, collectors)
```

## Development Status

**Current version: v1.20.0** — published as `ghcr.io/Think4dvantage/Lenticularis:1.20.0` (and `:latest`).

| Milestone | Status |
|---|---|
| v0.1 — Station detail page + MeteoSwiss collector | ✅ Shipped |
| v0.2 — Live Leaflet map | ✅ Shipped |
| v0.3 — JWT auth + admin | ✅ Shipped |
| v0.4 — Launch sites (pilot-owned, on map) | ✅ Shipped |
| v0.5 — SLF + METAR collectors | ✅ Shipped |
| v0.6 — Rule editor (condition builder) | ✅ Shipped |
| v0.7 — Rules evaluator + traffic lights + forecast evaluation | ✅ Shipped |
| v0.8 — Forecast pipeline + map time-navigation replay | ✅ Shipped |
| v0.9 — Flyability statistics dashboard | ✅ Shipped |
| v0.10 — Föhn monitor + Wunderground + personal-station toggle | ✅ Shipped |
| v1.0 — Multilanguage EN/DE/FR/IT + mobile-responsive UI | ✅ Shipped |
| v1.1 — Admin panel + customer role + Föhn config editor | ✅ Shipped |
| v1.2 — Webcam links + preset launch sites + map fixes | ✅ Shipped |
| v1.3 — Forecast accuracy dashboard | ✅ Shipped |
| v1.4 — Opportunity site type + AI rule suggestions (Ollama) | ✅ Shipped |
| v1.5 — Multi-tenant org system (VKPI) | ✅ Shipped |
| v1.6 — Help/FAQ page + AI input improvements + org ruleset isolation fix | ✅ Shipped |
| v1.7 — Holfuy collector + forecast replay prefetch cache + map arrow fix | ✅ Shipped |
| v1.8 — Replay performance: server cache, startup warm-up, 30m downsampling, all-button prefetch | ✅ Shipped |
| v1.9 — Virtual station deduplication (union-find, 50 m GPS, manual overrides) | ✅ Shipped |
| v1.10 — Replay cache correctness: post-collection invalidation + rewarm | ✅ Shipped |
| v1.11 — Google OAuth login | ✅ Shipped |
| v1.12 — Rules engine improvements, backtester, 30-day backfill, email notifications | ✅ Shipped |
| v1.13 — Föhn Tracker rework: delta/trend conditions, per-user config | ✅ Shipped |
| v1.14 — Ruleset gallery, FGA collector, wind forecast grid map | ✅ Shipped |
| v1.15 — SwissMeteo (`lsmfapi`) ICON-CH ensemble forecast + band charts | ✅ Shipped |
| v1.16 — lsmfapi full-stack fix: grid collector, dual Influx clients, 2-step replay query | ✅ Shipped |
| v1.17 — Parallel forecast collect, chunked grid writes, stats table improvements | ✅ Shipped |
| v1.18 — Security & performance batch, forecast accuracy analysis, Jungfraubahn collector, test harness | ✅ Shipped |
| v1.18.1 — Self-hosted Leaflet/Chart.js, tightened CSP, static-asset caching | ✅ Shipped |
| v1.19.0 — Public rule sets on the map, named condition groups | ✅ Shipped |
| v1.20.0 — Green conditions are requirements (fail-safe launch/landing) | ✅ Shipped |

Remaining work items are tracked as an unordered backlog in [.ai/context/features.md](.ai/context/features.md).

## Testing

```bash
poetry install --with dev
poetry run pytest -q
```

Backend tests live in `tests/backend/` and run on every push via GitHub Actions. They use an
in-memory SQLite database and a stubbed InfluxDB client — no network, no real infrastructure.

## Configuration

See [config.yml.example](config.yml.example) for all options (InfluxDB, collectors, JWT secret, API keys).

## License

MIT License
