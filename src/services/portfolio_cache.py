# -*- coding: utf-8 -*-
"""Short-TTL in-memory cache for portfolio snapshot computation (shared across requests).

The /snapshot and /risk endpoints each recompute the portfolio snapshot from trades.
This cache lets sequential repeats of the same view (same account/as-of/cost-method)
skip the replay within a short TTL. A module-level cache is used because the API
constructs a fresh service instance per request.

It is invalidated lazily via a dirty flag: portfolio write operations call
``mark_dirty()``, and the next snapshot read clears the cache before computing.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

TTL_SECONDS = 5.0

_SNAPSHOT: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
_DIRTY = False


def snapshot_key(
    account_id: Optional[int],
    as_of_iso: str,
    cost_method: str,
    include_realtime: bool,
) -> Tuple[Optional[int], str, str, bool]:
    return (account_id, as_of_iso, cost_method, bool(include_realtime))


def get_snapshot(key: Tuple[Any, ...]) -> Optional[Any]:
    global _DIRTY
    if _DIRTY:
        _SNAPSHOT.clear()
        _DIRTY = False
        return None
    entry = _SNAPSHOT.get(key)
    if entry is None:
        return None
    if time.monotonic() - entry[0] < TTL_SECONDS:
        return entry[1]
    _SNAPSHOT.pop(key, None)
    return None


def put_snapshot(key: Tuple[Any, ...], value: Any) -> None:
    now = time.monotonic()
    # 顺手清掉过期项，避免缓存无限增长
    expired = [k for k, (ts, _) in _SNAPSHOT.items() if now - ts >= TTL_SECONDS]
    for k in expired:
        _SNAPSHOT.pop(k, None)
    _SNAPSHOT[key] = (now, value)


def mark_dirty() -> None:
    global _DIRTY
    _DIRTY = True


def clear() -> None:
    global _DIRTY
    _SNAPSHOT.clear()
    _DIRTY = False
