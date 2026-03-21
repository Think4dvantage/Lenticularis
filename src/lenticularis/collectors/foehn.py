"""
Föhn status collector for Lenticularis.

Evaluates all föhn regions against the latest station observations and writes
numeric status values to ``weather_data`` as virtual stations so that rule sets
can condition on per-region föhn status without repeating the detection logic.

Virtual station IDs: ``foehn-haslital``, ``foehn-beo``, ``foehn-wallis``,
``foehn-reussthal``, ``foehn-rheintal``, ``foehn-guggi``, ``foehn-overall``.
Field: ``foehn_active``  —  1.0=active  0.5=partial  0.0=inactive  -1.0=no_data
"""
from __future__ import annotations

import logging

from lenticularis.foehn_detection import (
    get_all_station_ids,
    get_regions,
    VIRTUAL_STATIONS,
    eval_region,
)
from lenticularis.models.weather import WeatherStation

logger = logging.getLogger(__name__)

NETWORK = "foehn"

# WeatherStation objects for registration in the station registry
_VIRTUAL_WEATHER_STATIONS: list[WeatherStation] = [
    WeatherStation(
        station_id=vs["station_id"],
        name=vs["name"],
        network=NETWORK,
        latitude=vs["latitude"],
        longitude=vs["longitude"],
        elevation=vs["elevation"],
        canton=vs["canton"],
    )
    for vs in VIRTUAL_STATIONS
]


class FoehnCollector:
    """Evaluates föhn regions and writes results to InfluxDB as virtual stations.

    Not a ``BaseCollector`` subclass — it derives data from InfluxDB rather
    than an external HTTP source.  The scheduler calls ``run(influx, registry)``
    directly.
    """

    async def run(self, influx, station_registry: dict) -> int:
        """Evaluate all regions, write results, update station registry.

        Returns the number of InfluxDB points written.
        """
        latest  = influx.query_latest_for_stations(get_all_station_ids())
        regions = [eval_region(r, latest) for r in get_regions()]
        influx.write_foehn_status(regions)

        # Keep virtual stations in the shared registry so the station picker
        # and map can find them
        for ws in _VIRTUAL_WEATHER_STATIONS:
            station_registry[ws.station_id] = ws

        count = len(regions) + 1   # per-region + overall
        return count
