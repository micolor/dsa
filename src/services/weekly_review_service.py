# -*- coding: utf-8 -*-
"""Weekly review: what worked, what failed, and what to watch next week."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src.services.risk_profile import from_config, portfolio_warnings_for_accounts

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Shanghai")
WEEKLY_SESSION_ID = "proactive:weekly_review"
WEEKLY_SESSION_TITLE = "每周复盘"
WEEKLY_SLOT = "weekly"


@dataclass(frozen=True)
class WeeklyReview:
    generated_at: datetime
    week_key: str
    summary: str
    markdown: str
    message_id: Optional[int] = None
    notified: bool = False


class WeeklyReviewService:
    def __init__(
        self,
        config: Any = None,
        *,
        fetcher: Any = None,
        history_loader: Optional[Callable[[str, int], Tuple[Any, str]]] = None,
        portfolio_service: Any = None,
        risk_service: Any = None,
        outcome_service: Any = None,
        alert_service: Any = None,
        db: Any = None,
        notifier: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._history_loader = history_loader
        self._portfolio_service = portfolio_service
        self._risk_service = risk_service
        self._outcome_service = outcome_service
        self._alert_service = alert_service
        self._db = db
        self._notifier = notifier
        self._clock = clock or (lambda: datetime.now(MARKET_TZ))

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
    def risk_service(self):
        if self._risk_service is None:
            from src.services.portfolio_risk_service import PortfolioRiskService

            self._risk_service = PortfolioRiskService(
                portfolio_service=self.portfolio_service,
                config=self.config,
            )
        return self._risk_service

    @property
    def outcome_service(self):
        if self._outcome_service is None:
            from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService

            self._outcome_service = DecisionSignalOutcomeService()
        return self._outcome_service

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
    # Generation
    # ------------------------------------------------------------------
    def generate(self, now: Optional[datetime] = None) -> WeeklyReview:
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        week_start = current - timedelta(days=7)
        week_key = self._week_key(current)

        snapshot: Dict[str, Any] = {}
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(include_realtime=True)
        except Exception as exc:
            logger.warning("[WeeklyReview] portfolio snapshot failed: %s", exc)

        risk_report: Dict[str, Any] = {}
        try:
            risk_report = self.risk_service.get_risk_report()
        except Exception as exc:
            logger.warning("[WeeklyReview] portfolio risk failed: %s", exc)

        profile = from_config(self.config)
        warnings = portfolio_warnings_for_accounts(self.config, snapshot, risk_report)
        outcomes = self._outcome_stats(week_start, current)
        alerts = self._recent_alerts(week_start)
        movers = self._weekly_movers(snapshot)

        markdown, summary = self._render(
            current=current,
            week_start=week_start,
            profile=profile,
            snapshot=snapshot,
            warnings=warnings,
            outcomes=outcomes,
            alerts=alerts,
            movers=movers,
        )
        return WeeklyReview(
            generated_at=current,
            week_key=week_key,
            summary=summary,
            markdown=markdown,
        )

    def run(self, now: Optional[datetime] = None, *, force: bool = True) -> Optional[WeeklyReview]:
        current = now or self.now
        week_key = self._week_key(current)
        if not force and self.db.has_proactive_brief_run(week_key, WEEKLY_SLOT):
            return None
        review = self.generate(current)
        message_id = self.db.save_proactive_message(
            "weekly", f"{WEEKLY_SESSION_TITLE} · {current:%Y-%m-%d}", review.markdown
        )
        self.db.save_proactive_brief_run(week_key, WEEKLY_SLOT, WEEKLY_SESSION_ID, message_id)
        notified = self._notify(review, message_id)
        from dataclasses import replace

        return replace(review, message_id=message_id, notified=notified)

    def run_due(self, now: Optional[datetime] = None) -> Optional[WeeklyReview]:
        if not bool(getattr(self.config, "agent_weekly_review_enabled", False)):
            return None
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        if bool(getattr(self.config, "agent_proactive_market_guard", True)):
            from src.services.proactive_market_guard import is_trading_day

            if not is_trading_day("cn", current):
                return None
        review_day = int(getattr(self.config, "agent_weekly_review_day", 4) or 4)
        if current.weekday() != review_day:
            return None
        hour, minute = self._parse_time(getattr(self.config, "agent_weekly_review_time", "15:30"))
        if (current.hour, current.minute) < (hour, minute):
            return None
        week_key = self._week_key(current)
        if self.db.has_proactive_brief_run(week_key, WEEKLY_SLOT):
            return None
        return self.run(current, force=False)

    def _notify(self, review: WeeklyReview, message_id: Optional[int] = None) -> bool:
        if not bool(getattr(self.config, "agent_weekly_review_notify", False)):
            return False
        from src.services.proactive_notifications import in_quiet_hours, send_proactive_message

        if in_quiet_hours(self.config, self.now):
            logger.info("[WeeklyReview] quiet hours; skip notification")
            return False
        return send_proactive_message(
            self.notifier,
            self.config,
            review.markdown,
            severity="info",
            dedup_key=f"weekly-review:{review.week_key}",
            cooldown_key="weekly_review",
            item_id=message_id,
        )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _outcome_stats(self, week_start: datetime, now: datetime) -> Dict[str, Any]:
        try:
            payload = self.outcome_service.list_outcomes(
                anchor_from=week_start.date(),
                anchor_to=now.date(),
                page=1,
                page_size=100,
            )
        except Exception as exc:
            logger.warning("[WeeklyReview] outcomes failed: %s", exc)
            return {"status": "unavailable"}
        items = [item for item in payload.get("items", []) or [] if isinstance(item, dict)]
        completed = [item for item in items if item.get("eval_status") == "completed"]
        hit = sum(1 for item in completed if item.get("outcome") == "hit")
        miss = sum(1 for item in completed if item.get("outcome") == "miss")
        neutral = sum(1 for item in completed if item.get("outcome") == "neutral")
        denominator = hit + miss
        returns = [
            float(item["stock_return_pct"])
            for item in completed
            if _number(item.get("stock_return_pct")) is not None
        ]
        excess = [
            float(item["excess_return_pct"])
            for item in completed
            if _number(item.get("excess_return_pct")) is not None
        ]
        return {
            "status": "ok",
            "total": len(items),
            "completed": len(completed),
            "hit": hit,
            "miss": miss,
            "neutral": neutral,
            "hit_rate_pct": round(hit / denominator * 100, 2) if denominator else None,
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
            "avg_excess_pct": round(sum(excess) / len(excess), 2) if excess else None,
        }

    def _recent_alerts(self, since: datetime) -> List[Dict[str, Any]]:
        try:
            payload = self.alert_service.list_triggers(page=1, page_size=100)
        except Exception as exc:
            logger.warning("[WeeklyReview] alerts failed: %s", exc)
            return []
        recent: List[Dict[str, Any]] = []
        for item in payload.get("items", []) or []:
            triggered_at = str(item.get("triggered_at") or "")
            try:
                moment = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=MARKET_TZ)
            if moment >= since:
                recent.append(item)
        recent.sort(key=lambda item: str(item.get("triggered_at") or ""), reverse=True)
        return recent

    def _weekly_movers(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbols: List[str] = []
        for account in snapshot.get("accounts", []) or []:
            for position in account.get("positions", []) or []:
                symbol = str(position.get("symbol") or "").strip()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        for item in getattr(self.config, "stock_list", None) or []:
            symbol = str(item).strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)

        movers: List[Dict[str, Any]] = []
        for symbol in symbols[:20]:
            try:
                df, _source = self.history_loader(symbol, 30)
                if df is None or len(df) < 6:
                    continue
                last = _number(df.iloc[-1].get("close"))
                prior = _number(df.iloc[-6].get("close"))
                if last is None or prior is None or prior == 0:
                    continue
                name = symbol
                try:
                    name = str(self.fetcher.get_stock_name(symbol) or symbol)
                except Exception:
                    pass
                movers.append({
                    "symbol": symbol,
                    "name": name,
                    "change_pct": round((last / prior - 1) * 100, 2),
                })
            except Exception as exc:
                logger.debug("[WeeklyReview] weekly mover failed for %s: %s", symbol, exc)
        movers.sort(key=lambda item: item["change_pct"], reverse=True)
        return movers

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(
        self,
        *,
        current: datetime,
        week_start: datetime,
        profile,
        snapshot: Dict[str, Any],
        warnings: List[str],
        outcomes: Dict[str, Any],
        alerts: List[Dict[str, Any]],
        movers: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        total_equity = _number(snapshot.get("total_equity"))
        unrealized = _number(snapshot.get("unrealized_pnl"))
        realized = _number(snapshot.get("realized_pnl"))
        position_count = sum(
            len(account.get("positions", []) or [])
            for account in snapshot.get("accounts", []) or []
        )
        decision_text = self._decision_summary(outcomes)
        summary = (
            f"{current:%Y-%m-%d} 周复盘：{decision_text}；"
            f"持仓 {position_count} 只，画像风险 {len(warnings)} 项。"
        )

        lines: List[str] = [
            f"# {WEEKLY_SESSION_TITLE} · {current:%Y-%m-%d}（{self._week_key(current)}）",
            "",
            f"**一句话：** {summary}",
            "",
            "## 账户",
        ]
        if total_equity is not None:
            lines.append(f"- 总权益：{_fmt_money(total_equity)}")
            lines.append(f"- 浮动盈亏：{_fmt_money(unrealized)}")
            lines.append(f"- 已实现盈亏：{_fmt_money(realized)}")
        else:
            lines.append("- 持仓数据暂不可用。")

        lines.extend(["", "## 决策复盘"])
        if outcomes.get("status") != "ok":
            lines.append("- 决策后验数据暂不可用。")
        elif outcomes.get("total", 0) == 0:
            lines.append("- 本周暂无到期的决策信号。")
        else:
            lines.append(
                f"- 本周到期 {outcomes['completed']} 条：命中 {outcomes['hit']}、"
                f"未命中 {outcomes['miss']}、中性 {outcomes['neutral']}。"
            )
            if outcomes.get("hit_rate_pct") is not None:
                lines.append(f"- 方向命中率：{outcomes['hit_rate_pct']:.1f}%")
            if outcomes.get("avg_return_pct") is not None:
                lines.append(f"- 平均个股收益：{outcomes['avg_return_pct']:+.2f}%")
            if outcomes.get("avg_excess_pct") is not None:
                lines.append(f"- 平均超额收益：{outcomes['avg_excess_pct']:+.2f}%")

        lines.extend(["", "## 风险画像"])
        lines.append(
            f"- 期限 {profile.horizon_label}；单票上限 {profile.max_position_pct:.1f}%；"
            f"回撤容忍 {profile.max_drawdown_pct:.1f}%。"
        )
        if profile.notes:
            lines.append(f"- 补充：{profile.notes}")
        if warnings:
            lines.extend(f"- ⚠️ {warning}" for warning in warnings)
        else:
            lines.append("- 本周未触发画像上限。")

        lines.extend(["", "## 自选周涨跌"])
        if movers:
            gainers = [item for item in movers[:3] if item["change_pct"] > 0]
            losers = [
                item for item in sorted(movers, key=lambda item: item["change_pct"])[:3]
                if item["change_pct"] < 0
            ]
            for mover in gainers:
                lines.append(f"- 领涨 {mover['name']}（{mover['symbol']}）：{mover['change_pct']:+.2f}%")
            for mover in losers:
                lines.append(f"- 领跌 {mover['name']}（{mover['symbol']}）：{mover['change_pct']:+.2f}%")
            if not gainers and not losers:
                lines.append("- 本周自选涨跌均接近 0。")
        else:
            lines.append("- 暂无足够的日线数据计算周涨跌。")

        lines.extend(["", "## 告警"])
        if alerts:
            lines.append(f"- 本周共 {len(alerts)} 条告警，最近 3 条：")
            for alert in alerts[:3]:
                lines.append(
                    f"  - {alert.get('target') or '—'}：{alert.get('reason') or alert.get('status') or '触发'}"
                )
        else:
            lines.append("- 本周无新告警。")

        lines.extend(["", "## 下周关注"])
        focus: List[str] = []
        for warning in warnings[:3]:
            focus.append(f"处理画像风险：{warning}")
        for mover in movers[:2]:
            focus.append(f"继续观察 {mover['name']}（{mover['symbol']}）")
        if not focus:
            focus.append("保持现有节奏，继续观察持仓与自选")
        lines.extend(f"- {item}" for item in focus)
        lines.extend([
            "",
            "---",
            f"统计区间：{week_start:%Y-%m-%d} ~ {current:%Y-%m-%d}。"
            "数据来自 DSA 持仓、决策后验、告警与日线缓存；仅供辅助，不构成投资建议。",
        ])
        return "\n".join(lines), summary

    @staticmethod
    def _decision_summary(outcomes: Dict[str, Any]) -> str:
        if outcomes.get("status") != "ok" or not outcomes.get("completed"):
            return "本周暂无到期的决策复盘"
        rate = outcomes.get("hit_rate_pct")
        rate_text = f"{rate:.1f}%" if rate is not None else "—"
        return f"决策命中率 {rate_text}（{outcomes['hit']}/{outcomes['hit'] + outcomes['miss']}）"

    @staticmethod
    def _week_key(current: datetime) -> str:
        iso = current.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    @staticmethod
    def _parse_time(value: Any) -> Tuple[int, int]:
        text = str(value or "15:30").strip()
        try:
            hour, minute = text.split(":", 1)
            hour_i, minute_i = int(hour), int(minute)
        except (TypeError, ValueError):
            return 15, 30
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            return 15, 30
        return hour_i, minute_i


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_money(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"¥{number:,.2f}"
