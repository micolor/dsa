# -*- coding: utf-8 -*-
"""Trading-day / market-session guards for proactive assistant tasks."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.core.trading_calendar import MarketPhase, infer_market_phase

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo("Asia/Shanghai")


def _market_now(now: Optional[datetime]) -> datetime:
    current = now or datetime.now(MARKET_TZ)
    return current if current.tzinfo else current.replace(tzinfo=MARKET_TZ)


def is_trading_day(market: str = "cn", now: Optional[datetime] = None) -> bool:
    """Fail-open trading-day check; UNKNOWN calendars keep the existing behavior."""
    phase = infer_market_phase(market, _market_now(now))
    return phase != MarketPhase.NON_TRADING


def is_event_window(market: str = "cn", now: Optional[datetime] = None) -> bool:
    """Allow event analysis during the regular session and a short post-close tail."""
    current = _market_now(now)
    phase = infer_market_phase(market, current)
    if phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    }:
        return True
    if phase == MarketPhase.POSTMARKET:
        # Keep a short after-close window so closing events are still analysed.
        return current.hour < 16
    # PREMARKET, NON_TRADING and UNKNOWN: do not generate intraday events.
    return False
