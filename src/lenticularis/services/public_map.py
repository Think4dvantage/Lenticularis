"""
Public map payload — curated rule sets for anonymous visitors, published rule
sets for signed-in members.

Two properties drive the whole design:

1. **One InfluxDB query, not one per rule set.**  ``run_evaluation()`` batches
   station fetches within a single rule set, so calling it in a loop would issue
   one query per rule set — an unauthenticated N+1 anyone could spray.  Instead we
   collect every station across every rule set, fetch once, and evaluate each rule
   set in memory via ``_evaluate_from_station_data()``.

2. **No-data rule sets are omitted, never shown green.**  The evaluator returns
   green when nothing triggers, including when there is no data at all ("unknown =
   benefit of the doubt").  That is defensible for a signed-in pilot who can see
   ``no_data_stations`` — it is a lie to a visitor, who would read a confident
   green as "this site is flyable right now".
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from lenticularis.database.influx import InfluxClient
from lenticularis.database.models import RuleSet, User
from lenticularis.models.rules import PublicMapResponse, PublicRuleSetMarker
from lenticularis.rules.evaluator import _evaluate_from_station_data
from lenticularis.services.dedup import haversine_m

logger = logging.getLogger(__name__)

# Suppress another owner's rule set when the viewer already has one this close.
# A ridge often carries distinct launches 500-1000 m apart that work in opposite
# wind directions, so a wider radius would hide useful examples.
PROXIMITY_SUPPRESS_M = 500.0

_CACHE: dict[str, tuple[PublicMapResponse, float]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 60.0
_CACHE_MAX = 8

_ANON_CACHE_KEY = "anonymous"


def _cache_get(key: str) -> Optional[PublicMapResponse]:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            return None
        payload, stored_at = hit
        age = time.monotonic() - stored_at
        if age > _CACHE_TTL_S:
            del _CACHE[key]
            logger.info("Public map cache expired — key=%s age=%.1fs", key, age)
            return None
        logger.info("Public map cache hit — key=%s age=%.1fs", key, age)
        return payload


def _cache_set(key: str, payload: PublicMapResponse) -> None:
    # Poisoning guard: an empty payload is usually a transient InfluxDB failure
    # rather than a real "nothing to show".  Caching it would blank the map for
    # the whole TTL.  Let the next request retry instead.
    if not payload.data:
        logger.warning("Public map build produced 0 markers — not caching (key=%s)", key)
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX and key not in _CACHE:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][1])
            del _CACHE[oldest]
            logger.info("Public map cache full — evicted key=%s", oldest)
        _CACHE[key] = (payload, time.monotonic())


def invalidate_public_map_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    logger.info("Public map cache invalidated")


def _station_ids_for(ruleset: RuleSet) -> set[str]:
    ids: set[str] = set()
    for c in ruleset.conditions:
        ids.add(c.station_id)
        if c.station_b_id:
            ids.add(c.station_b_id)
    return ids


def _fetch_station_data(
    influx: InfluxClient,
    station_ids: set[str],
    virtual_members: Optional[dict[str, list[str]]] = None,
) -> dict[str, dict]:
    """One batched fetch for every station across every rule set."""
    vm = virtual_members or {}
    plain_ids = [sid for sid in station_ids if not vm.get(sid)]

    t0 = time.monotonic()
    batched = influx.query_latest_for_stations(plain_ids) if plain_ids else {}
    logger.info(
        "Public map: fetched %d/%d stations in one query (%.0f ms)",
        len(batched), len(plain_ids), (time.monotonic() - t0) * 1000,
    )

    station_data: dict[str, dict] = {sid: d for sid, d in batched.items() if d}
    for sid in station_ids:
        members = vm.get(sid)
        if not members:
            continue
        d = influx.query_latest_virtual(members)
        if d:
            station_data[sid] = d
    return station_data


def _select_rulesets(db: Session, viewer: Optional[User]) -> list[RuleSet]:
    stmt = select(RuleSet).where(RuleSet.lat.isnot(None), RuleSet.lon.isnot(None))
    if viewer is None:
        # Anonymous: the admin's curation AND the owner's consent. Neither alone
        # is sufficient, and consent is checked here rather than copied at write
        # time, so un-publishing takes effect immediately.
        stmt = stmt.where(RuleSet.is_showcase.is_(True), RuleSet.is_public.is_(True))
    else:
        # Signed-in: any published rule set belonging to someone else. The
        # viewer's own are already drawn by the authenticated path.
        stmt = stmt.where(RuleSet.is_public.is_(True), RuleSet.owner_id != viewer.id)
    return list(db.execute(stmt).scalars().all())


def _own_positions(db: Session, viewer: User) -> list[tuple[float, float]]:
    stmt = select(RuleSet.lat, RuleSet.lon).where(
        RuleSet.owner_id == viewer.id,
        RuleSet.lat.isnot(None),
        RuleSet.lon.isnot(None),
    )
    return [(lat, lon) for lat, lon in db.execute(stmt).all()]


def _too_close(rs: RuleSet, own: list[tuple[float, float]]) -> bool:
    for lat, lon in own:
        if haversine_m(rs.lat, rs.lon, lat, lon) < PROXIMITY_SUPPRESS_M:
            return True
    return False


def build_public_map(
    db: Session,
    influx: InfluxClient,
    viewer: Optional[User] = None,
    virtual_members: Optional[dict[str, list[str]]] = None,
) -> PublicMapResponse:
    """
    Build the public map payload.

    ``viewer=None`` builds the anonymous payload: curated + published only,
    identical for every caller and therefore cacheable.  A viewer builds a
    per-viewer payload with proximity suppression applied, which must NOT share
    the anonymous cache entry.
    """
    t0 = time.monotonic()
    rulesets = _select_rulesets(db, viewer)

    own = _own_positions(db, viewer) if viewer is not None else []
    if viewer is not None:
        before = len(rulesets)
        rulesets = [rs for rs in rulesets if not _too_close(rs, own)]
        logger.info(
            "Public map: proximity suppressed %d/%d rule sets within %.0f m for user %s",
            before - len(rulesets), before, PROXIMITY_SUPPRESS_M, viewer.id,
        )

    if not rulesets:
        logger.info("Public map: no qualifying rule sets")
        return PublicMapResponse(data=[], generated_at=_now_iso())

    all_ids: set[str] = set()
    for rs in rulesets:
        all_ids |= _station_ids_for(rs)

    station_data = _fetch_station_data(influx, all_ids, virtual_members)

    markers: list[PublicRuleSetMarker] = []
    omitted_no_data = 0
    omitted_opportunity = 0
    for rs in rulesets:
        needed = _station_ids_for(rs)
        if not needed:
            continue
        # FR-010: a rule set resting on missing data would evaluate green by
        # design. Omit it rather than tell a visitor an unknown site is flyable.
        missing = needed - station_data.keys()
        if missing:
            omitted_no_data += 1
            logger.info(
                "Public map: omitting ruleset %s — no data for %s", rs.id, sorted(missing)
            )
            continue

        decision, _results = _evaluate_from_station_data(rs, station_data)

        if rs.site_type == "opportunity" and decision != "green":
            omitted_opportunity += 1
            continue

        markers.append(PublicRuleSetMarker(
            id=rs.id,
            name=rs.name,
            lat=rs.lat,
            lon=rs.lon,
            site_type=rs.site_type,
            decision=decision,
        ))

    logger.info(
        "Public map: built %d markers in %.0f ms (omitted: %d no-data, %d non-green opportunity)",
        len(markers), (time.monotonic() - t0) * 1000, omitted_no_data, omitted_opportunity,
    )
    return PublicMapResponse(data=markers, generated_at=_now_iso())


def build_anonymous_map_cached(
    db: Session,
    influx: InfluxClient,
    virtual_members: Optional[dict[str, list[str]]] = None,
) -> PublicMapResponse:
    """Anonymous payload, served from a shared cache so cost does not scale with visitors."""
    cached = _cache_get(_ANON_CACHE_KEY)
    if cached is not None:
        return cached
    payload = build_public_map(db, influx, viewer=None, virtual_members=virtual_members)
    _cache_set(_ANON_CACHE_KEY, payload)
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
