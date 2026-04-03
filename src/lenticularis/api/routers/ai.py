"""
AI-assisted rule condition suggestions via a local Ollama instance.
"""
from __future__ import annotations

import json
import logging
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

Valid fields: wind_speed (km/h), wind_gust (km/h), wind_direction (degrees 0-360), temperature (°C), humidity (%), pressure (hPa), precipitation (mm), snow_depth (cm)
Valid operators: >, <, >=, <=, =, between, not_between, in_direction_range
  - "between" and "not_between" require value_a (lower) and value_b (upper)
  - "in_direction_range" requires value_a (from °) and value_b (to °), clockwise
Valid result_colour values: green (good/flyable), orange (marginal), red (dangerous/no-fly)

Return ONLY a JSON object with this exact structure, no explanation before or after:
{
  "conditions": [
    {"station_id": "...", "field": "...", "operator": "...", "value_a": 0, "value_b": null, "result_colour": "red"}
  ],
  "explanation": "one sentence summary"
}

Rules:
- Pick station_id from the provided list. If unsure, use the station that sounds geographically closest.
- For wind direction windows use in_direction_range with value_a=from and value_b=to (clockwise).
- For "no rain / no precipitation" use: field=precipitation, operator=>, value_a=0, result_colour=red
- Use result_colour=red for dangerous/no-fly conditions, orange for marginal, green for ideal.
- Sort conditions: wind first, then precipitation, then temperature.
"""


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

    # Build station list for the prompt (cap at 80 stations to keep prompt small)
    station_lines = "\n".join(
        f"  - {s.station_id}: {s.name} ({s.network})"
        for s in body.stations[:80]
    )
    user_prompt = f"Available stations:\n{station_lines}\n\nUser description:\n{body.description}"

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
