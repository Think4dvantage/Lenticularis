# Feature: Public Weather Access for AI Assistants (MCP Server)

## Overview

Lenticularis collects current, historical and forecast weather from many Swiss networks. Today that
data is only usable through the Lenticularis website. This feature lets other AI assistants
(chat assistants, coding agents, personal automations) ask Lenticularis for weather directly, using
the Model Context Protocol (MCP) — so anyone can ask "what is the wind at Jungfraujoch right now?"
or "what's tomorrow's forecast near Interlaken?" and get a trustworthy, clearly-labelled answer.

**Principle: any data that is public-worthy is publicly available.** The first release is open,
read-only and needs no account. Pilot-owned data (rule sets, traffic-light decisions) is out of the
first release but the design must not block adding authenticated access later.

## User Stories

### P1 — Must Have

**US1 — Find a station.**
As a person using an AI assistant, I want to find weather stations by name, place or coordinates so
that I never have to know internal station identifiers.

**Acceptance Criteria**:
- Searching by (partial) name returns matching stations with name, network, elevation and location.
- Searching by a point and radius returns the nearest stations, nearest first.
- Results can be narrowed by network or canton.
- Duplicate co-located stations are not listed twice.

**US2 — Current weather.**
As a person using an AI assistant, I want the latest measurements for a station so that I know what
conditions are right now.

**Acceptance Criteria**:
- Returns wind speed, gust, direction, temperature, humidity, pressure and precipitation where the
  station measures them, each with an explicit unit.
- States when the measurement was taken and how old it is.
- If the data is too old to count as "current", this is stated plainly rather than presented as fresh.
- Fields a station does not measure are omitted, never filled with a guess.

**US3 — Past weather.**
As a person using an AI assistant, I want measurements for a station over a past period so that I
can see what happened (e.g. "how windy was it last Saturday?").

**Acceptance Criteria**:
- The caller chooses station, time range and which measurements.
- Large ranges are summarised to a sensible number of points; the response says how it was reduced.
- A range with no data says so instead of returning an empty answer silently.

**US4 — Forecast.**
As a person using an AI assistant, I want the forecast for a station so that I can plan ahead.

**Acceptance Criteria**:
- Returns hourly forecast values for the requested horizon with units and the time zone stated.
- States which forecast model produced it and when that model run was issued.
- Hours for which no reliable forecast exists are shown as missing, never invented or silently
  carried over from an unrelated hour.

### P2 — Should Have

**US5 — Föhn status.**
As a person using an AI assistant, I want to know whether föhn is active, partial or inactive in the
Swiss föhn regions, now and in the forecast, because it is a key regional weather phenomenon.

**US6 — Self-explaining answers.**
As an assistant developer, I want the service to describe itself (what it covers, units, data
sources, update frequency, known limitations) so that an assistant can answer follow-up questions
about trustworthiness without guessing.

**US7 — Fair, stable use.**
As the service owner, I want public access to be rate-limited and logged so that no single user or
assistant can degrade the website for pilots.

### P3 — Nice to Have

**US8 — Station comparison / area summary.**
As a person using an AI assistant, I want a compact summary of current conditions across several
stations near a point, so that a single question covers a whole valley.

## Functional Requirements

- FR-001: The service MUST be usable by any MCP-capable AI assistant without registration, login or key.
- FR-002: The service MUST be strictly read-only; no request can change any stored data or settings.
- FR-003: The service MUST offer: station search, current weather, past weather, forecast, and föhn status (US1–US5).
- FR-004: Every value MUST carry its unit; every timestamp MUST state its time zone (UTC by default).
- FR-005: Every answer MUST identify its source (network and, for forecasts, model and run time).
- FR-006: Only **verified** data MUST be exposed. Data is verified when its source and meaning are
  understood and it has been checked against reality. Data that is unverified, known faulty, or
  unavailable is omitted rather than flagged.
- FR-006a: **Verified = station quality.** A station is verified when it is operated by an
  institution or professional/semi-professional operator (MeteoSwiss, SLF, METAR/aviation,
  Holfuy, Jungfraubahn, FGA, Windline and similar). Stations from the **Wunderground** and
  **Ecowitt** networks are privately mounted by individuals, cannot be trusted, and MUST NOT be
  exposed — not in search, current, past or forecast answers, nor in area summaries.
- FR-007: Known-faulty values MUST be suppressed. This includes the Hollandiahütte SAC station's
  temperature, humidity and pressure (reports a ~750 m reading while declaring 3248 m).
- FR-008: Experimental data sets MUST NOT be exposed until verified. At the time of writing this
  includes the thermal forecast (solar, CAPE, cloud base, thermal strength): ingested but its
  coverage and correctness are not established.
- FR-009: Forecast hours without usable data MUST be reported as missing. The service MAY fill a gap
  from an older model run of the same source (existing Lenticularis behaviour) but MUST NOT
  interpolate or fabricate values.
- FR-010: Stale observations MUST be labelled as stale, with their age.
- FR-011: Pilot-owned content — rule sets, traffic-light decisions, user accounts, notification
  settings, webcams attached to rule sets — MUST NOT be reachable through this service in this release.
- FR-012: Station-identifying input MUST be validated; malformed or unknown identifiers produce a
  clear "not found" answer, never an internal error.
- FR-013: Errors MUST be understandable by an assistant: say what went wrong and what to try instead
  (e.g. "unknown station — search by name first").
- FR-014: Answers MUST be compact enough for an assistant to use: bounded result sizes, no raw
  dumps of every station and hour.
- FR-015: The service MUST describe its own coverage, units, data sources, update cadence and known
  limitations (US6).
- FR-016: Public access MUST be rate-limited per caller, and each use MUST be logged (which tool,
  how long, outcome) without recording personal data.
- FR-017: The design MUST allow authenticated access to be added later (for pilot rule sets)
  without changing the meaning of the existing public tools.

## Non-Functional Requirements

- NFR-001: Public use MUST NOT noticeably slow the website: pilots' map and replay performance
  remain within their current behaviour under assistant load.
- NFR-002: Typical answers (current weather, one-station forecast) arrive fast enough for
  conversational use (target: within a few seconds).
- NFR-003: The service must be as reliable as the website; if weather data is unavailable it says so
  rather than failing opaquely.
- NFR-004: No new secrets or accounts are required to operate the public release.
- NFR-005: Operability follows the project doctrine (extensive logging, visible health, config
  transparency) — see `.ai/instructions/08-operability.md`.

## Success Criteria

- An assistant with no prior knowledge of Lenticularis can answer "what is the wind at <named place>
  right now" correctly in a single conversation turn.
- 100% of values in answers carry a unit and a source; 0 answers contain an invented value.
- For a sample of stations, answers match what the Lenticularis website shows for the same time.
- A known-faulty value (Hollandiahütte pressure/temperature) never appears in any answer.
- Under a burst of automated assistant traffic, website page and replay response times stay
  within their present range.
- The service owner can see how often each capability is used and by how many distinct callers.

## Key Entities

| Entity | Key Attributes | Notes |
|---|---|---|
| Station | name, network, canton, location, elevation | Co-located duplicates collapsed to one |
| Observation | station, time, measurements with units, age | "Current" = latest, with staleness label |
| Historical series | station, range, chosen measurements, resolution | Reduced if the range is large |
| Forecast | station, model, run time, hourly values with units | Missing hours explicit |
| Föhn status | region, state (active/partial/inactive/no data), time | Observed and forecast |
| Service description | coverage, sources, units, cadence, limitations | Self-documentation (US6) |

## Out of Scope

- Pilot rule sets and traffic-light (green/orange/red) decisions — **backlog**, needs
  authentication (see Dependencies, FR-017).
- Any write action (creating or editing anything).
- Thermal forecast data and wind-aloft grid data (not yet verified / not station-level) — revisit
  once verified.
- Authentication, API keys, accounts, or OAuth for assistants.
- Push notifications, webcams, user statistics, administration functions.
- Natural-language answers generated by Lenticularis itself — the service returns data; the
  assistant writes the prose.

## Assumptions

- Data from the verified networks is public-worthy. Wunderground and Ecowitt (private individuals'
  stations) are excluded entirely. Holfuy is semi-professional and included. (Redistribution
  terms of Holfuy's keyed API are still worth a quick check during planning.)
- The existing station and föhn read endpoints are open by design (owner-confirmed, and verified in
  code: no auth dependency on `stations.py`, optional-user on the föhn read routes). The project docs
  that said `/api/public` was "the only unauthenticated surface" were corrected 2026-10-06.
- "Verified" is a station-quality rule, not a per-value audit: every network except Wunderground and
  Ecowitt is verified. Individually known-faulty values (FR-007) are still suppressed.
- The first release is hosted alongside the existing application and shares its data.
- Assistants identify themselves only through ordinary connection details; no personal data is stored.
- The ICON-CH1/CH2 forecast gap (hours ~19–33) is an accepted, documented limitation; gaps are shown
  as missing (owner: "no biggie if they are missing").

## Dependencies

- Existing collectors, stored observations and forecasts (no new data collection needed).
- A later spec for authenticated access (pilot rule sets); this spec only guarantees it is not blocked.
- Upstream forecast provider (lsmfapi) for forecast availability.

## Edge Cases

- Station exists but has never reported, or stopped reporting hours/days ago.
- Search matches many stations (bounded list, nearest/best first) or none (helpful suggestion).
- Requested time range is in the future, reversed, or extremely long.
- Requested forecast horizon exceeds what the model provides.
- Two stations at the same place from different networks (shown once; highest-priority network wins).
- Forecast model run changes between two questions (run time in the answer explains differences).
- Data backend temporarily unavailable — a clear, retryable error, no partial garbage.
- A caller exceeding the rate limit receives a clear "slow down, retry in N seconds" answer.
- Föhn region with insufficient input data reports "no data", not "inactive".
