# T18 — Fix silently-swallowed exceptions

**Severity:** Medium · **Phase:** 3 · **Model tier:** Trivial

## Ground Rules
- Read `.ai/instructions/08-operability.md` ("Never catch-and-ignore exceptions"). LF line endings
  only. Exactly this task.

## Problem
Three sites swallow exceptions with no log line, violating the operability doctrine:
- `src/lenticularis/api/main.py` ~line 227: `except Exception: pass  # Registry update is best-effort`
  (inside `_patch_scheduler_registry`). **If T14 has already been done, this code has moved into the
  registry-updater callback — fix it there instead.**
- `src/lenticularis/api/routers/ai.py` ~lines 377–378 and ~384–385:
  `except json.JSONDecodeError: pass`.

## Fix
Replace each silent `pass` with a logged line that includes context. Keep the control flow
(best-effort behavior) — only add visibility.
```python
# main.py (or the T14 callback)
except Exception:
    logger.warning("Registry update after collector run failed (best-effort)", exc_info=True)
```
```python
# ai.py — for each JSONDecodeError swallow
except json.JSONDecodeError:
    logger.warning("[Lenti:ai] could not parse JSON from model output, skipping", exc_info=True)
```
Use the module's existing `logger` (add `logger = logging.getLogger(__name__)` only if the file
lacks one — `ai.py` and `main.py` already have loggers).

Leave the broad `except Exception` blocks in `influx.py`/collectors that already log-and-return a
safe default — those are acceptable query boundaries and out of scope here.

## Acceptance criteria
- None of the three sites swallow silently; each emits a `WARNING` with `exc_info=True`.
- Behavior is otherwise unchanged (the operations remain best-effort and do not raise).
- `grep -nE "except.*:\s*pass" src/` shows no remaining bare/silent swallow at these sites.
