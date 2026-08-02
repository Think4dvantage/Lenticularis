# Feature: Reactive Ruleset Evaluation

## Overview

Ruleset decisions (the green/orange/red traffic lights for launch, landing, and opportunity
sites) are currently recomputed on a fixed 10-minute schedule, regardless of whether any
underlying station data actually changed. Some networks (Holfuy) deliver new readings every
5 minutes, so a decision can lag up to 10 minutes behind reality even when fresher data has
already landed. This feature replaces the fixed schedule with a reactive one: a ruleset is
re-evaluated the moment a station it depends on receives new data — observed or forecast —
and never evaluated on a timer.

## Clarifications

### Session 2026-08-03

- Q: What granularity counts as "a station received new data"? → A: Whole collector run — when
  a network's collector run completes, every station in that network is treated as potentially
  updated. No per-station value diffing.
- Q: What happens to decisions at application startup, before any event has fired? → A: One
  evaluation pass over every ruleset at boot, then pure event-driven for everything after.
- Q: Should this feature also precompute the whole forecast horizon (fixing the v1.22.6 replay
  root cause, not just its symptom)? → A: Yes — precompute and store the entire forecast horizon
  whenever forecast data updates for a ruleset's station(s); replay/Play then reads the
  precomputed result instead of querying live per frame.
- Q: Should a zero-condition ruleset (nothing to react to) still produce periodic history
  entries? → A: No — it has no dependency to hang a trigger on, so it is simply never
  evaluated and never appears in the reactive event stream. Its live-read default (green, per
  existing "no conditions" behavior) is unaffected.

## User Stories

### P1 — Live decisions track data, not the clock
As a pilot watching the map, I want a launch/landing site's decision to update as soon as its
station reports new data, so that I'm never looking at a decision that's staler than the data
already sitting in the system.

**Acceptance Criteria**:
- When a station referenced by a ruleset's conditions receives a new observation, that
  ruleset's live decision is recomputed without waiting for a fixed interval to elapse.
- A ruleset whose stations haven't reported anything new is not recomputed — no wasted work.

### P2 — Forecast decisions track forecast updates the same way
As a pilot checking tomorrow's forecast decision, I want it to reflect the latest model run as
soon as that run's data is written, not the next time a timer happens to fire.

**Acceptance Criteria**:
- When forecast data for a station referenced by a ruleset is written (a new or updated model
  run), that ruleset's forecast decision is recomputed for its **entire forecast horizon**, not
  just "right now" — so a pilot scrubbing replay through any future hour sees an already-computed
  decision rather than triggering a fresh one.

### P2b — Replay stops depending on live query latency
As a pilot scrubbing the map's time-navigation bar through a forecast day, I want marker colors
to keep up with the animation, so that Play is actually watchable instead of lagging behind.

**Acceptance Criteria**:
- Scrubbing or playing through forecast time reads an already-computed decision for that moment
  — it does not wait on a fresh database round-trip per frame.
- This directly retires the root cause behind the v1.22.6 workaround (a slow per-frame forecast
  query) rather than only mitigating its symptom.

### P3 — No functional regression for existing consumers
As a pilot relying on decision history, email alerts, or the org dashboard, I want those to keep
working exactly as before — only the trigger changes, not the shape or meaning of a decision.

**Acceptance Criteria**:
- Decision history (the timeline strip on the org dashboard / ruleset analysis page) keeps
  recording decisions at essentially the same fidelity as today.
- Email notifications still fire only on an actual decision change, never repeatedly for an
  unchanged decision.

## Functional Requirements

- FR-001: A ruleset MUST be re-evaluated (live decision) when any station referenced by one of
  its conditions receives new observation data.
- FR-002: A ruleset MUST be re-evaluated (forecast decision) when any station referenced by one
  of its conditions receives new or updated forecast data.
- FR-002a: A forecast re-evaluation MUST compute and store the decision for the ruleset's
  **entire forecast horizon** in one pass, not only the single moment "now" — so that any future
  hour a pilot later scrubs to via replay already has a computed decision available, without
  re-querying the underlying data at scrub time.
- FR-003: A ruleset that combines stations from a deduplicated/virtual station cluster MUST be
  re-evaluated when *any* physical station backing that cluster updates — not only when the
  cluster's canonical station happens to be the one that reported.
- FR-004: The system MUST NOT re-evaluate a ruleset that has no dependency on the data that just
  changed.
- FR-005: The existing fixed-interval scheduled re-evaluation is retired — evaluation only ever
  happens in reaction to new station data, per FR-001/FR-002.
- FR-006: Re-evaluation results MUST continue to feed the same downstream consumers as today
  (decision history, org dashboard, email notifications) without any change to their behavior
  from the consumer's point of view.
- FR-007: Repeated re-evaluation triggered in quick succession by a burst of incoming data (e.g.
  one collector run updating hundreds of stations at once) MUST NOT re-evaluate the same
  ruleset more than once per burst.
- FR-008: At application startup, every ruleset MUST receive one evaluation pass before settling
  into pure event-driven re-evaluation — no ruleset should show an undefined or indefinitely
  stale decision after a restart while waiting for its first relevant data event.
- FR-009: A ruleset with no conditions has no data dependency to react to and is therefore never
  triggered by this feature — its live-read default (green) is unaffected and out of scope here.

## Non-Functional Requirements

- NFR-001: Re-evaluating in reaction to a data update MUST NOT introduce a per-station query
  loop — a burst that touches many rulesets is still one query per affected ruleset, matching
  today's batching discipline, never one query per station.
- NFR-002: The total number of ruleset evaluations performed over a day MUST NOT exceed what
  today's fixed-schedule approach performs by more than a small, bounded factor — this is a
  responsiveness improvement, not a licence for unbounded evaluation churn.
- NFR-003: A single burst of incoming data MUST NOT cause more Influx/database round-trips than
  the number of distinct rulesets actually affected by that burst.

## Success Criteria

- A pilot watching a Holfuy-backed launch site sees its decision reflect station data less than
  a minute old, essentially all the time — never waiting on a 10-minute-old reading.
- No ruleset is ever evaluated against data that hasn't changed since its last evaluation.
- Decision history density for any given ruleset does not become misleadingly sparse or
  overwhelmingly dense compared to today's every-10-minutes cadence.
- Email notification behavior is indistinguishable from today's: one notification per actual
  decision change, never more.
- Scrubbing or playing the map's time-navigation bar through forecast time never visibly lags
  behind the animation waiting on a decision to compute.

## Key Entities

| Entity | Key Attributes | Notes |
|--------|---------------|-------|
| RuleSet | conditions (station references) | Existing entity — unchanged shape |
| RuleCondition | station_id, station_b_id | Existing entity — the dependency edge this feature reacts to |
| Station data update | station_id, kind (observed / forecast), timestamp | Not a new persisted entity — the *event* this feature reacts to |

## Out of Scope

- Changing what a "decision" means or how conditions/groups combine (untouched).
- Any UI change — this is purely about *when* evaluation happens, not what it produces or how
  it's displayed.
- The thermal-forecast rules integration (`specs/006-thermal-forecast` Phase 2) — reactive
  evaluation should extend to it naturally once that ships, but wiring it in is not this
  feature's job.

## Assumptions

- "A station received new data" is observed at the granularity of a collector run completing,
  not a per-reading diff inside that run — a network's entire reported station set is treated
  as having potentially new data whenever that network's collector run finishes (confirmed —
  see Clarifications).
- SMTP-backed email notification suppression-on-unchanged-decision (already implemented today)
  is sufficient protection against notification storms from more frequent re-evaluation; no new
  throttling mechanism is assumed necessary.
- One evaluation pass over every ruleset at application startup is acceptable cost — this is the
  same total work today's system already does once every 10 minutes, just performed once at boot
  instead.

## Dependencies

- The existing collector/scheduler run-completion signal (today wired as `on_collector_run` /
  `on_forecast_run` post-run hooks) is the natural place to originate this feature's trigger.
- The existing station deduplication/virtual-station mapping (canonical station ↔ physical
  member stations) is required to satisfy FR-003.

## Edge Cases

- A ruleset references a station that never reports again (dead sensor) — it simply never gets
  re-evaluated after its last real update. A last known decision going stale with age is out of
  scope for this feature — same as today's behavior, where a stale reading already degrades the
  same way through the existing no-data fail-safe.
- Application restart: resolved by FR-008 — one evaluation pass over every ruleset at boot,
  before settling into pure event-driven re-evaluation.
- A ruleset with zero conditions (e.g. the currently-empty "Höhematte" in prod) has nothing to
  react to under a pure reactive model — resolved by FR-009: it is simply never triggered and
  never appears in decision history; its live-read default (green) is unchanged.
- A forecast collector run rewrites the *entire* horizon for potentially all stations at once
  (e.g. the hourly SwissMeteo grid/station forecast run) — per FR-002a this means every ruleset
  touched by that run gets its whole horizon recomputed in one pass each, not per-hour. This is
  strictly less total work than today's per-frame replay approach, not more.
- Two collector runs for different networks completing moments apart, both touching stations
  used by the same ruleset — FR-007 requires this collapses to a single re-evaluation of that
  ruleset, not one per contributing network.
