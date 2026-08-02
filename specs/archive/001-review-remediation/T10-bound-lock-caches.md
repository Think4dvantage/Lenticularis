# T10 — Bound + lock the in-memory caches; fix the ranking query client

**Severity:** Medium · **Phase:** 2 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md`, `08-operability.md`. LF line endings only.
- `cachetools` is NOT currently a dependency — this task uses only the stdlib (`threading`,
  `collections.OrderedDict`). Do not add a dependency. Exactly this task.

## Problems
1. `src/lenticularis/api/routers/stations.py` has two module-level dict caches — `_replay_cache`
   (line ~29) and `_accuracy_ranking_cache` (line ~383) — with **no size cap and no lock**. Keys are
   arbitrary query-param strings, so distinct `hours`/`forecast_hours` combos accumulate forever.
   They are also written from background warm-up threads and the forecast-invalidation callback
   concurrently → possible torn reads/lost entries.
2. The ranking docstring/comment says the cache TTL is 30 min but the constant
   `_ACCURACY_RANKING_CACHE_TTL_S = 300` is 5 min. Pick one and make code + comment agree.
3. `src/lenticularis/database/influx.py` `query_forecast_accuracy_ranking` runs a 90-day, all-station
   scan but uses `self._query_api` (the **10 s** default client, line ~1638), not the slow client.
   It will intermittently time out as data grows. (Note: `features.md`/the WIP doc claim a
   `_ranking_client` with a 300 s `ranking_query_timeout` was added — **it was not**; there is no
   such client/config. Reconcile by using the existing slow client.)

## Fix

### Caches — add a bounded, locked TTL cache helper
At the top of `stations.py`, add a tiny thread-safe bounded TTL cache and use it for both caches:
```python
import threading
from collections import OrderedDict

class _TTLCache:
    def __init__(self, ttl_s: float, maxsize: int = 64):
        self._ttl = ttl_s
        self._max = maxsize
        self._d: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.Lock()
    def get(self, key):
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            payload, stored_at = item
            if time.monotonic() - stored_at >= self._ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return payload
    def set(self, key, payload):
        with self._lock:
            self._d[key] = (payload, time.monotonic())
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)
    def invalidate_where(self, pred) -> int:
        with self._lock:
            keys = [k for k in self._d if pred(k)]
            for k in keys:
                self._d.pop(k, None)
            return len(keys)
```
Replace `_replay_cache`/`_accuracy_ranking_cache` dicts with `_TTLCache(_REPLAY_CACHE_TTL_S)` /
`_TTLCache(_ACCURACY_RANKING_CACHE_TTL_S)`. Update `get_replay`, `warm_replay_cache`,
`invalidate_forecast_replay_cache` (use `invalidate_where(lambda k: "|True|" in k)`), and
`get_forecast_accuracy_ranking` to use `.get()`/`.set()` and drop the manual age math. Preserve the
existing "skip caching when forecast expected but missing" guard.

### TTL docstring
Make the ranking comment/docstring say **5 minutes** to match the constant (or change the constant to
1800 if 30 min is intended — choose 5 min unless you find evidence the longer TTL was desired).

### Ranking query → slow client
In `influx.py` `query_forecast_accuracy_ranking`, change `self._query_api.query(...)` to
`self._slow_query_api.query(...)` (the 60 s client already exists in `__init__`). Add a log line
noting the query may be heavy.

## Acceptance criteria
- Hammering `/api/stations/replay` with many distinct `hours` values keeps the cache bounded
  (≤ maxsize entries); memory does not grow without limit.
- Replay/ranking still return cached payloads within TTL and recompute after it.
- `query_forecast_accuracy_ranking` uses the slow client and completes on a 90-day dataset without
  a 10 s timeout.
- Cache code + comments agree on the TTL value.
