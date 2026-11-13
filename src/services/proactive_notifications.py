# -*- coding: utf-8 -*-
"""Shared quiet-hours policy for proactive assistant pushes."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo("Asia/Shanghai")
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _parse_clock(value: str) -> Optional[Tuple[int, int]]:
    text = (value or "").strip()
    if ":" not in text:
        return None
    hour, minute = text.split(":", 1)
    try:
        hour_i, minute_i = int(hour), int(minute)
    except ValueError:
        return None
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return None
    return hour_i, minute_i


def parse_quiet_hours(value: Any) -> Optional[Tuple[int, int]]:
    """Parse ``HH:MM-HH:MM`` and return start/end minutes."""
    text = str(value or "").strip()
    if not text:
        return None
    if "-" not in text:
        return None
    start_text, end_text = text.split("-", 1)
    start = _parse_clock(start_text)
    end = _parse_clock(end_text)
    if start is None or end is None:
        return None
    return start[0] * 60 + start[1], end[0] * 60 + end[1]


def in_quiet_hours(config: Any, now: Optional[datetime] = None) -> bool:
    """Return True when proactive pushes should be skipped.

    ``AGENT_PROACTIVE_QUIET_HOURS`` accepts a single range such as
    ``22:00-08:00``. Crossing midnight is supported. Empty/invalid values mean
    "never quiet" so the feature is opt-in.
    """
    parsed = parse_quiet_hours(getattr(config, "agent_proactive_quiet_hours", ""))
    if parsed is None:
        return False
    current = now or datetime.now(MARKET_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MARKET_TZ)
    current_minutes = current.hour * 60 + current.minute
    start, end = parsed
    if start == end:
        return False
    if start < end:
        return start <= current_minutes < end
    return current_minutes >= start or current_minutes < end


def severity_allowed(config: Any, severity: str) -> bool:
    minimum = str(getattr(config, "agent_proactive_notify_min_severity", "info") or "info").lower()
    current = str(severity or "info").lower()
    return _SEVERITY_ORDER.get(current, 0) >= _SEVERITY_ORDER.get(minimum, 0)


def _with_deep_link(config: Any, content: str, item_id: Optional[int]) -> str:
    base_url = str(getattr(config, "agent_proactive_notify_base_url", "") or "").strip()
    if not base_url:
        return content
    base = base_url.rstrip("/")
    target = f"{base}/?inbox={item_id}" if item_id is not None else f"{base}/"
    return f"{content.rstrip()}\n\n[在站内提醒查看]({target})"


def send_proactive_message(
    notifier: Any,
    config: Any,
    content: str,
    *,
    severity: str = "info",
    dedup_key: str = "",
    cooldown_key: str = "",
    item_id: Optional[int] = None,
) -> bool:
    """Send one proactive message with severity filter, routing, retry and deep link."""
    if not severity_allowed(config, severity):
        logger.info("[ProactiveNotify] severity %s below threshold; skip", severity)
        return False
    route = str(getattr(config, "agent_proactive_notify_route", "") or "").strip() or None
    retries = max(0, int(getattr(config, "agent_proactive_notify_retries", 1) or 0))
    payload = _with_deep_link(config, content, item_id)
    for attempt in range(retries + 1):
        try:
            sent = notifier.send(
                payload,
                route_type=route,
                severity=severity,
                dedup_key=dedup_key or None,
                cooldown_key=cooldown_key or None,
            )
            if sent:
                return True
        except Exception as exc:
            logger.warning("[ProactiveNotify] attempt %s failed: %s", attempt + 1, exc)
    return False
