# -*- coding: utf-8 -*-
"""Fast scheduled analysis: quote + a few indicators, no multi-stage pipeline.

Quick mode exists for scheduled tasks that only need a one-glance summary. It
never runs the stock analysis pipeline or the multi-agent workflow: it fetches a
realtime quote and a short daily-bar window, computes a few deterministic
indicators, and returns one line per symbol. OTC funds reuse the deterministic
fund NAV profile instead of stock indicators.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.config import Config, get_config

logger = logging.getLogger(__name__)

MAX_QUICK_SYMBOLS = 20
QUICK_WINDOW_DAYS = 30


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _price(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def _mean(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    chunk = list(values[-window:])
    return sum(chunk) / window


class QuickAnalysisService:
    """Deterministic, low-latency summarizer used by scheduled quick tasks."""

    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        fetcher: Any = None,
        fund_fetcher: Any = None,
        notifier: Any = None,
    ) -> None:
        self.config = config or get_config()
        self._fetcher = fetcher
        self._fund_fetcher = fund_fetcher
        self._notifier = notifier

    def _fetch_manager(self) -> Any:
        if self._fetcher is None:
            from data_provider.base import DataFetcherManager

            self._fetcher = DataFetcherManager()
        return self._fetcher

    def _funds(self) -> Any:
        if self._fund_fetcher is None:
            from data_provider.fund_fetcher import FundFetcher

            self._fund_fetcher = FundFetcher()
        return self._fund_fetcher

    def analyze(self, codes: Sequence[str], *, notify: bool = False) -> str:
        symbols = [str(code).strip() for code in codes if str(code).strip()][:MAX_QUICK_SYMBOLS]
        generated_at = self._now_label()
        if not symbols:
            return "快速分析未执行：没有可分析的代码。"

        lines: List[str] = []
        for code in symbols:
            try:
                line = self._analyze_fund(code) if code.lower().startswith("fund:") else self._analyze_stock(code)
            except Exception as exc:  # pragma: no cover - defensive per-symbol branch
                logger.warning("[QuickAnalysis] %s failed: %s", code, exc)
                line = f"- `{code}`：数据获取失败（{exc}）"
            lines.append(line)

        markdown = (
            f"### 快速行情摘要 · {generated_at}\n\n"
            + "\n".join(lines)
            + "\n\n> 快速模式只做行情与基础指标摘要，不运行完整分析流水线，不构成投资建议。"
        )
        if notify:
            self._send(markdown)
        return markdown

    def _analyze_stock(self, code: str) -> str:
        quote = self._fetch_manager().get_realtime_quote(code)
        daily, source = self._fetch_manager().get_daily_data(code, days=QUICK_WINDOW_DAYS)
        closes: List[float] = []
        volumes: List[float] = []
        if daily is not None and not getattr(daily, "empty", True):
            if "date" in daily.columns:
                daily = daily.sort_values("date")
            close_column = "close" if "close" in daily.columns else None
            volume_column = "volume" if "volume" in daily.columns else None
            if close_column:
                closes = [value for value in (_number(item) for item in daily[close_column].tolist()) if value is not None]
            if volume_column:
                volumes = [value for value in (_number(item) for item in daily[volume_column].tolist()) if value is not None]

        name = str(getattr(quote, "name", "") or "") if quote is not None else ""
        price = _number(getattr(quote, "price", None)) if quote is not None else None
        if price is None and closes:
            price = closes[-1]
        change_pct = _number(getattr(quote, "change_pct", None)) if quote is not None else None
        if change_pct is None and len(closes) >= 2:
            change_pct = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] else None

        ma5, ma10, ma20 = _mean(closes, 5), _mean(closes, 10), _mean(closes, 20)
        return_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else None
        volume_ratio = _number(getattr(quote, "volume_ratio", None)) if quote is not None else None
        if volume_ratio is None and len(volumes) >= 6:
            average = sum(volumes[-6:-1]) / 5
            volume_ratio = volumes[-1] / average if average else None

        if price is None:
            raise RuntimeError("行情与日线均不可用")
        if ma20 is None:
            trend = "数据不足"
            distance = None
        else:
            distance = (price / ma20 - 1) * 100 if ma20 else None
            if price >= ma20 and (ma5 is None or ma5 >= ma20):
                trend = "偏强"
            elif price < ma20:
                trend = "偏弱"
            else:
                trend = "震荡"

        display = f"{name}({code})" if name else f"`{code}`"
        parts = [
            f"- **{display}**：{_price(price)}（{_pct(change_pct)}）",
            f"MA5 {_price(ma5)} / MA10 {_price(ma10)} / MA20 {_price(ma20)}",
        ]
        if distance is not None:
            parts.append(f"距 MA20 {_pct(distance)}")
        if return_5d is not None:
            parts.append(f"5 日 {_pct(return_5d)}")
        if volume_ratio is not None:
            parts.append(f"量比 {volume_ratio:.2f}")
        parts.append(f"短线{trend}")
        if source:
            parts.append(f"数据源 {source}")
        return "，".join(parts) + "。"

    def _analyze_fund(self, code: str) -> str:
        from data_provider.fund_fetcher import strip_fund_prefix
        from src.services.fund_analysis import build_fund_report

        profile = self._funds().get_profile(strip_fund_prefix(code))
        report = build_fund_report(profile)
        metrics = report.get("metrics") or {}

        def fund_pct(value: Any) -> str:
            number = _number(value)
            return "—" if number is None else f"{number * 100:+.1f}%"

        return (
            f"- **{report.get('name') or code}（{report.get('code') or strip_fund_prefix(code)}）**："
            f"最新净值 {report.get('latest_nav') if report.get('latest_nav') is not None else '—'}"
            f"（{report.get('latest_nav_date') or '—'}），"
            f"近 1 年 {fund_pct(metrics.get('return_1y'))}，"
            f"最大回撤 {fund_pct(metrics.get('max_drawdown'))}；"
            f"{report.get('trend_prediction') or '数据不足'}，{report.get('operation_advice') or ''}"
        )

    def _send(self, markdown: str) -> None:
        try:
            if self._notifier is None:
                from src.notification import NotificationService

                self._notifier = NotificationService()
            send: Callable[[str], bool] = self._notifier.send
            send(markdown)
        except Exception as exc:  # pragma: no cover - notification is best-effort
            logger.warning("[QuickAnalysis] notification failed: %s", exc)

    @staticmethod
    def _now_label() -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
