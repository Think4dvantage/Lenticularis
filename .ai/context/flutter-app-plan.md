# Plan: Lenticularis Flutter App (v1 — read-only)

## Context

Lenticularis is a paragliding weather decision-support system with a well-established FastAPI backend and vanilla JS web frontend. The goal is to add a native mobile app (Android first, iOS later via CI) that gives pilots read-only access to the same data — map with station markers + replay, ruleset traffic lights, station detail charts, and the föhn dashboard. No rule editing, no admin functions.

The app will live in `/app` inside this repo, ship as `cloud.lenti.app`, and target `https://lenti.cloud` (prod) and `https://dev.lenti.cloud` (dev). Username/password auth for v1; Google OAuth deferred.

---

## Prerequisite: dev.lenti.cloud routing

**File**: `docker-compose.dev.yml`

Add two new Traefik router labels (alongside the existing `lenti-dev.lg4.ch` labels, keeping those as aliases):

```
- "traefik.http.routers.lenticularis-dev-public.rule=Host(`dev.lenti.cloud`)"
- "traefik.http.routers.lenticularis-dev-public.entrypoints=websecure"
- "traefik.http.routers.lenticularis-dev-public.tls.certresolver=letsencrypt"
- "traefik.http.routers.lenticularis-dev-public.service=lenticularis-dev"
```

DNS: add an A (or CNAME) record `dev.lenti.cloud` → lg4 host public IP in your DNS provider. Let's Encrypt will handle the cert automatically via the existing certresolver.

---

## Flutter project

### Location & identity
- **Path**: `/app` (subfolder of this repo)
- **Package ID**: `cloud.lenti.app`
- **Display name**: Lenticularis
- **Min Android SDK**: 21 (Android 5+, covers >99% of active devices)
- **Flutter SDK**: stable channel

### Packages

| Package | Version | Purpose |
|---|---|---|
| `flutter_map` + `latlong2` | latest | Map + markers |
| `flutter_map_tile_caching` | latest | Offline map tile cache |
| `fl_chart` | latest | History + föhn pressure charts |
| `dio` | latest | HTTP client |
| `dio_cache_interceptor` + `dio_cache_interceptor_hive_store` | latest | Offline API response cache |
| `hive_flutter` | latest | Local cache store (fast key-value, no schema) |
| `connectivity_plus` | latest | Detect online/offline state |
| `flutter_secure_storage` | latest | JWT token storage |
| `flutter_riverpod` | latest | State management |
| `go_router` | latest | Navigation |
| `flutter_localizations` + `intl` | SDK bundled | i18n (4 languages) |

---

## Project structure

```
app/
  lib/
    main.dart                  # app entry, theme, router, providers
    core/
      api/
        api_client.dart        # Dio instance + base URL config
        auth_interceptor.dart  # Bearer token + 401 → refresh → retry
        endpoints.dart         # URL constants
      auth/
        auth_repository.dart   # login(), refresh(), logout()
        auth_state.dart        # Riverpod auth notifier
        token_storage.dart     # flutter_secure_storage wrapper
      offline/
        cache_store.dart       # Hive-backed dio_cache_interceptor store init
        connectivity_notifier.dart  # Riverpod stream from connectivity_plus
        offline_banner.dart    # "Offline — showing data from X" widget
      i18n/                    # *.arb files ported from static/i18n/*.json
      theme/
        app_theme.dart         # dark theme, color tokens
        colors.dart            # kGreen, kOrange, kRed, surface, etc.
      router/
        router.dart            # go_router: redirect on auth state
    features/
      auth/
        login_screen.dart
      map/
        map_screen.dart        # flutter_map + markers + bottom replay bar
        replay_controller.dart # frame array, slider state, play/pause
        station_marker.dart    # colored marker widget
      rulesets/
        rulesets_screen.dart   # list of traffic light cards
        ruleset_detail_screen.dart  # 24h decision history bar chart
      stations/
        station_detail_screen.dart  # latest measurements + history chart
      foehn/
        foehn_screen.dart      # per-region cards + pressure chart
```

---

## Screens

### 1. Login
- Username + password fields, "Sign in" button
- `POST /auth/login` → store `access_token` + `refresh_token`
- On success → redirect to Map

### 2. Map + Replay
- `flutter_map` with OpenStreetMap tile layer (no API key needed)
- Station markers: color = latest ruleset decision for that station's site; fall back to wind speed threshold coloring if no ruleset
- On marker tap → bottom sheet with station name + latest wind/temp → "View detail" nav
- **Replay bar** (persistent bottom panel):
  - Day buttons: Today ± 4 (same 9 offsets as web)
  - Hour slider (0–23 or 0–48 for forecast)
  - Play/pause button
  - Calls `GET /api/stations/replay?start=&end=&forecast_hours=&include_forecast=`
  - Stores frame array in `ReplayController`; slider position drives frame index
  - Client-side cache (10 min TTL) matching web behaviour — no redundant API calls on slider scrub

### 3. My Rulesets
- `GET /api/rulesets` → list of `RulesetCard` widgets
- Each card: site name, GREEN/ORANGE/RED traffic light badge, last evaluated timestamp
- Tap → Ruleset Detail: `GET /api/rulesets/{id}/history?hours=24` → bar chart of decision history

### 4. Station Detail
- Header: station name, network badge, elevation
- Measurement grid: wind speed, gust, direction, temp, humidity, pressure
- `GET /api/stations/{id}/history` → `fl_chart` line chart
- Tab selector: Wind / Temperature / Humidity / Pressure

### 5. Föhn Dashboard
- `GET /api/foehn/status` → per-region status cards (active/partial/inactive) with color badge
- `GET /api/foehn/history` → pressure gradient line chart (one line per station pair + ±4 hPa threshold lines)

---

## Navigation (go_router)

```
/login              → LoginScreen (unauthenticated redirect)
/map                → MapScreen (default after auth)
/rulesets           → RulesetsScreen
/rulesets/:id       → RulesetDetailScreen
/stations/:id       → StationDetailScreen
/foehn              → FoehnScreen
```

Bottom nav bar: Map · Rulesets · Föhn (3 tabs).

---

## Offline strategy

Pilots use this app mid-hike and mid-flight — connectivity is unreliable. The app must degrade gracefully, never show a blank screen, and always communicate data freshness.

### API cache (`dio_cache_interceptor` + Hive)

Every GET request is cached to disk with a `maxStale` duration appropriate to the data type:

| Endpoint | Cache TTL | Max stale |
|---|---|---|
| `GET /api/stations` (list) | 24 h | 7 days |
| `GET /api/stations/{id}/latest` | 5 min | 2 h |
| `GET /api/stations/{id}/history` | 10 min | 6 h |
| `GET /api/rulesets` | 5 min | 2 h |
| `GET /api/rulesets/{id}/history` | 10 min | 6 h |
| `GET /api/stations/replay` | 5 min | 2 h |
| `GET /api/foehn/status` | 5 min | 2 h |
| `GET /api/foehn/history` | 10 min | 6 h |

Cache policy: `CachePolicy.requestFirst` when online (fresh data preferred, fall back to cache on error); `CachePolicy.forceCache` when `connectivity_plus` reports no connection.

### Map tiles (`flutter_map_tile_caching`)

- Pre-cache the Swiss bounding box at zoom levels 8–13 on first launch (WiFi only prompt)
- Tiles served from local cache when offline
- Switzerland at z8–13 ≈ ~50 MB — acceptable

### Connectivity banner

`connectivity_notifier.dart` exposes a `StreamProvider` from `connectivity_plus`. When offline:
- A non-dismissable amber banner appears at the top of every screen: **"Offline — showing data from [timestamp]"**
- Refresh buttons are hidden (not greyed) — no point offering an action that will fail
- Cached data renders normally; no error screens unless cache is also empty (first-ever launch with no connection)

### Empty cache + offline

If a screen has never loaded data and the device is offline: show a friendly message ("No cached data yet — connect once to load your sites") rather than a loading spinner that never resolves.

### Auth offline

Tokens are stored in `flutter_secure_storage` (persists across restarts). If the app restarts offline, skip the `/health` check and proceed with the cached token optimistically. The 401 interceptor only redirects to login if it gets an actual 401 response — a network error is treated as offline, not as an auth failure.

---

## Auth flow

1. App starts → check `flutter_secure_storage` for tokens
2. No token → redirect to `/login`
3. Token present → optimistic trust; catch 401 at request time
4. Dio interceptor: on 401, attempt `POST /auth/refresh` → retry original request → on refresh failure, clear tokens + redirect to `/login`

---

## i18n

- Port `static/i18n/{en,de,fr,it}.json` → `app/lib/core/i18n/app_{en,de,fr,it}.arb`
- Only port keys relevant to mobile screens (skip web-only keys like ruleset editor labels)
- `flutter_localizations` + `intl` package — standard Flutter i18n
- Language follows device locale; in-app override saved to `SharedPreferences`

---

## Theme

Dark theme throughout, matching the web app:
- `kGreen`: `#4CAF50`
- `kOrange`: `#FF9800`
- `kRed`: `#F44336`
- Background: `#121212`, surface: `#1E1E1E`, card: `#2C2C2C`
- Primary accent: `#90CAF9` (light blue, matching web nav)

---

## API base URL

Configurable via a compile-time `--dart-define` flag:

```
flutter run --dart-define=API_BASE_URL=https://dev.lenti.cloud
flutter build apk --dart-define=API_BASE_URL=https://lenti.cloud
```

Default (no flag): `https://lenti.cloud`.

---

## Implementation order (session)

1. `docker-compose.dev.yml` — add `dev.lenti.cloud` Traefik labels
2. `flutter create` in `/app` with package ID + display name
3. Add all dependencies to `pubspec.yaml`
4. Core: theme → colors → offline cache store (Hive init) → connectivity notifier → API client with cache interceptor → auth repository → router
5. Login screen + auth flow (offline-safe token handling)
6. Offline banner widget (reused on every screen)
7. Map screen — basic markers (no replay)
8. Map tile pre-caching setup
9. Replay bar + `ReplayController`
10. My Rulesets list + detail
11. Station detail + history chart
12. Föhn dashboard + pressure chart
13. i18n wiring (ARB files + locale switching)
14. Final polish: empty-cache states, stale data timestamps

---

## Verification

- Run on Android emulator: `flutter run` (from `/app`)
- Auth: login with pilot credentials against `dev.lenti.cloud`
- Map: confirm station markers appear, tap opens bottom sheet
- Replay: drag slider, confirm markers update color per frame
- Rulesets: confirm traffic light badges match web dashboard
- Föhn: confirm region cards match `/foehn` web page
- i18n: switch device language to DE/FR/IT, confirm labels update
- Offline: enable airplane mode → confirm banner appears, cached data still visible, no spinner loops
- Offline cold start: clear app data → airplane mode → reopen → confirm "no cached data" message shown gracefully
