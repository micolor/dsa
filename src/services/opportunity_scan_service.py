# -*- coding: utf-8 -*-
"""Opportunity scan: turn the latest screening run and hot themes into a shortlist."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Shanghai")
OPPORTUNITY_SESSION_ID = "proactive:opportunity_scan"
OPPORTUNITY_SESSION_TITLE = "机会扫描"
DEFAULT_SCAN_TIMES = ("08:45", "15:45")


@dataclass(frozen=True)
class OpportunityScan:
    generated_at: datetime
    slot: str
    summary: str
    markdown: str
    message_id: Optional[int] = None
    notified: bool = False


class OpportunityScanService:
    def __init__(
        self,
        config: Any = None,
        *,
        screening_service: Any = None,
        portfolio_service: Any = None,
        db: Any = None,
        notifier: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config
        self._screening_service = screening_service
        self._portfolio_service = portfolio_service
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
    def portfolio_service(self):
        if self._portfolio_service is None:
            from src.services.portfolio_service import PortfolioService

            self._portfolio_service = PortfolioService()
        return self._portfolio_service

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
    def generate(self, *, slot: str = "manual", now: Optional[datetime] = None) -> OpportunityScan:
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        slot = (slot or "manual").strip() or "manual"

        max_age_hours = int(getattr(self.config, "agent_opportunity_scan_max_age_hours", 72) or 72)
        run, candidates, run_error = self._latest_screening(current, max_age_hours)
        hotspots, hotspot_meta = self._hotspots()
        held, watchlist = self._ownership()

        max_candidates = max(
            1, min(int(getattr(self.config, "agent_opportunity_scan_max_candidates", 5) or 5), 20)
        )
        candidates = candidates[:max_candidates]
        run_age_hours = self._age_hours(run.get("created_at") if run else None, current)
        stale_run = run_age_hours is not None and run_age_hours > max_age_hours
        stale_hotspots = bool(hotspot_meta.get("stale"))

        markdown, summary = self._render(
            current=current,
            slot=slot,
            run=run,
            run_error=run_error,
            candidates=candidates,
            run_age_hours=run_age_hours,
            stale_run=stale_run,
            max_age_hours=max_age_hours,
            hotspots=hotspots,
            hotspot_meta=hotspot_meta,
            stale_hotspots=stale_hotspots,
            held=held,
            watchlist=watchlist,
        )
        return OpportunityScan(
            generated_at=current,
            slot=slot,
            summary=summary,
            markdown=markdown,
        )

    def run(
        self,
        *,
        slot: Optional[str] = None,
        now: Optional[datetime] = None,
        force: bool = True,
    ) -> Optional[OpportunityScan]:
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        slot = (slot or "manual").strip() or "manual"
        day = f"{current:%Y-%m-%d}"
        if not force and slot != "manual" and self.db.has_proactive_brief_run(day, f"opportunity:{slot}"):
            return None

        scan = self.generate(slot=slot, now=current)
        message_id = self.db.save_proactive_message(
            "opportunity", f"{OPPORTUNITY_SESSION_TITLE} · {current:%Y-%m-%d}", scan.markdown
        )
        self.db.save_proactive_brief_run(
            day, f"opportunity:{slot}", OPPORTUNITY_SESSION_ID, message_id
        )
        # A manual run also satisfies the latest scheduled slot for today, so the
        # 5-minute scheduler tick does not immediately produce a duplicate push.
        if slot == "manual":
            due_slot = self._latest_due_slot(current)
            if due_slot:
                self.db.save_proactive_brief_run(
                    day, f"opportunity:{due_slot}", OPPORTUNITY_SESSION_ID, message_id
                )
        notified = self._notify(scan, message_id)
        from dataclasses import replace

        return replace(scan, message_id=message_id, notified=notified)

    def run_due(self, now: Optional[datetime] = None) -> Optional[OpportunityScan]:
        if not bool(getattr(self.config, "agent_opportunity_scan_enabled", False)):
            return None
        current = now or self.now
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TZ)
        if bool(getattr(self.config, "agent_proactive_market_guard", True)):
            from src.services.proactive_market_guard import is_trading_day

            if not is_trading_day("cn", current):
                return None
        slot = self._latest_due_slot(current)
        if slot is None:
            return None
        day = f"{current:%Y-%m-%d}"
        if self.db.has_proactive_brief_run(day, f"opportunity:{slot}"):
            return None
        return self.run(slot=slot, now=current, force=False)

    def _notify(self, scan: OpportunityScan, message_id: Optional[int] = None) -> bool:
        if not bool(getattr(self.config, "agent_opportunity_scan_notify", False)):
            return False
        from src.services.proactive_notifications import in_quiet_hours, send_proactive_message

        if in_quiet_hours(self.config, self.now):
            logger.info("[OpportunityScan] quiet hours; skip notification")
            return False
        return send_proactive_message(
            self.notifier,
            self.config,
            scan.markdown,
            severity="info",
            dedup_key=f"opportunity-scan:{scan.generated_at:%Y-%m-%d}:{scan.slot}",
            cooldown_key="opportunity_scan",
            item_id=message_id,
        )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _latest_screening(
        self,
        current: datetime,
        max_age_hours: int,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
        run, candidates, error = self._fetch_latest_screening()
        if not bool(getattr(self.config, "agent_opportunity_scan_refresh", False)):
            return run, candidates, error

        age_hours = self._age_hours(run.get("created_at") if run else None, current)
        if run is not None and age_hours is not None and age_hours <= max_age_hours:
            return run, candidates, error

        # Opt-in refresh: the snapshot is missing or stale, so run screening once
        # and re-read the latest result. Failures keep the previous snapshot.
        try:
            logger.info(
                "[OpportunityScan] refreshing stale screening snapshot (age=%s h)",
                age_hours if age_hours is not None else "missing",
            )
            self.screening_service.screen(
                strategy=str(getattr(self.config, "agent_opportunity_scan_refresh_strategy", "capital_heat") or "capital_heat"),
                market=str(getattr(self.config, "agent_opportunity_scan_refresh_market", "cn") or "cn"),
                max_results=max(
                    1,
                    min(int(getattr(self.config, "agent_opportunity_scan_refresh_max_results", 20) or 20), 100),
                ),
            )
        except Exception as exc:
            logger.warning("[OpportunityScan] auto refresh failed: %s", exc)
            suffix = f"自动刷新选股失败：{exc}"
            return run, candidates, f"{error}；{suffix}" if error else suffix
        return self._fetch_latest_screening()

    def _fetch_latest_screening(self) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
        try:
            history = self.screening_service.history(limit=1)
        except Exception as exc:
            logger.warning("[OpportunityScan] screening history failed: %s", exc)
            return None, [], f"选股历史不可用：{exc}"
        runs = history.get("runs", []) or []
        if not runs:
            return None, [], "暂无选股快照，请先在选股页运行一次"
        run = runs[0]
        try:
            detail = self.screening_service.history_detail(run.get("run_id"))
        except Exception as exc:
            logger.warning("[OpportunityScan] screening detail failed: %s", exc)
            return run, [], f"选股详情不可用：{exc}"
        result = detail.get("result") or {}
        candidates = [
            item for item in (result.get("candidates") or [])
            if isinstance(item, dict)
        ]
        return detail, candidates, ""

    def _hotspots(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        try:
            payload = self.screening_service.hotspots(top=5)
        except Exception as exc:
            logger.warning("[OpportunityScan] hotspots failed: %s", exc)
            return [], {"stale": False, "stale_age_hours": None}
        hotspots = [
            item for item in (payload.get("hotspots") or [])
            if isinstance(item, dict)
        ][:5]
        return hotspots, {
            "stale": bool(payload.get("stale")),
            "stale_age_hours": payload.get("stale_age_hours"),
            "cached_at": payload.get("cached_at"),
        }

    def _ownership(self) -> Tuple[set, set]:
        watchlist = {
            str(item).strip()
            for item in (getattr(self.config, "stock_list", None) or [])
            if str(item).strip()
        }
        held: set = set()
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(include_realtime=False)
            for account in snapshot.get("accounts", []) or []:
                for position in account.get("positions", []) or []:
                    symbol = str(position.get("symbol") or "").strip()
                    if symbol:
                        held.add(symbol)
        except Exception as exc:
            logger.warning("[OpportunityScan] portfolio failed: %s", exc)
        return held, watchlist

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(
        self,
        *,
        current: datetime,
        slot: str,
        run: Optional[Dict[str, Any]],
        run_error: str,
        candidates: List[Dict[str, Any]],
        run_age_hours: Optional[float],
        stale_run: bool,
        max_age_hours: int,
        hotspots: List[Dict[str, Any]],
        hotspot_meta: Dict[str, Any],
        stale_hotspots: bool,
        held: set,
        watchlist: set,
    ) -> Tuple[str, str]:
        display_time = current.strftime("%H:%M") if slot == "manual" else slot
        candidate_count = len(candidates)
        hotspot_count = len(hotspots)
        stale_notes: List[str] = []
        if stale_run and run_age_hours is not None:
            stale_notes.append(f"选股快照已过期 {run_age_hours:.0f} 小时（阈值 {max_age_hours} 小时）")
        if stale_hotspots:
            age = hotspot_meta.get("stale_age_hours")
            stale_notes.append(
                f"热点缓存已过期 {float(age):.0f} 小时" if age is not None else "热点缓存已过期"
            )
        summary = (
            f"最新选股 {candidate_count} 个候选、热点 {hotspot_count} 个"
            + (f"；{len(stale_notes)} 项数据已过期" if stale_notes else "")
        )
        if run_error:
            summary = run_error

        lines: List[str] = [
            f"# {OPPORTUNITY_SESSION_TITLE} · {current:%Y-%m-%d} {display_time}",
            "",
            f"**一句话：** {summary}",
            "",
        ]
        if stale_notes:
            lines.append("> ⚠️ " + "；".join(stale_notes) + "。建议先刷新数据再据此决策。")
            lines.append("")

        lines.append("## 候选")
        if not candidates:
            lines.append(f"- {run_error or '暂无候选，请先运行选股。'}")
        else:
            for index, item in enumerate(candidates, start=1):
                code = str(item.get("code") or "")
                name = str(item.get("name") or code)
                score = _number(item.get("llm_score"))
                if score is None:
                    score = _number(item.get("score"))
                theme = str(item.get("llm_theme") or item.get("llm_sector") or item.get("industry") or "").strip()
                thesis = str(item.get("llm_thesis") or item.get("reason") or "").strip()
                risks = item.get("llm_risks") or item.get("risk_flags") or []
                watch_items = item.get("llm_watch_items") or []
                tags: List[str] = []
                if code in held:
                    tags.append("已在持仓")
                if code in watchlist:
                    tags.append("已在自选")
                tags.append("新候选" if len(tags) == 0 else "需确认")
                title = f"### {index}. {name}（{code}）"
                if score is not None:
                    title += f" · 评分 {score:.1f}"
                lines.append(title)
                if theme:
                    lines.append(f"- 主题/行业：{theme}")
                if thesis:
                    lines.append(f"- 理由：{thesis}")
                risk_text = "、".join(str(value) for value in risks if str(value).strip())
                if risk_text:
                    lines.append(f"- 风险：{risk_text}")
                watch_text = "；".join(str(value) for value in watch_items if str(value).strip())
                if watch_text:
                    lines.append(f"- 关注：{watch_text}")
                lines.append(f"- 状态：{' / '.join(tags)}")
                lines.append("")

        lines.append("## 热点题材")
        if hotspots:
            for item in hotspots[:3]:
                name = item.get("name") or item.get("topic") or "—"
                change = _number(item.get("change_pct"))
                heat = _number(item.get("heat_score"))
                stage = str(item.get("state") or item.get("stage") or "").strip()
                detail = []
                if change is not None:
                    detail.append(f"{change:+.2f}%")
                if heat is not None:
                    detail.append(f"热度 {heat:.1f}")
                if stage:
                    detail.append(stage)
                lines.append(f"- {name}：{'，'.join(detail) if detail else '—'}")
        else:
            lines.append("- 暂无可用热点数据。")

        lines.extend([
            "",
            "## 使用说明",
            "- 候选来自最近一次选股快照，不是实时推荐；请结合行情、仓位和风险画像自行判断。",
            "- 已在持仓/自选的标的会标注状态，避免重复建仓。",
            "- 本扫描不构成投资建议，不会自动交易。",
            "",
            "---",
            f"数据时间：{run.get('created_at') if run else '—'}；"
            f"热点缓存：{hotspot_meta.get('cached_at') or '—'}。",
        ])
        return "\n".join(lines), summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _latest_due_slot(self, current: datetime) -> Optional[str]:
        raw = getattr(self.config, "agent_opportunity_scan_times", None) or ",".join(DEFAULT_SCAN_TIMES)
        slots = [item.strip() for item in str(raw).split(",") if _parse_time(item.strip())]
        if not slots:
            slots = list(DEFAULT_SCAN_TIMES)
        due: Optional[str] = None
        for slot in slots:
            parsed = _parse_time(slot)
            if parsed is None:
                continue
            if (current.hour, current.minute) >= parsed:
                due = slot
        return due

    @staticmethod
    def _age_hours(value: Any, now: datetime) -> Optional[float]:
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=MARKET_TZ)
        return max(0.0, (now - moment).total_seconds() / 3600.0)


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


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
