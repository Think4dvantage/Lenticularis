# Architecture Reference

## SQLite Tables

| Table | Key columns |
|---|---|
| `organizations` | `id`, `slug` (unique), `name`, `description`, `created_at` |
| `users` | `id`, `username`, `email`, `hashed_password`, `role`, `org_id` FK → organizations, `created_at` |
| `weather_stations` | `station_id`, `name`, `network`, `latitude`, `longitude`, `elevation`, `canton`, `active` |
| `launch_sites` | `id`, `name`, `latitude`, `longitude`, `owner_id` FK → users |
| `rulesets` | `id`, `name`, `description`, `launch_site_id`, `owner_id`, `org_id` FK → organizations, `site_type` (launch/landing/opportunity), `combination_logic`, `is_public`, `is_preset`, `clone_count`, `cloned_from_id`, `created_at`, `updated_at` |
| `rule_conditions` | `id`, `ruleset_id`, `group_id` (nullable), `station_id`, `station_b_id` (nullable), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order` |
| `condition_groups` | `id`, `ruleset_id`, `parent_group_id` (nullable), `logic` (AND/OR), `sort_order` |
| `ruleset_webcams` | `id`, `ruleset_id`, `url`, `label`, `sort_order` |
| `notification_configs` | `id`, `user_id`, `launch_site_id`, `channel`, `config_json`, `on_transitions_json` |

---

## InfluxDB Measurements

### `weather_data`
- **Tags**: `station_id`, `network`, `canton`
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`, `pressure_qnh`, `pressure_qff`, `precipitation`, `snow_depth`

### `weather_forecast`
- **Tags**: `station_id`, `network`, `model` (`icon-ch1` / `icon-ch2` / `open-meteo`), `init_date` (YYYY-MM-DD for same-day dedup)
- **Timestamp**: `valid_time` (the future moment the forecast is valid for)
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qnh`, `precipitation`
- Kept indefinitely — enables forecast-vs-actual accuracy analysis.

### `rule_decisions`
- **Tags**: `launch_site_id`, `ruleset_id`, `owner_id`, `site_type`
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array), `blocking_conditions` (JSON array of condition IDs)
- Storing `condition_results` per evaluation enables per-condition and per-station trigger statistics without re-querying raw weather data.

---

## API Contracts

### Auth
- `POST /auth/register` — `{username, email, password}` → `{user_id, token}`
- `POST /auth/login` — `{username, password}` → `{access_token, refresh_token}`
- `POST /auth/refresh` — `{refresh_token}` → `{access_token}`

### Stations
- `GET /api/stations` — list all active stations (`?network=&canton=`)
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history` — `?from=&to=&fields=`
- `GET /api/stations/{id}/forecast-accuracy` — `?from=&to=` — actuals + per-init_date forecast series

### Launch Sites
- `GET/POST /api/launch-sites`
- `GET/PUT/DELETE /api/launch-sites/{id}`

### Rule Sets
- `GET /api/rulesets` — user's own, `org_id IS NULL` (personal only)
- `POST /api/rulesets` — create with full condition tree; pass `org_slug` to scope to org
- `GET /api/rulesets/{id}` — full rule set including condition tree
- `PUT /api/rulesets/{id}` — replace full condition tree (editor save)
- `DELETE /api/rulesets/{id}`
- `POST /api/rulesets/{id}/evaluate` — evaluate NOW, return decision + per-condition reasoning
- `GET /api/rulesets/{id}/history` — `?hours=N` — from `rule_decisions` InfluxDB
- `GET /api/rulesets/{id}/forecast` — `?hours=N` — declared BEFORE `/{ruleset_id}` to avoid FastAPI route shadowing
- `PUT /api/rulesets/{id}/webcams` — full-replace webcam list
- `PUT /api/rulesets/{id}/set_preset` — admin-only (`?is_preset=bool`)
- `GET /api/rulesets/presets` — all preset rulesets (any pilot)
- `POST /api/rulesets/{id}/publish`, `unpublish`

### Org
- `GET /api/org/{slug}/status` — public traffic light
- `GET /api/org/{slug}/dashboard` — org member: condition breakdown + 24h history
- `GET /api/org/{slug}/rulesets` — org admin

### Gallery
- `GET /api/gallery` — public rule sets (`?q=&sort=clone_count`)
- `POST /api/gallery/{id}/clone`

### Statistics
- `GET /api/stats/{ruleset_id}/flyable-days`
- `GET /api/stats/{ruleset_id}/hourly-pattern`
- `GET /api/stats/{ruleset_id}/monthly`
- `GET /api/stats/{ruleset_id}/seasonal`
- `GET /api/stats/{ruleset_id}/condition-triggers`
- `GET /api/stats/compare?ruleset_ids=1,2,3`
- `GET /api/stats/{ruleset_id}/best-windows`

All time-range endpoints accept `?from=&to=`. Best-windows also accepts `?top_n=5`.

### Admin (require_admin)
- `GET/PUT /api/admin/users`
- `GET/PUT /api/admin/collectors`
- `GET/PUT/DELETE /api/admin/foehn-config`
- `GET/POST /api/admin/orgs`

### AI
- `POST /api/ai/suggest-conditions` — Ollama-powered natural-language → condition JSON

### System
- `GET /health`
- `GET /docs` (FastAPI auto-generated Swagger UI)

---

## Rules Engine Design

`rules/evaluator.py`:

1. Load the rule set's condition tree from SQLite (`rule_conditions` + `condition_groups`)
2. For each condition, fetch the **latest measurement** from InfluxDB for `station_id` (and `station_b_id` for `pressure_delta`)
3. Apply operator/value logic to produce a per-condition `result_colour`
4. Walk AND/OR group nesting to combine results within groups
5. Apply `combination_logic` (`worst_wins` or `majority_vote`) across top-level results
6. Return `TrafficLightDecision` including `condition_results` array
7. Write full decision (including `condition_results` JSON) to `rule_decisions` InfluxDB

Station picker is **per condition row** — one rule set can reference any number of different stations. Pressure delta is a first-class condition type with two-station picker.

### Forecast Evaluation

`run_forecast_evaluation(ruleset, influx, horizon_hours=120)` reuses identical logic but iterates over hourly `valid_time` steps from `weather_forecast` instead of latest `weather_data`. Returns `list[ForecastStep]`, does **not** write to InfluxDB.

### Combination Logic

- `worst_wins` (default) — any RED → RED; any ORANGE → ORANGE; else GREEN
- `majority_vote` — most common colour wins

---

## Statistics Design

All metrics computed from `rule_decisions` InfluxDB measurement (not raw `weather_data`).

| Metric | Endpoint |
|---|---|
| Flyable days (≥1 GREEN per calendar day) | `/api/stats/{id}/flyable-days` |
| GREEN % per hour-of-day (0–23) | `/api/stats/{id}/hourly-pattern` |
| GREEN/ORANGE/RED counts per calendar month | `/api/stats/{id}/monthly` |
| Same grouped by meteorological season | `/api/stats/{id}/seasonal` |
| % evaluations where each condition voted non-GREEN | `/api/stats/{id}/condition-triggers` |
| Flyable days side-by-side for ≥2 rulesets | `/api/stats/compare` |
| Top N longest consecutive GREEN streaks | `/api/stats/{id}/best-windows` |

Best-windows is computed server-side (not Flux) for simplicity.

---

## Forecast Accuracy

`query_forecast_accuracy()` in `influx.py` fetches actuals + per-init_date forecast series for a station/window. Handles legacy data (no `init_date` tag) as fallback series.

Frontend: station picker + date picker + per-field Chart.js charts with actual (solid) + one overlaid line per model-run day.

---

## Deployment

### Traefik Label Format

This homelab requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"
```

When a container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay

`docker-compose.dev.yml` extends the base with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lenti-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files from shadowing volume-mounted sources
