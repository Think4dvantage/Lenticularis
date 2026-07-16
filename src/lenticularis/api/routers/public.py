"""
Public, unauthenticated routes.

This is the only unauthenticated surface in the rule set area.  Everything served
here is visible to the open internet, so payloads are deliberately narrow and
every field is an explicit decision to expose.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from lenticularis.api.errors import AppException
from lenticularis.database.db import get_db
from lenticularis.models.rules import PublicMapResponse
from lenticularis.services.public_map import build_anonymous_map_cached

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/rulesets/map", response_model=PublicMapResponse)
async def public_ruleset_map(request: Request, db: Session = Depends(get_db)):
    """
    Curated example rule sets for visitors who are not signed in.

    Shows rule sets that are both curated by an admin and published by their
    owner — neither alone is sufficient.  Served from a shared 60 s cache so the
    cost does not scale with the number of visitors.
    """
    influx = getattr(request.app.state, "influx", None)
    if influx is None:
        raise AppException(503, "INTERNAL_ERROR", "InfluxDB not available")

    virtual_members = getattr(request.app.state, "virtual_members", {})

    # InfluxDB's client is synchronous — calling it directly here would block the
    # event loop and stall every other request.
    payload = await asyncio.to_thread(
        build_anonymous_map_cached, db, influx, virtual_members
    )
    logger.info("[public] GET /api/public/rulesets/map → %d markers", len(payload.data))
    return payload
