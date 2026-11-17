# -*- coding: utf-8 -*-
"""Independent screening/hotspot refresh used by opportunity scans."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Shanghai")
REFRESH_SESSION_ID = "proactive:data_refresh"
DEFAULT_REFRESH_TIMES = ("08:40", "15:40")


def _parse_time(value: str) -> Optional[Tuple[int, int]]:
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


class ProactiveDataRefreshService:
    def __init__(
        self,
        config: Any = None,
        *,
        screening_service: Any = None,
        db: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config
        self._screening_service = screening_service
        self._db = db
        self._clock = clock or (lambda: datetime.now(MARKET_TZ))

    @property
    def config(self):
        if self._config is None:
            from src.config import get_config

            self._config = get_config()
        return self._config

    @property
    def screening_service(self):
        if self._screening_service is None:
            from src.services.screening_service import ScreeningService
            from src.storage import DatabaseManager

            self._screening_service = ScreeningService(
                config=self.config,
                db_manager=DatabaseManager.get_instance(),
            )
        return self._screening_service

    @property
    def db(self):
        if self._db is None:
            from src.storage import DatabaseManager

            self._db = DatabaseManager.get_instance()
        return self._db

    @property
    def now(self) -> datetime:
        current = self._clock()
        return current if current.tzinfo else current.replace(tzinfo=MARKET_TZ)

    def run(
        self,
        *,
        slot: str = "manual",
        now: Optional[datetime] = None,
        force: bool = True,
        strategy: Optional[str] = None,
        market: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if not bool(getattr(self.config, "agent_data_refresh_enabled", False)) and not force:
            return None
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        if not force and bool(getattr(self.config, "agent_proactive_market_guard", True)):
            from src.services.proactive_market_guard import is_trading_day

            if not is_trading_day("cn", current):
                return None

        slot = (slot or "manual").strip() or "manual"
        day = f"{current:%Y-%m-%d}"
        marker = f"data_refresh:{slot}"
        if not force and self.db.has_proactive_brief_run(day, marker):
            return None

        strategy = str(
            strategy
            or getattr(self.config, "agent_data_refresh_strategy", "capital_heat")
            or "capital_heat"
        )
        market = str(
            market or getattr(self.config, "agent_data_refresh_market", "cn") or "cn"
        )
        configured_max = max_results if max_results is not None else getattr(
            self.config, "agent_data_refresh_max_results", 20
        )
        max_results_value = max(1, min(int(configured_max or 20), 100))
        try:
            screening = self.screening_service.screen(
                strategy=strategy,
                market=market,
                max_results=max_results_value,
            )
        except Exception as exc:
            logger.warning("[DataRefresh] screening refresh failed: %s", exc)
            return {"status": "failed", "error": str(exc), "slot": slot}

        hotpots_count = 0
        try:
            hotspots = self.screening_service.hotspots(top=5, refresh=True)
            hotpots_count = len(hotspots.get("hotspots") or [])
        except Exception as exc:
            logger.warning("[DataRefresh] hotspot refresh failed: %s", exc)

        self.db.save_proactive_brief_run(day, marker, REFRESH_SESSION_ID, None)
        return {
            "status": "ok",
            "slot": slot,
            "candidates": len(screening.get("candidates") or []),
            "hotspots": hotpots_count,
        }

    def run_due(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        if not bool(getattr(self.config, "agent_data_refresh_enabled", False)):
            return None
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        slot = self._latest_due_slot(current)
        if slot is None:
            return None
        return self.run(slot=slot, now=current, force=False)

    def _latest_due_slot(self, current: datetime) -> Optional[str]:
        raw = getattr(self.config, "agent_data_refresh_times", None) or ",".join(DEFAULT_REFRESH_TIMES)
        slots = [item.strip() for item in str(raw).split(",") if _parse_time(item.strip())]
        if not slots:
            slots = list(DEFAULT_REFRESH_TIMES)
        due: Optional[str] = None
        for slot in slots:
            parsed = _parse_time(slot)
            if parsed is None:
                continue
            if (current.hour, current.minute) >= parsed:
                due = slot
        return due
