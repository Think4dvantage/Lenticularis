"""
Reactive ruleset evaluation — dependency lookup and burst coalescing.

Pure, side-effect-free helpers backing the event-driven evaluation introduced in
``specs/009-reactive-ruleset-evaluation``.  The evaluation itself stays on
``CollectorScheduler`` (it needs the SMTP notifier, the Influx client and the
virtual-member map); this module only answers *which* rule sets an incoming batch
of station updates affects, and guards against evaluating one twice at once.

No I/O beyond the single reverse-lookup query, no Influx, no Pydantic — so both
halves are unit-testable against an in-memory session.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable, Optional

from sqlalchemy import distinct, or_, select

from lenticularis.database.models import RuleCondition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Station → rule set reverse lookup
# ---------------------------------------------------------------------------

def expand_station_cluster(
    station_ids: Iterable[str],
    virtual_members: Optional[dict[str, list[str]]] = None,
) -> set[str]:
    """
    Expand each reported station id to every id in its deduplication cluster.

    ``virtual_members`` maps ``canonical_id -> [member_ids…]`` (canonical first,
    multi-member clusters only) — see ``services/dedup.py``.  A pilot building a
    condition can only pick a *canonical* id, because the ruleset editor's station
    picker reads the deduplicated ``display_registry``.  So when a physical member
    reports, the rule set referencing its canonical id must still be found (FR-003).

    Expansion is deliberately in **both** directions — reported id → canonical, and
    canonical → all members — rather than just member → canonical.  Canonicality is
    priority-ranked (meteoswiss > slf > … > jfb), so adding a higher-priority station
    near an existing one *moves* the canonical id, leaving older conditions pointing
    at what is now a member.  Whole-cluster expansion covers that case; a one-way map
    would silently stop re-evaluating those rule sets.
    """
    ids = set(station_ids)
    if not virtual_members or not ids:
        return ids

    member_to_canonical: dict[str, str] = {
        member_id: canonical_id
        for canonical_id, members in virtual_members.items()
        for member_id in members
    }

    expanded = set(ids)
    for station_id in ids:
        canonical_id = member_to_canonical.get(station_id)
        if canonical_id is not None:
            expanded.add(canonical_id)
            expanded.update(virtual_members.get(canonical_id, ()))
    return expanded


def affected_ruleset_ids(
    db,
    station_ids: Iterable[str],
    virtual_members: Optional[dict[str, list[str]]] = None,
) -> set[str]:
    """
    Return the ids of every rule set with a condition referencing one of ``station_ids``.

    One ``SELECT DISTINCT`` for the whole batch — never a query per station (NFR-001,
    NFR-003).  A rule set with no conditions has no row here and is therefore never
    returned, which is exactly FR-009's "never triggered" without a special case.
    """
    ids = expand_station_cluster(station_ids, virtual_members)
    if not ids:
        return set()

    id_list = list(ids)
    rows = db.execute(
        select(distinct(RuleCondition.ruleset_id)).where(
            or_(
                RuleCondition.station_id.in_(id_list),
                RuleCondition.station_b_id.in_(id_list),
            )
        )
    ).scalars().all()
    return set(rows)


# ---------------------------------------------------------------------------
# In-flight coalescing (FR-007)
# ---------------------------------------------------------------------------
#
# Unlike the module-level caches ``04-constraints.md`` requires a max size on, this
# set is self-draining: every claim is released in a ``finally``, so it can never hold
# more entries than there are rule sets being evaluated concurrently.  No bound needed.

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def try_claim(ruleset_id: str) -> bool:
    """
    Claim ``ruleset_id`` for evaluation; ``False`` if an evaluation is already running.

    A refused claim is **skipped, not queued** — if two collector runs complete moments
    apart and both touch the same rule set, the second is dropped.  That is the point of
    FR-007, not a missed update: the in-flight evaluation is reading the same freshly
    written data, and the next event for either network re-evaluates anyway.
    """
    with _in_flight_lock:
        if ruleset_id in _in_flight:
            return False
        _in_flight.add(ruleset_id)
        return True


def release(ruleset_id: str) -> None:
    """Release a claim taken by :func:`try_claim`. Always call from a ``finally``."""
    with _in_flight_lock:
        _in_flight.discard(ruleset_id)
