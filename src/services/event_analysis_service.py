# -*- coding: utf-8 -*-
"""事件驱动 Agent 分析：把盘中的异常事件变成可读的风险/机会提示。

这不是另一条个股分析流水线，而是单 Agent 的短分析：

1. 采集事件：告警触发、自选/持仓跌破 MA20、单日大幅波动、放量、浮亏扩大；
2. 对于新事件（``event_agent_runs`` 去重），预取行情/日线/持仓数据；
3. 调用一次 LLM，输出 3-5 句结论，不开放工具调用；
4. 写入「事件提醒」会话，并按配置推送通知。

默认关闭；开启后由运行时调度器定期扫描。所有分析仅供辅助，不自动交易。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Shanghai")
EVENT_SESSION_ID = "proactive:event_alerts"
EVENT_SESSION_TITLE = "事件提醒"

_EVENT_PRIORITY = {
    "alert": 0,
    "position_loss": 1,
    "big_move": 2,
    "volume_spike": 3,
    "below_ma20": 4,
}


@dataclass(frozen=True)
class DetectedEvent:
    event_key: str
    stock_code: str
    event_type: str
    title: str
    detail: str
    severity: str
    snapshot: Dict[str, Any] = field(default_factory=dict)
    position: Optional[Dict[str, Any]] = None
    triggered_at: str = ""


@dataclass(frozen=True)
class EventAnalysisResult:
    event: DetectedEvent
    content: str
    message_id: Optional[int]
    notified: bool = False


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}{suffix}"


def _fmt_signed(value: Any, digits: int = 2, suffix: str = "%") -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+.{digits}f}{suffix}"


class EventAnalysisService:
    """Detect and analyse event-driven alerts with a single LLM call."""

    def __init__(
        self,
        config: Any = None,
        *,
        fetcher: Any = None,
        history_loader: Optional[Callable[[str, int], Tuple[Any, str]]] = None,
        portfolio_service: Any = None,
        alert_service: Any = None,
        db: Any = None,
        llm: Any = None,
        notifier: Any = None,
        news_loader: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._history_loader = history_loader
        self._portfolio_service = portfolio_service
        self._alert_service = alert_service
        self._db = db
        self._llm = llm
        self._notifier = notifier
        self._news_loader = news_loader
        self._clock = clock or (lambda: datetime.now(MARKET_TZ))

    # ------------------------------------------------------------------
    # Lazy dependencies
    # ------------------------------------------------------------------
    @property
    def config(self):
        if self._config is None:
            from src.config import get_config

            self._config = get_config()
        return self._config

    @property
    def fetcher(self):
        if self._fetcher is None:
            from data_provider import DataFetcherManager

            self._fetcher = DataFetcherManager()
        return self._fetcher

    @property
    def history_loader(self):
        if self._history_loader is None:
            from src.services.history_loader import load_history_df

            self._history_loader = load_history_df
        return self._history_loader

    @property
    def portfolio_service(self):
        if self._portfolio_service is None:
            from src.services.portfolio_service import PortfolioService

            self._portfolio_service = PortfolioService()
        return self._portfolio_service

    @property
    def alert_service(self):
        if self._alert_service is None:
            from src.services.alert_service import AlertService

            self._alert_service = AlertService()
        return self._alert_service

    @property
    def db(self):
        if self._db is None:
            from src.storage import DatabaseManager

            self._db = DatabaseManager.get_instance()
        return self._db

    @property
    def llm(self):
        if self._llm is None:
            from src.agent.llm_adapter import LLMToolAdapter

            self._llm = LLMToolAdapter(self.config)
        return self._llm

    @property
    def notifier(self):
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()
        return self._notifier

    @property
    def news_loader(self):
        if self._news_loader is None:
            def load_news(symbol: str, name: str) -> List[Dict[str, Any]]:
                from src.search_service import get_search_service

                service = get_search_service()
                if not service.is_stock_news_available:
                    return []
                response = service.search_stock_news(symbol, name, max_results=3)
                if not response.success:
                    return []
                return [
                    {
                        "title": item.title,
                        "snippet": item.snippet,
                        "source": item.source,
                        "published_date": item.published_date,
                    }
                    for item in (response.results or [])[:3]
                ]

            self._news_loader = load_news
        return self._news_loader

    @property
    def now(self) -> datetime:
        current = self._clock()
        return current if current.tzinfo else current.replace(tzinfo=MARKET_TZ)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def collect_events(
        self,
        *,
        now: Optional[datetime] = None,
        lookback_minutes: Optional[int] = None,
    ) -> List[DetectedEvent]:
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)

        positions = self._positions()
        symbols = self._symbols(positions)
        snapshot_cache: Dict[str, Dict[str, Any]] = {}
        events: List[DetectedEvent] = []
        seen: set[str] = set()

        def add(event: DetectedEvent) -> None:
            if event.event_key in seen or self.db.has_event_agent_run(event.event_key):
                return
            seen.add(event.event_key)
            events.append(event)

        concurrency = max(
            1, min(int(getattr(self.config, "agent_proactive_scan_concurrency", 4) or 4), 16)
        )

        def load_snapshot(symbol: str) -> Tuple[str, Dict[str, Any]]:
            return symbol, self._snapshot(symbol)

        if concurrency > 1 and len(symbols) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                loaded = list(pool.map(load_snapshot, symbols))
        else:
            loaded = [load_snapshot(symbol) for symbol in symbols]

        for symbol, snapshot in loaded:
            snapshot_cache[symbol] = snapshot
            position = positions.get(symbol)
            for event in self._watchlist_events(symbol, snapshot, position, current):
                add(event)

        for event in self._alert_events(current, lookback_minutes, snapshot_cache, positions):
            add(event)

        events.sort(key=lambda event: (_EVENT_PRIORITY.get(event.event_type, 99), event.stock_code))
        return events

    def _positions(self) -> Dict[str, Dict[str, Any]]:
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(include_realtime=False)
        except Exception as exc:
            logger.warning("[EventAnalysis] portfolio snapshot failed: %s", exc)
            return {}
        positions: Dict[str, Dict[str, Any]] = {}
        for account in snapshot.get("accounts", []) or []:
            for position in account.get("positions", []) or []:
                symbol = str(position.get("symbol") or "").strip()
                if symbol:
                    positions[symbol] = position
        return positions

    def _symbols(self, positions: Dict[str, Dict[str, Any]]) -> List[str]:
        watchlist = [
            str(item).strip()
            for item in (getattr(self.config, "stock_list", None) or [])
            if str(item).strip()
        ]
        limit = int(getattr(self.config, "agent_proactive_brief_watchlist_limit", 20) or 20)
        watchlist = watchlist[:max(1, limit)]
        symbols: List[str] = []
        for symbol in list(positions) + watchlist:
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols

    def _snapshot(self, symbol: str) -> Dict[str, Any]:
        quote = None
        try:
            quote = self.fetcher.get_realtime_quote(symbol)
        except Exception as exc:
            logger.debug("[EventAnalysis] quote failed for %s: %s", symbol, exc)
        row: Dict[str, Any] = {}
        try:
            df, _source = self.history_loader(symbol, 30)
            if df is not None and not df.empty:
                row = df.iloc[-1].to_dict()
        except Exception as exc:
            logger.debug("[EventAnalysis] history failed for %s: %s", symbol, exc)

        name = str(getattr(quote, "name", "") or "").strip()
        if not name:
            try:
                name = str(self.fetcher.get_stock_name(symbol) or "").strip()
            except Exception:
                name = ""
        price = _number(getattr(quote, "price", None))
        if price is None:
            price = _number(row.get("close"))
        change_pct = _number(getattr(quote, "change_pct", None))
        if change_pct is None:
            change_pct = _number(row.get("pct_chg"))
        volume_ratio = _number(getattr(quote, "volume_ratio", None))
        if volume_ratio is None:
            volume_ratio = _number(row.get("volume_ratio"))
        return {
            "symbol": symbol,
            "name": name or symbol,
            "price": price,
            "change_pct": change_pct,
            "pre_close": _number(getattr(quote, "pre_close", None)),
            "ma5": _number(row.get("ma5")),
            "ma10": _number(row.get("ma10")),
            "ma20": _number(row.get("ma20")),
            "volume_ratio": volume_ratio,
        }

    def _watchlist_events(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        now: datetime,
    ) -> List[DetectedEvent]:
        name = snapshot.get("name") or symbol
        price = snapshot.get("price")
        ma20 = snapshot.get("ma20")
        change = snapshot.get("change_pct")
        volume_ratio = snapshot.get("volume_ratio")
        day = now.strftime("%Y-%m-%d")
        events: List[DetectedEvent] = []

        pnl = _number((position or {}).get("unrealized_pnl_pct"))
        if position is not None and pnl is not None and pnl <= -10:
            events.append(DetectedEvent(
                event_key=f"{day}:position_loss:{symbol}",
                stock_code=symbol,
                event_type="position_loss",
                title=f"{name}（{symbol}）浮亏 {pnl:.2f}%",
                detail=f"持仓浮亏 {pnl:.2f}%，现价 {_fmt(price)}，MA20 {_fmt(ma20)}。",
                severity="warning",
                snapshot=snapshot,
                position=position,
                triggered_at=now.isoformat(),
            ))

        if change is not None and abs(change) >= 5:
            direction = "up" if change > 0 else "down"
            events.append(DetectedEvent(
                event_key=f"{day}:big_move:{direction}:{symbol}",
                stock_code=symbol,
                event_type="big_move",
                title=f"{name}（{symbol}）单日{'上涨' if change > 0 else '下跌'} {abs(change):.2f}%",
                detail=(
                    f"现价 {_fmt(price)}，涨跌 {_fmt_signed(change)}，量比 {_fmt(volume_ratio)}，"
                    f"MA20 {_fmt(ma20)}。"
                ),
                severity="warning",
                snapshot=snapshot,
                position=position,
                triggered_at=now.isoformat(),
            ))

        if price is not None and ma20 is not None and price < ma20:
            events.append(DetectedEvent(
                event_key=f"{day}:below_ma20:{symbol}",
                stock_code=symbol,
                event_type="below_ma20",
                title=f"{name}（{symbol}）跌破 MA20",
                detail=f"现价 {_fmt(price)} < MA20 {_fmt(ma20)}，涨跌 {_fmt_signed(change)}。",
                severity="warning",
                snapshot=snapshot,
                position=position,
                triggered_at=now.isoformat(),
            ))

        if volume_ratio is not None and volume_ratio >= 2:
            events.append(DetectedEvent(
                event_key=f"{day}:volume_spike:{symbol}",
                stock_code=symbol,
                event_type="volume_spike",
                title=f"{name}（{symbol}）放量，量比 {volume_ratio:.2f}",
                detail=f"量比 {volume_ratio:.2f}，现价 {_fmt(price)}，涨跌 {_fmt_signed(change)}。",
                severity="info",
                snapshot=snapshot,
                position=position,
                triggered_at=now.isoformat(),
            ))
        return events

    def _alert_events(
        self,
        now: datetime,
        lookback_minutes: Optional[int],
        snapshot_cache: Dict[str, Dict[str, Any]],
        positions: Dict[str, Dict[str, Any]],
    ) -> List[DetectedEvent]:
        minutes = lookback_minutes
        if minutes is None:
            interval = int(getattr(self.config, "agent_event_analysis_interval_minutes", 10) or 10)
            minutes = max(interval * 2, 20)
        cutoff = now - timedelta(minutes=minutes)
        try:
            payload = self.alert_service.list_triggers(page=1, page_size=50)
        except Exception as exc:
            logger.warning("[EventAnalysis] alert triggers failed: %s", exc)
            return []

        events: List[DetectedEvent] = []
        for trigger in payload.get("items", []) or []:
            triggered_at = str(trigger.get("triggered_at") or "")
            try:
                moment = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=MARKET_TZ)
            if moment < cutoff:
                continue
            symbol = str(trigger.get("target") or "").strip()
            if not symbol or symbol.upper() == "MARKET":
                continue
            snapshot = snapshot_cache.get(symbol)
            if snapshot is None:
                snapshot = self._snapshot(symbol)
                snapshot_cache[symbol] = snapshot
            reason = str(trigger.get("reason") or trigger.get("status") or "触发")
            observed = trigger.get("observed_value")
            threshold = trigger.get("threshold")
            events.append(DetectedEvent(
                event_key=f"alert:{trigger.get('id')}",
                stock_code=symbol,
                event_type="alert",
                title=f"告警：{snapshot.get('name') or symbol}（{symbol}）{reason}",
                detail=(
                    f"观测值 {observed if observed is not None else '—'}，"
                    f"阈值 {threshold if threshold is not None else '—'}，触发时间 {triggered_at}。"
                ),
                severity="warning",
                snapshot=snapshot,
                position=positions.get(symbol),
                triggered_at=triggered_at,
            ))
        return events

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def run_once(
        self,
        *,
        now: Optional[datetime] = None,
        max_events: Optional[int] = None,
        force: bool = False,
    ) -> List[EventAnalysisResult]:
        if not bool(getattr(self.config, "agent_event_analysis_enabled", False)) and not force:
            return []
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)

        if not force and bool(getattr(self.config, "agent_proactive_market_guard", True)):
            from src.services.proactive_market_guard import is_event_window, is_trading_day

            if not is_trading_day("cn", current) or not is_event_window("cn", current):
                return []

        daily_limit = int(getattr(self.config, "agent_event_analysis_max_per_day", 20) or 0)
        used_today = 0
        if not force and daily_limit > 0:
            used_today = self.db.count_event_agent_runs_for_date(f"{current:%Y-%m-%d}")
            if used_today >= daily_limit:
                logger.info(
                    "[EventAnalysis] daily LLM budget reached (%s/%s); skip",
                    used_today,
                    daily_limit,
                )
                return []

        lookback = 24 * 60 if force else None
        events = self.collect_events(now=current, lookback_minutes=lookback)
        limit = max_events
        if limit is None:
            limit = int(getattr(self.config, "agent_event_analysis_max_per_run", 3) or 3)
        if not force and daily_limit > 0:
            limit = min(limit, max(0, daily_limit - used_today))
        limit = max(0, min(int(limit), 10))
        if limit == 0:
            return []

        results: List[EventAnalysisResult] = []
        for event in events[:limit]:
            content = self._analyze(event)
            if not content:
                logger.warning("[EventAnalysis] no analysis for event=%s", event.event_key)
                continue
            message_id = self._persist(event, content)
            self.db.save_event_agent_run(
                event.event_key, event.stock_code, event.event_type, message_id
            )
            results.append(EventAnalysisResult(
                event=event,
                content=content,
                message_id=message_id,
                notified=False,
            ))

        if not results:
            return results

        from dataclasses import replace

        if bool(getattr(self.config, "agent_event_analysis_digest_notify", False)):
            markdown = "\n\n".join(
                f"## {item.event.title}\n\n{item.content}\n\n---\n{item.event.detail}"
                for item in results
            )
            severity = "warning" if any(item.event.severity == "warning" for item in results) else "info"
            notified = self._notify_digest(results, markdown, severity)
            return [replace(item, notified=notified) for item in results]

        return [
            replace(item, notified=self._notify(item.event, item.content, item.message_id))
            for item in results
        ]

    def _analyze(self, event: DetectedEvent) -> str:
        try:
            from src.services.risk_profile import from_config, prompt_section

            profile = from_config(self.config)
            profile_payload = profile.as_dict()
            profile_section = prompt_section(profile)
        except Exception:
            profile_payload = None
            profile_section = ""

        news: List[Dict[str, Any]] = []
        if bool(getattr(self.config, "agent_event_analysis_news_enabled", True)):
            try:
                news = self.news_loader(
                    event.stock_code,
                    str(event.snapshot.get("name") or event.stock_code),
                ) or []
            except Exception as exc:
                logger.debug("[EventAnalysis] news lookup failed for %s: %s", event.stock_code, exc)

        payload = {
            "event": {
                "type": event.event_type,
                "title": event.title,
                "detail": event.detail,
                "severity": event.severity,
            },
            "market_data": event.snapshot,
            "position": event.position or None,
            "risk_profile": profile_payload,
            "news": news,
        }
        system_prompt = (
            "你是 DSA 的盘中事件分析助手。根据给定的事件和真实数据，用中文输出 3-5 句：\n"
            "1. 事件是什么、是否值得重视；\n"
            "2. 对趋势或持仓的主要风险/机会；\n"
            "3. 接下来应关注的一个动作或观察点。\n"
            "如果 payload 带 news，仅结合其中真实存在的标题/摘要，不要扩展猜测；没有 news 时不要编造新闻。"
            "只使用给定数据；数据不足时直说，不要编造。不要给出确定性买卖指令，不要写免责声明。"
        )
        if profile_section:
            system_prompt = f"{system_prompt}\n\n{profile_section}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        timeout = int(getattr(self.config, "agent_event_analysis_timeout_s", 60) or 60)
        try:
            response = self.llm.call_completion(
                messages,
                temperature=0.2,
                max_tokens=500,
                timeout=max(10, timeout),
            )
        except Exception as exc:
            logger.warning("[EventAnalysis] LLM call failed for %s: %s", event.event_key, exc)
            return ""
        content = str(getattr(response, "content", "") or "").strip()
        provider = str(getattr(response, "provider", "") or "")
        if provider == "error" or not content:
            logger.warning("[EventAnalysis] LLM returned no usable content for %s", event.event_key)
            return ""
        return content

    def _persist(self, event: DetectedEvent, content: str) -> Optional[int]:
        markdown = (
            f"## {event.title}\n\n{content}\n\n"
            f"---\n{event.detail}\n事件时间：{event.triggered_at or self.now.isoformat()}"
        )
        return self.db.save_proactive_message("event", event.title, markdown)

    def _notify(self, event: DetectedEvent, content: str, message_id: Optional[int] = None) -> bool:
        if not bool(getattr(self.config, "agent_event_analysis_notify", False)):
            return False
        from src.services.proactive_notifications import in_quiet_hours

        if in_quiet_hours(self.config, self.now):
            logger.info("[EventAnalysis] quiet hours; skip notification for %s", event.event_key)
            return False
        from src.services.proactive_notifications import send_proactive_message

        markdown = f"## {event.title}\n\n{content}\n\n---\n{event.detail}"
        return send_proactive_message(
            self.notifier,
            self.config,
            markdown,
            severity="warning" if event.severity == "warning" else "info",
            dedup_key=f"event-agent:{event.event_key}",
            cooldown_key="event_agent",
            item_id=message_id,
        )

    def _notify_digest(
        self,
        results: List[EventAnalysisResult],
        markdown: str,
        severity: str,
    ) -> bool:
        if not bool(getattr(self.config, "agent_event_analysis_notify", False)):
            return False
        from src.services.proactive_notifications import in_quiet_hours, send_proactive_message

        if in_quiet_hours(self.config, self.now):
            return False
        return send_proactive_message(
            self.notifier,
            self.config,
            markdown,
            severity=severity,
            dedup_key=f"event-agent-digest:{self.now:%Y-%m-%d}",
            cooldown_key="event_agent",
        )
