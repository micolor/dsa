# -*- coding: utf-8 -*-
"""主动助手日报：把持仓、自选、告警和市场数据汇总成一条可读简报。

这是"主动辅助"的第一层：不依赖用户提问，也不调用 LLM。定时任务在盘前/盘后
生成一份确定性简报，写入 ``assistant`` 会话消息；数据源失败按 section 降级，
单只标的失败不影响整份日报。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Shanghai")
BRIEF_SESSION_ID = "proactive:daily_brief"
BRIEF_SESSION_TITLE = "助手日报"
MAX_ALERT_ROWS = 5

DEFAULT_BRIEF_TIMES = ("08:30", "15:30")


@dataclass(frozen=True)
class ProactiveBrief:
    """One generated brief and the marker used for idempotency."""

    generated_at: datetime
    slot: str
    markdown: str
    marker: str
    summary: str
    highlights: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProactiveBriefRun:
    brief: Optional[ProactiveBrief]
    message_id: Optional[int]
    skipped: bool = False


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _fmt_signed(value: Any, digits: int = 2, suffix: str = "%") -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:+.{digits}f}{suffix}"


def _normalize_code(code: Any) -> str:
    return str(code or "").strip()


def _parse_slot(value: str) -> Optional[Tuple[int, int]]:
    text = (value or "").strip()
    if not text or ":" not in text:
        return None
    hour_text, minute_text = text.split(":", 1)
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


class ProactiveBriefService:
    """Build and persist the proactive daily brief."""

    def __init__(
        self,
        config: Any = None,
        *,
        fetcher: Any = None,
        history_loader: Optional[Callable[[str, int], Tuple[Any, str]]] = None,
        portfolio_service: Any = None,
        alert_service: Any = None,
        db: Any = None,
        notifier: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._history_loader = history_loader
        self._portfolio_service = portfolio_service
        self._alert_service = alert_service
        self._db = db
        self._notifier = notifier
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
    def notifier(self):
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()
        return self._notifier

    @property
    def now(self) -> datetime:
        current = self._clock()
        return current if current.tzinfo else current.replace(tzinfo=MARKET_TZ)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self, *, slot: str = "manual", now: Optional[datetime] = None) -> ProactiveBrief:
        generated_at = now or self.now
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=MARKET_TZ)
        slot = (slot or "manual").strip() or "manual"

        positions, summary_failed = self._portfolio_section()
        watchlist = self._watchlist_codes()
        symbols = self._unique_symbols([p["symbol"] for p in positions] + watchlist)

        # Build in one pass so quote/history failures degrade per symbol.
        position_by_symbol = {p["symbol"]: p for p in positions}
        concurrency = max(
            1, min(int(getattr(self.config, "agent_proactive_scan_concurrency", 4) or 4), 16)
        )

        def load_row(symbol: str) -> Dict[str, Any]:
            return self._build_row(symbol, position_by_symbol.get(symbol))

        if concurrency > 1 and len(symbols) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows = list(pool.map(load_row, symbols))
        else:
            rows = [load_row(symbol) for symbol in symbols]

        market = self._market_indices()
        alerts = self._recent_alerts(generated_at)
        highlights = self._highlights(rows, alerts)
        markdown, summary = self._render(
            generated_at=generated_at,
            slot=slot,
            market=market,
            positions=positions,
            rows=rows,
            alerts=alerts,
            highlights=highlights,
            portfolio_failed=summary_failed,
        )
        marker = self._marker(generated_at, slot)
        return ProactiveBrief(
            generated_at=generated_at,
            slot=slot,
            markdown=markdown,
            marker=marker,
            summary=summary,
            highlights=highlights,
        )

    def run(
        self,
        *,
        slot: str = "manual",
        now: Optional[datetime] = None,
        session_id: str = BRIEF_SESSION_ID,
    ) -> ProactiveBriefRun:
        generated_at = now or self.now
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=MARKET_TZ)
        slot = (slot or "manual").strip() or "manual"

        # Scheduled slots are idempotent; manual runs are explicit and always append.
        if slot != "manual" and self.db.has_proactive_brief_run(
            f"{generated_at:%Y-%m-%d}", slot
        ):
            return ProactiveBriefRun(brief=None, message_id=None, skipped=True)

        brief = self.generate(slot=slot, now=generated_at)
        message_id = self.db.save_proactive_message(
            "daily_brief", f"{BRIEF_SESSION_TITLE} · {brief.generated_at:%Y-%m-%d}", brief.markdown
        )
        self.db.save_proactive_brief_run(
            f"{generated_at:%Y-%m-%d}", slot, BRIEF_SESSION_ID, message_id
        )
        self._notify(brief, message_id)
        logger.info("[ProactiveBrief] persisted slot=%s message_id=%s", brief.slot, message_id)
        return ProactiveBriefRun(brief=brief, message_id=message_id, skipped=False)

    def _notify(self, brief: ProactiveBrief, message_id: Optional[int] = None) -> bool:
        if not bool(getattr(self.config, "agent_proactive_brief_notify", False)):
            return False
        from src.services.proactive_notifications import in_quiet_hours, send_proactive_message

        if in_quiet_hours(self.config, self.now):
            logger.info("[ProactiveBrief] quiet hours; skip notification")
            return False
        sent = send_proactive_message(
            self.notifier,
            self.config,
            brief.markdown,
            severity="info",
            dedup_key=f"proactive-brief:{brief.generated_at:%Y-%m-%d}:{brief.slot}",
            cooldown_key="proactive_brief",
            item_id=message_id,
        )
        if not sent:
            logger.warning("[ProactiveBrief] notification not delivered slot=%s", brief.slot)
        return sent

    def run_due(self, now: Optional[datetime] = None) -> Optional[ProactiveBriefRun]:
        """Run the latest configured slot that is due and not generated today."""
        if not bool(getattr(self.config, "agent_proactive_brief_enabled", False)):
            return None
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        if bool(getattr(self.config, "agent_proactive_market_guard", True)):
            from src.services.proactive_market_guard import is_trading_day

            if not is_trading_day("cn", current):
                return None
        slots = self._configured_slots()
        due: Optional[str] = None
        for slot in slots:
            parsed = _parse_slot(slot)
            if parsed is None:
                continue
            slot_time = current.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
            if slot_time <= current:
                due = slot
        if due is None:
            return None
        # Check before building the brief: run_due ticks every few minutes.
        if self.db.has_proactive_brief_run(f"{current:%Y-%m-%d}", due):
            return None
        return self.run(slot=due, now=current)

    @staticmethod
    def _marker(generated_at: datetime, slot: str) -> str:
        return f"<!-- proactive-brief:{generated_at:%Y-%m-%d}:{slot} -->"

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------
    def _portfolio_section(self) -> Tuple[List[Dict[str, Any]], bool]:
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(include_realtime=True)
        except Exception as exc:
            logger.warning("[ProactiveBrief] portfolio snapshot failed: %s", exc)
            return [], True

        positions: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []) or []:
            for position in account.get("positions", []) or []:
                symbol = _normalize_code(position.get("symbol"))
                if not symbol:
                    continue
                positions.append({
                    "symbol": symbol,
                    "name": "",
                    "quantity": position.get("quantity"),
                    "market_value": position.get("market_value_base"),
                    "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
                    "last_price": position.get("last_price"),
                    "account": account.get("account_name"),
                })
        return positions, False

    def _watchlist_codes(self) -> List[str]:
        raw = getattr(self.config, "stock_list", None) or []
        limit = int(getattr(self.config, "agent_proactive_brief_watchlist_limit", 20) or 20)
        codes: List[str] = []
        for item in raw:
            code = _normalize_code(item)
            if code and code not in codes:
                codes.append(code)
            if len(codes) >= max(1, limit):
                break
        return codes

    @staticmethod
    def _unique_symbols(symbols: Iterable[str]) -> List[str]:
        seen: List[str] = []
        for symbol in symbols:
            if symbol and symbol not in seen:
                seen.append(symbol)
        return seen

    def _snapshot(self, symbol: str, position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        quote = None
        try:
            quote = self.fetcher.get_realtime_quote(symbol)
        except Exception as exc:
            logger.debug("[ProactiveBrief] quote failed for %s: %s", symbol, exc)

        history_row: Dict[str, Any] = {}
        try:
            df, _source = self.history_loader(symbol, 30)
            if df is not None and not df.empty:
                history_row = df.iloc[-1].to_dict()
        except Exception as exc:
            logger.debug("[ProactiveBrief] history failed for %s: %s", symbol, exc)

        name = str(getattr(quote, "name", "") or "").strip()
        if not name:
            try:
                name = str(self.fetcher.get_stock_name(symbol) or "").strip()
            except Exception:
                name = ""
        price = _number(getattr(quote, "price", None))
        if price is None:
            price = _number(history_row.get("close"))
        change_pct = _number(getattr(quote, "change_pct", None))
        if change_pct is None:
            change_pct = _number(history_row.get("pct_chg"))
        ma20 = _number(history_row.get("ma20"))
        volume_ratio = _number(getattr(quote, "volume_ratio", None))
        if volume_ratio is None:
            volume_ratio = _number(history_row.get("volume_ratio"))

        return {
            "symbol": symbol,
            "name": name or symbol,
            "price": price,
            "change_pct": change_pct,
            "ma20": ma20,
            "volume_ratio": volume_ratio,
            "position": position or {},
        }

    def _build_row(self, symbol: str, position: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return self._snapshot(symbol, position=position)

    def _market_indices(self) -> List[Dict[str, Any]]:
        try:
            indices = self.fetcher.get_main_indices(region="cn") or []
        except Exception as exc:
            logger.warning("[ProactiveBrief] market indices failed: %s", exc)
            return []
        return [item for item in indices if isinstance(item, dict)][:4]

    def _recent_alerts(self, now: datetime) -> List[Dict[str, Any]]:
        try:
            payload = self.alert_service.list_triggers(page=1, page_size=50)
        except Exception as exc:
            logger.warning("[ProactiveBrief] alert triggers failed: %s", exc)
            return []
        cutoff = now - timedelta(hours=24)
        recent: List[Dict[str, Any]] = []
        for item in payload.get("items", []) or []:
            triggered_at = str(item.get("triggered_at") or "")
            try:
                moment = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=MARKET_TZ)
            if moment >= cutoff:
                recent.append(item)
        recent.sort(key=lambda item: str(item.get("triggered_at") or ""), reverse=True)
        return recent[:MAX_ALERT_ROWS]

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _highlights(self, rows: List[Dict[str, Any]], alerts: List[Dict[str, Any]]) -> List[str]:
        highlights: List[str] = []
        for row in rows:
            label = row["name"] or row["symbol"]
            price = row.get("price")
            ma20 = row.get("ma20")
            change = row.get("change_pct")
            position = row.get("position") or {}
            if price is not None and ma20 is not None and price < ma20:
                highlights.append(f"{label} 位于 MA20 下方（{price:.2f} < {ma20:.2f}），留意趋势转弱")
            if change is not None and change <= -5:
                highlights.append(f"{label} 今日下跌 {change:.2f}%，检查是否触及止损")
            if change is not None and change >= 5:
                highlights.append(f"{label} 今日上涨 {change:.2f}%，关注是否放量延续")
            pnl = _number(position.get("unrealized_pnl_pct"))
            if pnl is not None and pnl <= -10:
                highlights.append(f"{label} 浮亏 {pnl:.2f}%，复核持仓逻辑与止损位")
            volume_ratio = row.get("volume_ratio")
            if volume_ratio is not None and volume_ratio >= 2:
                highlights.append(f"{label} 量比 {volume_ratio:.2f}，出现放量")
        if alerts:
            highlights.append(f"近 24 小时有 {len(alerts)} 条告警触发，详见下方告警区")
        if not highlights:
            highlights.append("暂无需要立即处理的事项，继续观察持仓与自选")
        return highlights[:8]

    def _render(
        self,
        *,
        generated_at: datetime,
        slot: str,
        market: List[Dict[str, Any]],
        positions: List[Dict[str, Any]],
        rows: List[Dict[str, Any]],
        alerts: List[Dict[str, Any]],
        highlights: List[str],
        portfolio_failed: bool,
    ) -> Tuple[str, str]:
        below_ma20 = sum(
            1 for row in rows
            if row.get("price") is not None and row.get("ma20") is not None
            and row["price"] < row["ma20"]
        )
        summary = (
            f"持仓 {len(positions)} 只、自选 {max(0, len(rows) - len(positions))} 只；"
            f"{below_ma20} 只位于 MA20 下方，近 24 小时 {len(alerts)} 条告警。"
        )
        if portfolio_failed:
            summary = f"持仓数据暂不可用；{summary}"

        display_time = generated_at.strftime("%H:%M") if slot == "manual" else slot
        lines: List[str] = [
            f"# {BRIEF_SESSION_TITLE} · {generated_at:%Y-%m-%d} {display_time}",
            "",
            f"**一句话：** {summary}",
            "",
            "## 市场",
        ]
        if market:
            for index in market:
                lines.append(
                    f"- {index.get('name') or index.get('code')}："
                    f"{_fmt(index.get('current'))}（{_fmt_signed(index.get('change_pct'))}）"
                )
        else:
            lines.append("- 市场指数暂不可用")

        lines.extend(["", "## 持仓"])
        if positions:
            lines.append("| 标的 | 现价 | 涨跌 | 浮盈亏 | MA20 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for row in rows:
                position = row.get("position") or {}
                if not position:
                    continue
                lines.append(
                    f"| {row['name']}（{row['symbol']}） | {_fmt(row.get('price'))} | "
                    f"{_fmt_signed(row.get('change_pct'))} | "
                    f"{_fmt_signed(position.get('unrealized_pnl_pct'))} | "
                    f"{self._ma20_status(row)} |"
                )
        else:
            lines.append("- 当前没有持仓。")

        watchlist_rows = [row for row in rows if not (row.get("position") or {})]
        notable = [
            row for row in watchlist_rows
            if (row.get("change_pct") is not None and abs(row["change_pct"]) >= 5)
            or (row.get("price") is not None and row.get("ma20") is not None and row["price"] < row["ma20"])
            or (row.get("volume_ratio") is not None and row["volume_ratio"] >= 2)
        ]
        lines.extend(["", "## 自选异动"])
        if notable:
            lines.append("| 标的 | 现价 | 涨跌 | 量比 | MA20 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for row in notable[:10]:
                lines.append(
                    f"| {row['name']}（{row['symbol']}） | {_fmt(row.get('price'))} | "
                    f"{_fmt_signed(row.get('change_pct'))} | {_fmt(row.get('volume_ratio'))} | "
                    f"{self._ma20_status(row)} |"
                )
        elif watchlist_rows:
            lines.append("- 自选暂无显著异动。")
        else:
            lines.append("- 未配置自选股。")

        lines.extend(["", "## 告警"])
        if alerts:
            for alert in alerts:
                triggered = str(alert.get("triggered_at") or "")
                moment = triggered[11:16] if len(triggered) >= 16 else triggered
                target = alert.get("target") or "—"
                reason = alert.get("reason") or alert.get("status") or "触发"
                lines.append(f"- {moment} {target}：{reason}")
        else:
            lines.append("- 近 24 小时无新告警。")

        lines.extend(["", "## 今日关注"])
        lines.extend(f"- {item}" for item in highlights)
        lines.extend([
            "",
            "---",
            "数据来源：DSA 实时行情、日线、持仓与告警。仅为辅助信息，不构成投资建议。",
        ])
        return "\n".join(lines), summary

    @staticmethod
    def _ma20_status(row: Dict[str, Any]) -> str:
        price = row.get("price")
        ma20 = row.get("ma20")
        if price is None or ma20 is None:
            return "—"
        return "上方" if price >= ma20 else "下方"

    def _configured_slots(self) -> List[str]:
        raw = getattr(self.config, "agent_proactive_brief_times", None) or ",".join(DEFAULT_BRIEF_TIMES)
        slots = [item.strip() for item in str(raw).split(",") if _parse_slot(item.strip())]
        return slots or list(DEFAULT_BRIEF_TIMES)
