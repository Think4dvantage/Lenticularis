"""
AI-assisted rule condition suggestions via a local Ollama instance.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lenticularis.api.dependencies import get_current_user
from lenticularis.config import get_config
from lenticularis.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class StationHint(BaseModel):
    station_id: str
    name: str
    network: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[int] = None


class SuggestRequest(BaseModel):
    description: str
    stations: list[StationHint] = []


class SuggestedCondition(BaseModel):
    station_id: str
    field: str
    operator: str
    value_a: float
    value_b: Optional[float] = None
    result_colour: str = "red"


class SuggestResponse(BaseModel):
    conditions: list[SuggestedCondition]
    explanation: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a paragliding weather rule assistant. Your job is to convert a natural language description of flying conditions into structured JSON rules.

The user may write in any language (German, French, Italian, English). Understand the description regardless of language, but always output the JSON fields in English as specified below.

Valid fields: wind_speed (km/h), wind_gust (km/h), wind_direction (degrees 0-360), temperature (°C), humidity (%), pressure (hPa), pressure_delta (hPa, absolute pressure difference between two stations — Föhn detection), precipitation (mm), snow_depth (cm)
Valid operators: >, <, >=, <=, =, between, not_between, in_direction_range
  - "between" and "not_between" require value_a (lower) and value_b (upper)
  - "in_direction_range" requires value_a (from °) and value_b (to °), clockwise
  - "pressure_delta" conditions use the nearest valley/ridge station pair the user mentions
Valid result_colour values: green (good/flyable), orange (marginal), red (dangerous/no-fly)

Return ONLY a JSON object with this exact structure, no explanation before or after:
{
  "conditions": [
    {"station_id": "...", "field": "...", "operator": "...", "value_a": 0, "value_b": null, "result_colour": "red"}
  ],
  "explanation": "one sentence summary in English"
}

Compass direction reference (use these degree ranges for in_direction_range):
  N   (Nord/North):      from=337, to=23
  NNE (Nord-Nordost):    from=23,  to=45
  NE  (Nordost):         from=23,  to=68
  ENE (Ost-Nordost):     from=45,  to=90
  E   (Ost/East):        from=68,  to=113
  ESE (Ost-Südost):      from=90,  to=135
  SE  (Südost):          from=113, to=158
  SSE (Süd-Südost):      from=135, to=180
  S   (Süd/South):       from=158, to=203
  SSW (Süd-Südwest):     from=180, to=225
  SW  (Südwest):         from=203, to=248
  WSW (West-Südwest):    from=225, to=270
  W   (West):            from=248, to=293
  WNW (West-Nordwest):   from=270, to=315
  NW  (Nordwest):        from=293, to=338
  NNW (Nord-Nordwest):   from=315, to=337

  Broad sector shortcuts (use when user says "anything with X in the name" or a wide arc):
  "all southerly" / "alles mit S" / SE–SW arc: from=113, to=248
  "all northerly" / "alles mit N" / NW–NE arc: from=293, to=68
  "all westerly"  / "alles mit W" / SW–NW arc: from=203, to=338
  "all easterly"  / "alles mit E/O" / NE–SE arc: from=23, to=158

Rules:
- If a "Likely station matches based on name" section is provided, prefer those station_ids for the abbreviated names.
- If a "Geographically nearby stations" section is provided, use those station_ids when the user references an area or location (e.g. "around Mürren", "near Birg", "stations in that area").
- "Same height" / "gleiche Höhe" means similar elevation — prefer the stations listed in the geo hints that are closest in altitude.
- IMPORTANT — "all surrounding stations" / "alle umliegenden Stationen" / "each nearby station": generate one separate condition entry per station listed in the geo hints. Do NOT collapse them into one. Every station gets its own {"station_id": ..., ...} object in the conditions array.
- If the user describes both a GREEN (flyable) case and a RED (no-fly) case, generate conditions for BOTH. Flyable criteria use result_colour=green; no-fly criteria use result_colour=red.
- Pick station_id from the provided list. If unsure, use the station that sounds geographically closest.
- For wind direction windows use in_direction_range with value_a=from and value_b=to (clockwise).
- Use the compass reference above for any direction name in any language (Süd, South, S, SE, Südost, etc.).
- When the user says "from the south" or "Südwind" or "aus S/SW/SE" as a flyable condition, use result_colour=green.
- For "no rain / no precipitation" use: field=precipitation, operator=>, value_a=0, result_colour=red
- Use result_colour=red for dangerous/no-fly conditions, orange for marginal, green for ideal.
- Sort conditions: GREEN conditions first (flyable criteria), then RED/ORANGE (no-fly criteria). Within each group: wind direction, then wind speed/gust, then precipitation, then temperature.
- For Föhn / pressure gradient conditions use field=pressure_delta with the two station IDs the user mentions; if unspecified, pick the closest valley/ridge pair from the available stations.

Example:
Input: "Beatenberg: Südwind, Windgeschwindigkeit zwischen 10 und 30 km/h, Böen unter 45 km/h, kein Regen"
Output:
{
  "conditions": [
    {"station_id": "STATION_A", "field": "wind_direction", "operator": "in_direction_range", "value_a": 158, "value_b": 203, "result_colour": "green"},
    {"station_id": "STATION_A", "field": "wind_speed", "operator": "between", "value_a": 10, "value_b": 30, "result_colour": "green"},
    {"station_id": "STATION_A", "field": "wind_gust", "operator": ">", "value_a": 45, "value_b": null, "result_colour": "red"},
    {"station_id": "STATION_A", "field": "precipitation", "operator": ">", "value_a": 0, "value_b": null, "result_colour": "red"}
  ],
  "explanation": "Flyable with southerly wind 10–30 km/h and gusts below 45 km/h; no-fly if precipitation occurs or gusts exceed 45 km/h."
}
"""


# ---------------------------------------------------------------------------
# Natural-language wind direction / speed normaliser
# Runs before the prompt is sent so Ollama never has to guess degrees.
# Replaces recognised terms with "term [explicit degrees / units]" in-place.
# ---------------------------------------------------------------------------

# Each entry: (compiled regex, replacement template)
# Broad sectors (Komponente / component / composante / componente)
_DIR_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── Broad sectors ──────────────────────────────────────────────────────
    (re.compile(r'\b(süd\s*komponente|south\s*component|composante\s*sud|componente\s*sud)\b', re.I),
     r'\1 [in_direction_range 113–248°, SE–SW]'),
    (re.compile(r'\b(nord\s*komponente|north\s*component|composante\s*nord|componente\s*nord)\b', re.I),
     r'\1 [in_direction_range 293–68°, NW–NE, crosses north]'),
    (re.compile(r'\b(west\s*komponente|west(?:erly)?\s*component|composante\s*ouest|componente\s*ovest)\b', re.I),
     r'\1 [in_direction_range 203–338°, SW–NW]'),
    (re.compile(r'\b(ost\s*komponente|east(?:erly)?\s*component|composante\s*est|componente\s*est)\b', re.I),
     r'\1 [in_direction_range 23–158°, NE–SE]'),

    # ── Half-intercardinal exact (must come before intercardinals to prevent partial matches) ──
    (re.compile(r'\b(nord-?nordost(?:wind)?|aus\s+nno\b|aus\s+nne\b|north-?north-?east(?:erly)?|vent\s+du\s+nord-?nord-?est|vento\s+da\s+nord-?nord-?est)\b', re.I),
     r'\1 [in_direction_range 23–45°, NNE]'),
    (re.compile(r'\b(ost-?nordost(?:wind)?|aus\s+ono\b|aus\s+ene\b|east-?north-?east(?:erly)?|vent\s+est-?nord-?est|vento\s+est-?nord-?est)\b', re.I),
     r'\1 [in_direction_range 45–90°, ENE]'),
    (re.compile(r'\b(ost-?südost(?:wind)?|aus\s+oso\b|aus\s+ese\b|east-?south-?east(?:erly)?|vent\s+est-?sud-?est|vento\s+est-?sud-?est)\b', re.I),
     r'\1 [in_direction_range 90–135°, ESE]'),
    (re.compile(r'\b(süd-?südost(?:wind)?|aus\s+sso\b|aus\s+sse\b|south-?south-?east(?:erly)?|vent\s+du\s+sud-?sud-?est|vento\s+da\s+sud-?sud-?est)\b', re.I),
     r'\1 [in_direction_range 135–180°, SSE]'),
    (re.compile(r'\b(süd-?südwest(?:wind)?|aus\s+ssw\b|south-?south-?west(?:erly)?|vent\s+du\s+sud-?sud-?ouest|vento\s+da\s+sud-?sud-?ovest)\b', re.I),
     r'\1 [in_direction_range 180–225°, SSW]'),
    (re.compile(r'\b(west-?südwest(?:wind)?|aus\s+wsw\b|west-?south-?west(?:erly)?|vent\s+ouest-?sud-?ouest|vento\s+ovest-?sud-?ovest)\b', re.I),
     r'\1 [in_direction_range 225–270°, WSW]'),
    (re.compile(r'\b(west-?nordwest(?:wind)?|aus\s+wnw\b|west-?north-?west(?:erly)?|vent\s+ouest-?nord-?ouest|vento\s+ovest-?nord-?ovest)\b', re.I),
     r'\1 [in_direction_range 270–315°, WNW]'),
    (re.compile(r'\b(nord-?nordwest(?:wind)?|aus\s+nnw\b|north-?north-?west(?:erly)?|vent\s+du\s+nord-?nord-?ouest|vento\s+da\s+nord-?nord-?ovest)\b', re.I),
     r'\1 [in_direction_range 315–337°, NNW]'),

    # ── Intercardinal exact ────────────────────────────────────────────────
    (re.compile(r'\b(nordost(?:wind)?|aus\s+no\b|aus\s+nordost|northeast(?:erly)?|vent\s+du\s+nord-?est|vento\s+da\s+nord-?est)\b', re.I),
     r'\1 [in_direction_range 23–68°, NE]'),
    (re.compile(r'\b(südost(?:wind)?|aus\s+so\b|aus\s+südost|southeast(?:erly)?|vent\s+du\s+sud-?est|vento\s+da\s+sud-?est)\b', re.I),
     r'\1 [in_direction_range 113–158°, SE]'),
    (re.compile(r'\b(südwest(?:wind)?|aus\s+sw\b|aus\s+südwest|southwest(?:erly)?|vent\s+du\s+sud-?ouest|vento\s+da\s+sud-?ovest)\b', re.I),
     r'\1 [in_direction_range 203–248°, SW]'),
    (re.compile(r'\b(nordwest(?:wind)?|aus\s+nw\b|aus\s+nordwest|northwest(?:erly)?|vent\s+du\s+nord-?ouest|vento\s+da\s+nord-?ovest)\b', re.I),
     r'\1 [in_direction_range 293–338°, NW]'),

    # ── Cardinal exact ─────────────────────────────────────────────────────
    (re.compile(r'\b(nordwind|aus\s+nord(?:en)?|north(?:erly)?\s+wind|vent\s+du\s+nord|vento\s+da\s+nord)\b', re.I),
     r'\1 [in_direction_range 337–23°, N]'),
    (re.compile(r'\b(südwind|aus\s+süd(?:en)?|south(?:erly)?\s+wind|vent\s+du\s+sud|vento\s+da\s+sud)\b', re.I),
     r'\1 [in_direction_range 158–203°, S]'),
    (re.compile(r'\b(ostwind|aus\s+ost(?:en)?|east(?:erly)?\s+wind|vent\s+d[e\']?\s*est|vento\s+da\s+est)\b', re.I),
     r'\1 [in_direction_range 68–113°, E]'),
    (re.compile(r'\b(westwind|aus\s+west(?:en)?|west(?:erly)?\s+wind|vent\s+d[e\']?\s*ouest|vento\s+da\s+ovest)\b', re.I),
     r'\1 [in_direction_range 248–293°, W]'),
]

# Gust / speed patterns — normalise to explicit field + unit + operator
_SPEED_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Böen / Windböen / gusts — under
    (re.compile(r'\b(?:wind)?(?:böen|boen|gusts?|rafales?|raffiche)\s+(?:unter|under|less\s+than|inf[eé]rieures?\s+[àa]|inferiori\s+a|<)\s*(\d+)\s*(?:km/h|kmh|kph)?', re.I),
     r'wind_gust < \1 km/h'),
    # Böen / Windböen — over
    (re.compile(r'\b(?:wind)?(?:böen|boen|gusts?|rafales?|raffiche)\s+(?:über|uber|grösser\s+als|gr[oö]sser\s+wie|greater\s+than|more\s+than|sup[eé]rieures?\s+[àa]|superiori\s+a|>)\s*(\d+)\s*(?:km/h|kmh|kph)?', re.I),
     r'wind_gust > \1 km/h'),
    # Wind speed — under
    (re.compile(r'\bwind(?:geschwindigkeit)?\s+(?:unter|under|less\s+than|<)\s*(\d+)\s*(?:km/h|kmh|kph)?', re.I),
     r'wind_speed < \1 km/h'),
    # Wind speed — over
    (re.compile(r'\bwind(?:geschwindigkeit)?\s+(?:über|uber|grösser\s+als|greater\s+than|>)\s*(\d+)\s*(?:km/h|kmh|kph)?', re.I),
     r'wind_speed > \1 km/h'),
]


def _normalize_description(description: str) -> str:
    """
    Annotate natural-language wind direction phrases and speed comparisons
    with their explicit degree ranges / field names so Ollama doesn't have
    to guess. Original wording is preserved; annotations are appended in [].
    """
    result = description
    for pattern, replacement in _DIR_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in _SPEED_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _fuzzy_station_hints(description: str, stations: list[StationHint]) -> list[tuple[str, StationHint]]:
    """
    Find stations likely referenced by abbreviated or partial names in the description.
    Checks whether any word in the description is a case-insensitive prefix of (or contained
    in) any word of a station name. Returns (matched_term, station) pairs, deduped by station.
    """
    desc_lower = description.lower()
    # Extract alphabetic tokens (handles umlauts)
    desc_words = re.findall(r"[a-zäöüàáâèéêùúûìíîß]{3,}", desc_lower)

    seen_ids: set[str] = set()
    hints: list[tuple[str, StationHint]] = []

    for station in stations:
        name_lower = station.name.lower()
        name_words = re.findall(r"[a-zäöüàáâèéêùúûìíîß]{3,}", name_lower)
        for dw in desc_words:
            matched = any(nw.startswith(dw) or dw in nw for nw in name_words)
            if matched and station.station_id not in seen_ids:
                hints.append((dw, station))
                seen_ids.add(station.station_id)
                break

    return hints


# ---------------------------------------------------------------------------
# Known Swiss paragliding / mountain locations → (lat, lon, elevation_m)
# Used for geographic context matching ("stations around Mürren/Birg area")
# ---------------------------------------------------------------------------

_KNOWN_LOCATIONS: dict[str, tuple[float, float, int]] = {
    # Bernese Oberland
    "mürren": (46.559, 7.893, 1638), "murren": (46.559, 7.893, 1638),
    "birg": (46.563, 7.837, 2677),
    "schilthorn": (46.558, 7.836, 2970),
    "grindelwald": (46.624, 8.041, 1034),
    "wengen": (46.607, 7.924, 1274),
    "männlichen": (46.623, 7.941, 2343), "maennlichen": (46.623, 7.941, 2343),
    "kleine scheidegg": (46.585, 7.961, 2061), "scheidegg": (46.585, 7.961, 2061),
    "lauterbrunnen": (46.595, 7.907, 795),
    "beatenberg": (46.694, 7.771, 1150),
    "niesen": (46.645, 7.651, 2362),
    "stockhorn": (46.682, 7.571, 2190),
    "axalp": (46.718, 7.935, 1530),
    "sigriswil": (46.680, 7.737, 900),
    "interlaken": (46.686, 7.863, 566),
    "thun": (46.751, 7.628, 558),
    "spiez": (46.686, 7.672, 628),
    "zweisimmen": (46.551, 7.377, 940),
    "adelboden": (46.492, 7.560, 1356),
    "kandersteg": (46.498, 7.671, 1176),
    "gstaad": (46.474, 7.284, 1050),
    # Valais
    "verbier": (46.097, 7.228, 1500),
    "zermatt": (46.019, 7.749, 1608),
    "saas-fee": (46.105, 7.930, 1803), "saas fee": (46.105, 7.930, 1803),
    "brig": (46.317, 7.988, 678),
    "visp": (46.294, 7.882, 651),
    "sion": (46.233, 7.360, 482),
    "leukerbad": (46.379, 7.631, 1401),
    "crans-montana": (46.312, 7.484, 1500), "montana": (46.312, 7.484, 1500),
    # Central Switzerland
    "engelberg": (46.822, 8.402, 1020),
    "titlis": (46.773, 8.426, 3028),
    "stoos": (46.980, 8.681, 1300),
    "rigi": (47.055, 8.487, 1797),
    "pilatus": (46.979, 8.253, 2073),
    "stanserhorn": (46.924, 8.325, 1898),
    # Graubünden
    "davos": (46.803, 9.835, 1560),
    "arosa": (46.782, 9.676, 1775),
    "flims": (46.833, 9.278, 1100), "laax": (46.810, 9.257, 1100),
    "disentis": (46.707, 8.856, 1130),
    "lenzerheide": (46.729, 9.557, 1472),
    "chur": (46.849, 9.532, 585),
    # Eastern Switzerland / Appenzell
    "säntis": (47.249, 9.343, 2502), "santis": (47.249, 9.343, 2502),
    "flumserberg": (47.098, 9.265, 1400),
    "elm": (46.927, 9.176, 980),
    # Jura / Mittelland
    "chasseral": (47.132, 7.056, 1607),
    "bern": (46.948, 7.447, 540),
    "zürich": (47.378, 8.540, 408), "zurich": (47.378, 8.540, 408),
    "luzern": (47.050, 8.309, 435),
    # Vaud / Fribourg
    "villeneuve": (46.393, 6.928, 375),
    "leysin": (46.338, 7.005, 1263),
    "les diablerets": (46.361, 7.148, 1162),
    "château-d'oex": (46.478, 7.138, 958),
}

_GEO_RADIUS_KM = 20.0      # stations within this radius of a mentioned location
_ELEV_MARGIN_M = 400       # ± margin for "same height" matching


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geo_station_hints(
    description: str,
    stations: list[StationHint],
) -> list[tuple[str, list[StationHint]]]:
    """
    Detect known location names in the description and return nearby stations
    (within _GEO_RADIUS_KM). Also handles "same height / gleiche Höhe" by
    checking elevation proximity to the matched location.

    Returns list of (location_name, [nearby_stations sorted by distance]).
    """
    desc_lower = description.lower()
    # Check for "same height" / elevation intent
    elev_intent = any(kw in desc_lower for kw in (
        "same height", "gleiche höhe", "gleicher höhe", "same elevation",
        "same altitude", "gleiche höhenlage", "auf gleicher höhe",
    ))

    results: list[tuple[str, list[StationHint]]] = []
    seen_locations: set[str] = set()

    # Multi-word locations first (longest match wins), then single words
    for loc_name, (loc_lat, loc_lon, loc_elev) in sorted(
        _KNOWN_LOCATIONS.items(), key=lambda x: -len(x[0])
    ):
        if loc_name in desc_lower and loc_name not in seen_locations:
            seen_locations.add(loc_name)
            nearby: list[tuple[float, StationHint]] = []
            for s in stations:
                if s.latitude is None or s.longitude is None:
                    continue
                dist = _haversine_km(loc_lat, loc_lon, s.latitude, s.longitude)
                if dist <= _GEO_RADIUS_KM:
                    if elev_intent and s.elevation is not None:
                        if abs(s.elevation - loc_elev) > _ELEV_MARGIN_M:
                            continue
                    nearby.append((dist, s))
            nearby.sort(key=lambda x: x[0])
            if nearby:
                results.append((loc_name, [s for _, s in nearby[:8]]))

    return results


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from the model response."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first {...} block
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON found in model response: {text[:200]}")


def _validate_conditions(raw: list) -> list[SuggestedCondition]:
    valid_fields = {
        'wind_speed', 'wind_gust', 'wind_direction', 'temperature',
        'humidity', 'pressure', 'pressure_delta', 'precipitation', 'snow_depth',
    }
    valid_ops = {'>', '<', '>=', '<=', '=', 'between', 'not_between', 'in_direction_range'}
    valid_colours = {'green', 'orange', 'red'}

    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = item.get('field', '')
        operator = item.get('operator', '')
        colour = item.get('result_colour', 'red')
        if field not in valid_fields or operator not in valid_ops or colour not in valid_colours:
            logger.warning("AI suggestion skipped invalid condition: %s", item)
            continue
        try:
            result.append(SuggestedCondition(
                station_id=str(item['station_id']),
                field=field,
                operator=operator,
                value_a=float(item['value_a']),
                value_b=float(item['value_b']) if item.get('value_b') is not None else None,
                result_colour=colour,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("AI suggestion skipped malformed condition %s: %s", item, exc)
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/suggest-conditions", response_model=SuggestResponse)
async def suggest_conditions(
    body: SuggestRequest,
    _current_user: User = Depends(get_current_user),
):
    cfg = get_config()
    ollama_cfg = cfg.ollama

    if not ollama_cfg.enabled:
        raise HTTPException(status_code=503, detail="AI suggestions are disabled in configuration.")

    # Pre-process: resolve natural-language wind terms to explicit degrees/units
    normalised_description = _normalize_description(body.description)

    # Build station list for the prompt (cap at 80 stations to keep prompt small)
    station_lines = "\n".join(
        f"  - {s.station_id}: {s.name} ({s.network})"
        for s in body.stations[:80]
    )

    # Fuzzy-match abbreviated/local station names from the description
    name_hints = _fuzzy_station_hints(normalised_description, body.stations[:80])
    name_hints_block = ""
    if name_hints:
        hint_lines = "\n".join(
            f'  - "{term}" → {s.name} ({s.network}): {s.station_id}'
            for term, s in name_hints
        )
        name_hints_block = f"\nLikely station matches based on name:\n{hint_lines}\n"

    # Geo-match location names (e.g. "around Mürren/Birg area")
    geo_hints = _geo_station_hints(normalised_description, body.stations)
    geo_hints_block = ""
    if geo_hints:
        parts = []
        for loc_name, nearby in geo_hints:
            station_list = ", ".join(f"{s.name} ({s.station_id}, {s.elevation}m)" for s in nearby)
            parts.append(f'  - Near "{loc_name}": {station_list}')
        geo_hints_block = f"\nGeographically nearby stations:\n" + "\n".join(parts) + "\n"

    user_prompt = (
        f"Available stations:\n{station_lines}\n"
        f"{name_hints_block}"
        f"{geo_hints_block}"
        f"\nUser description:\n{normalised_description}"
    )

    payload = {
        "model": ollama_cfg.model,
        "prompt": user_prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "format": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=ollama_cfg.timeout_seconds) as client:
            resp = await client.post(f"{ollama_cfg.url}/api/generate", json=payload)
            resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach Ollama at {ollama_cfg.url}. Make sure it is running."
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Ollama request timed out.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama error: {exc.response.status_code}")

    raw_text = resp.json().get("response", "")
    logger.debug("Ollama raw response: %s", raw_text[:500])

    try:
        parsed = _extract_json(raw_text)
    except ValueError as exc:
        logger.error("Failed to parse Ollama response: %s", exc)
        raise HTTPException(status_code=502, detail="AI returned unparseable response. Try rephrasing.")

    conditions = _validate_conditions(parsed.get("conditions", []))
    explanation = str(parsed.get("explanation", ""))

    if not conditions:
        raise HTTPException(
            status_code=422,
            detail="AI could not generate valid conditions from the description. Try being more specific."
        )

    return SuggestResponse(conditions=conditions, explanation=explanation)
