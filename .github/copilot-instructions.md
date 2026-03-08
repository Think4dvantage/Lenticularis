# Copilot Instructions — Lenticularis

## What this project is

Lenticularis is a paragliding weather decision-support system for Switzerland. It collects data from 5 Swiss weather networks (MeteoSwiss, Holfuy, SLF, Windline, Ecovitt), stores it in InfluxDB, and lets each pilot build graphical rule sets that evaluate weather conditions across multiple stations to produce GREEN/ORANGE/RED traffic light decisions per launch site. It also computes flyability statistics from historical decision data.

The full product spec is in `plan-lenticularisStructure.prompt.md`. Read it before making significant changes.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Time-series DB | InfluxDB 2.x (`influxdb-client`) |
| Relational DB | SQLite via SQLAlchemy + Alembic |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose`, passwords via `passlib` |
| Email | `aiosmtplib` |
| Config | YAML (`config.yml`) validated by Pydantic |
| Frontend | Vanilla JS + Leaflet.js + Chart.js |
| Container | Docker + docker-compose |

---

## Project Structure

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app, startup/shutdown lifecycle
│   └── routers/             # One file per router (auth, stations, rulesets, stats, etc.)
├── collectors/
│   ├── base.py              # Abstract BaseCollector
│   ├── meteoswiss.py
│   ├── holfuy.py
│   ├── slf.py
│   ├── windline.py
│   └── ecovitt.py
├── database/
│   ├── models.py            # SQLAlchemy ORM models (all 7 tables)
│   └── influx.py            # InfluxDB client (write + query)
├── models/
│   ├── weather.py           # WeatherStation, WeatherMeasurement
│   ├── auth.py              # User, UserCreate, Token
│   ├── sites.py             # LaunchSite, LaunchSiteCreate
│   ├── rules.py             # RuleSet, Condition, ConditionGroup, TrafficLightDecision
│   ├── decisions.py         # DecisionRecord, ConditionResult
│   └── stats.py             # Response shapes for all statistics endpoints
├── rules/
│   └── evaluator.py         # Walks condition tree, fetches live data, resolves decision
├── services/
│   ├── stats.py             # Flux queries + best-windows algorithm
│   └── notifications.py     # Email + Pushover dispatch on status transitions
├── config.py                # YAML loader + Pydantic config models
└── scheduler.py             # APScheduler wiring all collectors + ruleset evaluations
static/
├── app.js                   # Map dashboard (Leaflet.js)
├── editor.js                # Rule editor condition builder
└── stats.js                 # Statistics dashboard (Chart.js)
```

---

## Coding Conventions

- **Always use type hints** on function signatures and class attributes
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries
- **Pydantic v2** for all data schemas and config validation
- **SQLAlchemy 2.0 style** (use `select()` not legacy `query()`)
- **FastAPI dependency injection** for auth (`get_current_user`, `require_admin`) and DB sessions
- **One router per domain** — never put all routes in `main.py`
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors
- **Never hardcode** config values — always read from `config.yml` or environment variables
- **Log** all collection events, rule evaluations, and errors using the standard `logging` module
- **No print statements** in production code

---

## Rules Engine — Critical Design

The rules evaluator (`rules/evaluator.py`) must:

1. Load the rule set's condition tree from SQLite (`rule_conditions` + `condition_groups`)
2. For each condition, fetch the **latest measurement** from InfluxDB for `station_id` (and `station_b_id` for `pressure_delta`)
3. Apply the operator/value logic to produce a per-condition `result_colour`
4. Walk AND/OR group nesting to combine condition results within groups
5. Apply the ruleset's `combination_logic` (`worst_wins` or `majority_vote`) across top-level results
6. Return a `TrafficLightDecision` including a `condition_results` array: `[{condition_id, station_id, field, actual_value, result_colour}]`
7. Write the full decision (including `condition_results` JSON) to the `rule_decisions` InfluxDB measurement

The station picker is **per condition row** — a single rule set can reference any number of different stations. This is intentional and central to the product.

---

## Statistics — Critical Design

All statistics are derived from the `rule_decisions` InfluxDB measurement, not from raw `weather_data`. This is intentional — the `condition_results` field already contains per-condition, per-station actual values and result colours.

Statistics service (`services/stats.py`) implements 7 metrics via Flux queries:
- Flyable days, hourly pattern, monthly breakdown, seasonal breakdown, condition trigger rate, site comparison, best windows
- Best windows (longest consecutive GREEN streaks) is computed server-side after fetching the decision time series

---

## Authentication

- JWT bearer tokens (access + refresh)
- `get_current_user` FastAPI dependency — required on all user-facing endpoints
- `require_admin` dependency — required on all `/api/admin/*` endpoints
- Pilots can only read/write their own launch sites, rulesets, and notification configs (enforce `owner_id == current_user.id`)

---

## InfluxDB Measurements

### `weather_data`
- Tags: `station_id`, `network`, `canton`
- Fields: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure`, `precipitation`, `snow_depth`

### `rule_decisions`
- Tags: `launch_site_id`, `ruleset_id`, `owner_id`
- Fields: `decision` (string: green/orange/red), `condition_results` (JSON string), `blocking_conditions` (JSON string)

---

## What the Admin role does NOT do

Admins do **not** manage launch sites, rule sets, or station-to-site assignments. Those are fully pilot-owned and self-served. Admin scope is limited to:
- Enabling/disabling collectors and changing their collection intervals
- Managing user accounts (roles, deactivation)

---

## Community Rule Gallery

- Rule sets are private by default (`is_public = false`)
- A pilot can publish a rule set (`is_public = true`) so it appears in the gallery
- Other pilots can **clone** a public rule set into their own account (`cloned_from_id` is set, `clone_count` increments on the original)
- Clones are independent — editing a clone does not affect the original

---

## Reference Implementation — winds-mobi

**Before implementing or debugging any collector, check the winds-mobi providers repo first:**

> **https://github.com/winds-mobi/winds-mobi-providers**
>
> Each file under `providers/` is a battle-tested production collector for the same networks we use
> (MeteoSwiss, Holfuy, SLF, Windline, and others). API field names, URL patterns, authentication
> quirks, and data-shape edge cases are already solved there.

Key lessons already learned from that repo:

- **MeteoSwiss `wind_direction`** is NOT a separate GeoJSON endpoint. It is embedded as
  `properties["wind_direction"]` inside every feature of the `wind_speed` response. There is no
  `windrichtung-10min` endpoint — requesting it returns 404.
- **MeteoSwiss pressure** has three distinct endpoints: `qff`, `qfe`, and `qnh`. The plain
  `luftdruck-10min` slug (without a variant suffix) does not exist.
- **MeteoSwiss timestamps** — use `reference_ts` (ISO 8601) as the primary field; fall back to
  `date` only if absent.
- **Altitude/elevation strings** can arrive as floats (`'1888.00'`) or with a unit suffix
  (`'1538.86 m'`). Always parse via `int(float(str(raw).split()[0]))`.

---

## Deployment Notes (homelab)

Target: Fedora host `172.18.10.50`, deployed via `scripts/remote.ps1`.

### Traefik label format

This homelab's Traefik instance requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"

# WRONG — Traefik silently ignores map-format labels here
labels:
  traefik.enable: "true"
```

When a container is on **multiple Docker networks**, you must also add:
```yaml
  - "traefik.docker.network=proxy"
```
otherwise Traefik picks the wrong network IP and the upstream connection fails.

### Healthcheck

`python:3.11-slim` does **not** include `curl`. Use Python's stdlib instead:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

Traefik's Docker provider explicitly filters out containers whose health status is `unhealthy` or
`starting` — a broken healthcheck means the route never appears in the Traefik API, regardless of
how correct the labels are.

### Dev overlay

`docker-compose.dev.yml` extends the base compose file with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lenti-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files baked into the image from
  shadowing the volume-mounted `.py` sources