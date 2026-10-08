"""
MCP-private station registry: verified networks only, deduplicated on its own.

Why a separate registry (specs/010 research R4): the website's dedup merges co-located stations
across ALL networks, and ``query_latest_virtual`` / ``query_history_virtual`` pool every member
newest-wins. Filtering the website registry's output is therefore not enough — a MeteoSwiss
station with an Ecowitt neighbour would leak Ecowitt values. Here the private networks are removed
BEFORE dedup, so clusters, members and canonical ids contain verified stations only.
"""
from __future__ import annotations

import logging
import math
from typing import Iterable, Optional

from lenticularis.models.weather import WeatherStation
from lenticularis.services.dedup import build_deduped_registry, haversine_m
from lenticularis.mcp_server.sanitize import name_matches

logger = logging.getLogger(__name__)


class McpRegistry:
    def __init__(self, verified_networks: Iterable[str]) -> None:
        self.verified_networks: frozenset[str] = frozenset(verified_networks)
        self.stations: dict[str, WeatherStation] = {}
        self.virtual_members: dict[str, list[str]] = {}
        self._alias: dict[str, str] = {}   # any verified member id -> canonical id

    def rebuild(
        self,
        raw: dict[str, WeatherStation],
        distance_m: float,
        manual_pairs: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        """Rebuild in place from the full station registry. Safe to call repeatedly."""
        verified_raw = {
            sid: s
            for sid, s in raw.items()
            if s.network in self.verified_networks and s.network != "foehn"
        }
        # Unknown ids in manual_pairs are ignored by build_deduped_registry (checked).
        display, members = build_deduped_registry(verified_raw, distance_m, manual_pairs)
        alias: dict[str, str] = {}
        for canon, ids in members.items():
            for mid in ids:
                alias[mid] = canon
        # In-place replace so references held elsewhere stay valid.
        self.stations = display
        self.virtual_members = members
        self._alias = alias
        logger.info(
            "[Lenti:mcp] verified registry: %d stations (%d merged groups) from %d raw; networks=%s",
            len(display), len(members), len(raw), sorted(self.verified_networks),
        )

    def resolve(self, station_id: str) -> Optional[str]:
        """Map a canonical or member id to the canonical id; None if not a verified station."""
        if station_id in self.stations:
            return station_id
        return self._alias.get(station_id)

    def member_ids(self, canonical_id: str) -> list[str]:
        """Verified member ids to query (canonical first); a single id for unmerged stations."""
        return list(self.virtual_members.get(canonical_id) or [canonical_id])

    def search(
        self,
        query: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        network: Optional[str] = None,
        canton: Optional[str] = None,
    ) -> list[tuple[WeatherStation, Optional[float]]]:
        """Return ``[(station, distance_km|None)]`` — nearest first with a point, else by name."""
        results: list[tuple[WeatherStation, Optional[float]]] = []
        for s in self.stations.values():
            if network and s.network != network:
                continue
            if canton and (s.canton or "").upper() != canton.upper():
                continue
            if query and not name_matches(query, s.name):
                continue
            dist: Optional[float] = None
            if lat is not None and lon is not None:
                dist = haversine_m(lat, lon, s.latitude, s.longitude) / 1000.0
                if radius_km is not None and dist > radius_km:
                    continue
            results.append((s, dist))
        if lat is not None and lon is not None:
            results.sort(key=lambda r: (r[1] if r[1] is not None else math.inf, r[0].name))
        else:
            results.sort(key=lambda r: (r[0].name.lower(), r[0].station_id))
        return results
