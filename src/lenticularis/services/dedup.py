"""
Proximity-based weather station deduplication.

When two or more stations are within PROXIMITY_THRESHOLD_M metres of each other
they are collapsed into a single virtual station for the presentation layer.
InfluxDB data is NOT affected — all real stations continue to write normally.

The canonical station for each cluster is the highest-priority member by network.
Lower-priority members are hidden from the display registry but remain queryable.

Priority order (index 0 = highest):
  meteoswiss > slf > metar > holfuy > windline > ecowitt > wunderground

The ``foehn`` network is excluded from deduplication (synthetic pressure-delta
virtual stations; pairing them with real stations makes no sense).
"""
from __future__ import annotations

import math
from typing import Optional  # noqa: F401 (used in type hints)

from lenticularis.models.weather import WeatherStation

PROXIMITY_THRESHOLD_M = 50.0

NETWORK_PRIORITY: list[str] = [
    "meteoswiss",
    "slf",
    "metar",
    "holfuy",
    "windline",
    "ecowitt",
    "wunderground",
]

_EXCLUDED_NETWORKS = {"foehn"}


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two WGS84 points."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Union-Find (for transitive cluster closure)
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self._parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self._parent[ry] = rx


# ---------------------------------------------------------------------------
# Priority helpers
# ---------------------------------------------------------------------------

def _network_rank(network: str) -> int:
    """Lower rank = higher priority.  Unknown networks get lowest priority."""
    try:
        return NETWORK_PRIORITY.index(network)
    except ValueError:
        return len(NETWORK_PRIORITY)


def _canonical(stations: list[WeatherStation]) -> WeatherStation:
    """Return the highest-priority station from a cluster."""
    return min(stations, key=lambda s: (_network_rank(s.network), s.station_id))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_deduped_registry(
    raw: dict[str, WeatherStation],
    distance_m: float = PROXIMITY_THRESHOLD_M,
    manual_pairs: Optional[list[tuple[str, str]]] = None,
) -> tuple[dict[str, WeatherStation], dict[str, list[str]]]:
    """
    Deduplicate ``raw`` by proximity and return:

    * ``display_registry`` — one entry per cluster (the canonical station,
      enriched with ``member_ids``), plus all ungrouped real stations.
      Foehn stations pass through unchanged.
    * ``virtual_members`` — maps canonical station_id → ordered list of all
      member station_ids (canonical first).  Only populated for multi-member
      clusters; single-station entries are omitted.

    ``manual_pairs`` — list of (station_id_a, station_id_b) tuples that are
    always merged regardless of distance.  Station IDs not present in ``raw``
    are silently ignored.

    This function is pure (no side-effects) and fast enough to call at startup
    and after each collector run.
    """
    eligible: list[WeatherStation] = []
    excluded: list[WeatherStation] = []

    for s in raw.values():
        if s.network in _EXCLUDED_NETWORKS:
            excluded.append(s)
        else:
            eligible.append(s)

    eligible_ids = {s.station_id for s in eligible}

    # Build proximity clusters using union-find
    uf = _UnionFind([s.station_id for s in eligible])

    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            dist = haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
            if dist < distance_m:
                uf.union(a.station_id, b.station_id)

    # Apply manual overrides — merge pairs regardless of distance
    for id_a, id_b in (manual_pairs or []):
        if id_a in eligible_ids and id_b in eligible_ids:
            uf.union(id_a, id_b)

    # Group stations by cluster root
    clusters: dict[str, list[WeatherStation]] = {}
    for s in eligible:
        root = uf.find(s.station_id)
        clusters.setdefault(root, []).append(s)

    display_registry: dict[str, WeatherStation] = {}
    virtual_members: dict[str, list[str]] = {}

    for members in clusters.values():
        canon = _canonical(members)
        if len(members) == 1:
            # No dedup needed — pass through unchanged
            display_registry[canon.station_id] = canon
        else:
            # Build ordered member list: canonical first, rest by priority
            others = sorted(
                [s for s in members if s.station_id != canon.station_id],
                key=lambda s: (_network_rank(s.network), s.station_id),
            )
            member_ids = [canon.station_id] + [s.station_id for s in others]
            virtual_station = canon.model_copy(update={"member_ids": member_ids})
            display_registry[canon.station_id] = virtual_station
            virtual_members[canon.station_id] = member_ids

    # Re-add excluded (foehn) stations unchanged
    for s in excluded:
        display_registry[s.station_id] = s

    return display_registry, virtual_members
