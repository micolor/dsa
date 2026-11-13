# -*- coding: utf-8 -*-
"""Observability for the proactive assistant background tasks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.storage import DatabaseManager

MARKET_TZ = ZoneInfo("Asia/Shanghai")


def _parse_time(value: Any, default: str) -> tuple[int, int]:
    text = str(value or default).strip()
    try:
        hour, minute = text.split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except (TypeError, ValueError):
        hour_i, minute_i = (8, 30) if default == "08:30" else (15, 30)
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return 8, 30
    return hour_i, minute_i


def _next_daily(times: Any, now: datetime, default: str) -> Optional[str]:
    raw = str(times or default)
    slots = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        parsed = _parse_time(text, default)
        slots.append((parsed[0], parsed[1]))
    if not slots:
        slots = [_parse_time("", default)]
    candidates = []
    for hour, minute in sorted(slots):
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates).isoformat() if candidates else None


def _next_weekly(day: int, time_value: Any, now: datetime) -> Optional[str]:
    hour, minute = _parse_time(time_value, "15:30")
    target = max(0, min(int(day), 6))
    delta = (target - now.weekday()) % 7
    candidate = (now + timedelta(days=delta)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate.isoformat()


class ProactiveStatusService:
    def __init__(self, config: Any = None, db: Optional[DatabaseManager] = None) -> None:
        self._config = config
        self.db = db or DatabaseManager.get_instance()

    @property
    def config(self):
        if self._config is None:
            from src.config import get_config

            self._config = get_config()
        return self._config

    def get_status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        current = now or datetime.now(MARKET_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        last_activity = self.db.get_proactive_type_last_activity()

        brief_enabled = bool(getattr(self.config, "agent_proactive_brief_enabled", False))
        event_enabled = bool(getattr(self.config, "agent_event_analysis_enabled", False))
        weekly_enabled = bool(getattr(self.config, "agent_weekly_review_enabled", False))
        opportunity_enabled = bool(getattr(self.config, "agent_opportunity_scan_enabled", False))
        event_interval = max(
            1, int(getattr(self.config, "agent_event_analysis_interval_minutes", 10) or 10)
        )

        return {
            "tasks": [
                {
                    "key": "daily_brief",
                    "label": "助手日报",
                    "enabled": brief_enabled,
                    "last_run": last_activity.get("daily_brief") or None,
                    "next_run": _next_daily(
                        getattr(self.config, "agent_proactive_brief_times", "08:30,15:30"),
                        current,
                        "08:30",
                    ) if brief_enabled else None,
                },
                {
                    "key": "event",
                    "label": "事件提醒",
                    "enabled": event_enabled,
                    "last_run": last_activity.get("event") or None,
                    "next_run": (
                        (current + timedelta(minutes=event_interval)).isoformat()
                        if event_enabled
                        else None
                    ),
                },
                {
                    "key": "weekly",
                    "label": "每周复盘",
                    "enabled": weekly_enabled,
                    "last_run": last_activity.get("weekly") or None,
                    "next_run": _next_weekly(
                        int(getattr(self.config, "agent_weekly_review_day", 4) or 4),
                        getattr(self.config, "agent_weekly_review_time", "15:30"),
                        current,
                    ) if weekly_enabled else None,
                },
                {
                    "key": "opportunity",
                    "label": "机会扫描",
                    "enabled": opportunity_enabled,
                    "last_run": last_activity.get("opportunity") or None,
                    "next_run": _next_daily(
                        getattr(self.config, "agent_opportunity_scan_times", "08:45,15:45"),
                        current,
                        "08:45",
                    ) if opportunity_enabled else None,
                },
            ]
        }
