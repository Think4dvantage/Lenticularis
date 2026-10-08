# Tasks: Public Weather Access for AI Assistants (MCP Server) — spec 010

Package name: `src/lenticularis/mcp_server/` (see plan "Revisions"). Tests are included: the plan makes the
verified-data invariants a release gate. Phase 5 tagging/pushing is **not** done by the AI without being asked.

## Status (2026-10-08)
T001–T032 and T036 done (313 backend tests green; real MCP client verified over uvicorn). Open: T033 (prod
verification, needs approval), T034 (owner: commit/tag/deploy), T035 (archive after release).

## Summary
- Total tasks: 36 · Parallel opportunities: 9
- MVP scope: Phases 1–4 (US1 search + US2 current) behind the verified registry; full P1 = through Phase 6
- Not in scope: rule sets, thermal, aloft wind (backlog)

## Dependencies
```
P1 Setup → P2 Foundation (registry, sanitize, config, mount) → US1 → US2 → US4 → US3 → US5/US6 → US7 → Polish
```

## Phase 1 — Setup
- [x] T001 Phase 0 spike: install `mcp`, prove mount through GZip/BaseHTTPMiddleware, find redirect/host/logging issues (done, see plan Revisions)
- [x] T002 Add `mcp (>=1.30,<2.0)` to `pyproject.toml`; regenerate `poetry.lock` in a scratch copy and copy back; bump version to 1.24.0
- [x] T003 [P] Add `McpConfig` + `MainConfig.mcp` in `src/lenticularis/config.py`; add `mcp:` block to `config.yml.example`

## Phase 2 — Foundation (blocks all stories)
- [x] T004 Create `src/lenticularis/mcp_server/__init__.py` and `sanitize.py` (SUPPRESSED_FIELDS incl. Hollandiahütte temp/humidity/pressure, null stripping, UNITS map, staleness, station-id regex, accent folding)
- [x] T005 Create `src/lenticularis/mcp_server/registry.py` — `McpRegistry` (verified allowlist → private dedup; `rebuild()`, `get()`, `members()`, `search()`)
- [x] T006 Create `src/lenticularis/mcp_server/ratelimit.py` (bounded sliding window, salted caller hash, rightmost-XFF key)
- [x] T007 Create `src/lenticularis/mcp_server/usage.py` (per-tool counters, bounded distinct-caller set, structured log line)
- [x] T008 Create `src/lenticularis/mcp_server/server.py` — `create_mcp(app)` factory: FastMCP per app, logging snapshot/restore, allowed hosts, explicit `/mcp` + `/mcp/` routes, tool wrapper (rate limit, usage, error mapping, no leaks)
- [x] T009 Wire into `src/lenticularis/api/main.py`: mount in `create_app()`, enter `session_manager.run()` in lifespan, build registry at startup, rebuild in `rebuild_display_registry` and `_make_registry_updater`
- [x] T010 [P] Add `FakeInflux.query_history_range` and `query_forecast_for_stations(keep_init_date)` stubs in `tests/backend/conftest.py`
- [x] T011 [P] Tests `tests/backend/test_mcp_registry.py` — verified-only; **dedup-member leak** (MeteoSwiss + Ecowitt cluster); foehn excluded; unverified cluster absent; rebuild
- [x] T012 [P] Tests `tests/backend/test_mcp_ratelimit.py` — window, eviction bound, XFF rightmost, retry-after
- [x] T013 Test `tests/backend/test_mcp_transport.py` — `POST /mcp` 200 no redirect, `/mcp/` 200, bad Host 421, root logging unchanged, lifespan-run integration

## Phase 3 — US1 Find a station (P1)
**Goal**: name/place/radius search over verified stations. **Test**: umlaut query, radius sort, network filter, no private ids.
- [x] T014 [US1] `search_stations` tool in `src/lenticularis/mcp_server/tools.py`
- [x] T015 [US1] Tests in `tests/backend/test_mcp_tools.py` (search part)

## Phase 4 — US2 Current weather (P1)
**Goal**: latest values + age/stale + units + source. **Test**: suppressed fields, stale flag, unknown id, virtual member pooling verified-only.
- [x] T016 [US2] `get_current_weather` tool in `tools.py` (verified members only, sanitize)
- [x] T017 [US2] Tests (current part)

## Phase 5 — US4 Forecast (P1)
- [x] T018 [US4] `InfluxClient.query_forecast_for_stations(..., keep_init_date=False)` in `src/lenticularis/database/influx.py`
- [x] T019 [US4] `get_forecast` tool (model/source, `forecast_issued`, missing hours, gap note)
- [x] T020 [US4] Tests (forecast part) incl. default call path unchanged

## Phase 6 — US3 Past weather (P1)
- [x] T021 [US3] `InfluxClient.query_history_range(member_ids, start, end, every, fields)` — per-field aggregation (gust max, precip sum, dir/snow last, rest mean), explicit `stop`, OR-chain station filter, point cap
- [x] T022 [US3] `get_weather_history` tool (hours | start+end, auto resolution, downsample note)
- [x] T023 [US3] Tests (history part) + Flux-string tests for `query_history_range` in `tests/backend/test_influx_query_clients.py`

## Phase 7 — US5/US6 Föhn + self-description (P2)
- [x] T024 [US5] `get_foehn_status` tool (system default config, runtime unverified-station drop, live/observed/forecast)
- [x] T025 [US6] `describe_service` tool + `lenticularis://about` resource
- [x] T026 [US5] Tests (föhn incl. unverified input dropped; default config stations all verified)

## Phase 8 — US7 Fair use & observability (P2)
- [x] T027 [US7] Apply rate limit + usage logging in tool wrapper; startup logs of all `mcp.*` config; XFF-count log once
- [x] T028 [US7] Add MCP section to `src/lenticularis/api/routers/health.py` (enabled, calls, errors, distinct callers)
- [x] T029 Test `tests/backend/test_mcp_invariants.py` — no pilot-model imports in `mcp_server/`; no write calls; suppressed fields never emitted

## Final Phase — Polish
- [x] T030 Run full backend suite + `ruff check --isolated`
- [x] T031 Update `.ai/context/architecture.md`, `backend-notes.md`, `security-notes.md` (Holfuy assumption, allowlist, leak trap), `features.md`, `.ai/prompts/add-collector.md`, `README.md`
- [x] T032 Update `specs/010-mcp-server/checklists/requirements.md`; mark tasks
- [ ] T033 **Owner/AI-with-permission**: read-only prod verification script (SC-3, exclusions) — NOT DONE: needs read-only SSH approval; exclude clusters whose website canonical is a private station
- [ ] T034 **Owner**: commit, tag `v1.24.0`, deploy, add Traefik rate-limit via IaC, add `mcp.allowed_hosts` to server config.yml if hostname differs
- [ ] T035 Archive spec to `specs/archive/` after release
- [x] T036 Backlog: authenticated MCP (pilot rule sets), thermal/aloft tools (already recorded in `features.md`)
