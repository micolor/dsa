# -*- coding: utf-8 -*-
"""User-defined recurring tasks and their calendar occurrences."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.config import Config, get_config
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo("Asia/Shanghai")

TASK_TYPES = (
    "stock_analysis",
    "market_review",
    "screening",
    "data_refresh",
    "paper_auto_trade",
    "backtest",
    "agent_prompt",
)
RUN_STATUS_LABELS = {
    "submitted": "已提交",
    "success": "成功",
    "failed": "失败",
    "skipped": "已跳过",
    "missed": "未执行",
    "scheduled": "待执行",
}
MAX_RANGE_DAYS = 92


class ScheduledTaskValidationError(ValueError):
    """Raised when a scheduled-task payload is invalid."""


def _parse_time(value: str) -> time:
    try:
        hour, minute = str(value or "").split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except (TypeError, ValueError):
        raise ScheduledTaskValidationError("time_of_day 必须是 HH:MM") from None
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        raise ScheduledTaskValidationError("time_of_day 必须是 HH:MM")
    return time(hour=hour_i, minute=minute_i)


def _normalize_weekdays(value: Any) -> List[int]:
    if value is None:
        return list(range(7))
    if not isinstance(value, (list, tuple)) or not value:
        raise ScheduledTaskValidationError("weekdays 必须是非空数组（0=周一 ... 6=周日）")
    days: List[int] = []
    for item in value:
        try:
            day = int(item)
        except (TypeError, ValueError):
            raise ScheduledTaskValidationError("weekdays 只能包含 0-6 的整数") from None
        if not 0 <= day <= 6:
            raise ScheduledTaskValidationError("weekdays 只能包含 0-6 的整数")
        days.append(day)
    return sorted(set(days))


def _string_list(value: Any, field: str, limit: int = 50) -> List[str]:
    if value in (None, "", []):
        return []
    if not isinstance(value, (list, tuple)):
        raise ScheduledTaskValidationError(f"{field} 必须是数组")
    codes = [str(item).strip() for item in value if str(item).strip()]
    return codes[:limit]


def _market_for_task(task_type: str, payload: Dict[str, Any]) -> str:
    if task_type == "market_review":
        region = str(payload.get("region") or "cn").split(",")[0].strip().lower()
        return region or "cn"
    if task_type in ("screening", "data_refresh"):
        return str(payload.get("market") or "cn").strip().lower()
    return "cn"


def normalize_payload(task_type: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and normalize a type-specific payload."""
    if task_type not in TASK_TYPES:
        raise ScheduledTaskValidationError(f"不支持的任务类型: {task_type}")
    data = payload if isinstance(payload, dict) else {}

    if task_type == "stock_analysis":
        report_type = str(data.get("report_type") or "detailed").strip()
        mode = str(data.get("mode") or "standard").strip().lower()
        if report_type not in {"brief", "detailed"}:
            raise ScheduledTaskValidationError("report_type 只支持 brief / detailed")
        if mode not in {"standard", "quick"}:
            raise ScheduledTaskValidationError("mode 只支持 standard / quick")
        return {
            "stocks": _string_list(data.get("stocks"), "stocks"),
            "report_type": report_type,
            "force_refresh": bool(data.get("force_refresh", False)),
            "mode": mode,
            "notify": bool(data.get("notify", False)),
        }
    if task_type == "market_review":
        region = str(data.get("region") or "cn").strip().lower()
        if region not in {"cn", "hk", "us"}:
            raise ScheduledTaskValidationError("region 只支持 cn / hk / us")
        return {"region": region, "notify": bool(data.get("notify", False))}
    if task_type in ("screening", "data_refresh"):
        strategy = str(data.get("strategy") or "capital_heat").strip()
        market = str(data.get("market") or "cn").strip().lower()
        if not strategy:
            raise ScheduledTaskValidationError("strategy 不能为空")
        if market not in {"cn", "hk", "us"}:
            raise ScheduledTaskValidationError("market 只支持 cn / hk / us")
        try:
            max_results = int(data.get("max_results", 20))
        except (TypeError, ValueError):
            raise ScheduledTaskValidationError("max_results 必须是整数") from None
        if not 1 <= max_results <= 100:
            raise ScheduledTaskValidationError("max_results 必须在 1-100 之间")
        return {"strategy": strategy, "market": market, "max_results": max_results}
    if task_type == "paper_auto_trade":
        try:
            lookback_days = int(data.get("lookback_days", 7))
        except (TypeError, ValueError):
            raise ScheduledTaskValidationError("lookback_days 必须是整数") from None
        if not 1 <= lookback_days <= 365:
            raise ScheduledTaskValidationError("lookback_days 必须在 1-365 之间")
        return {"lookback_days": lookback_days}
    if task_type == "backtest":
        code = str(data.get("code") or "").strip().upper() or None
        try:
            limit = int(data.get("limit", 200))
        except (TypeError, ValueError):
            raise ScheduledTaskValidationError("limit 必须是整数") from None
        if not 1 <= limit <= 1000:
            raise ScheduledTaskValidationError("limit 必须在 1-1000 之间")
        return {"code": code, "force": bool(data.get("force", False)), "limit": limit}

    prompt = str(data.get("prompt") or "").strip()
    if len(prompt) < 5:
        raise ScheduledTaskValidationError("agent_prompt 的 prompt 至少 5 个字符")
    if len(prompt) > 2000:
        raise ScheduledTaskValidationError("agent_prompt 的 prompt 不能超过 2000 个字符")
    return {"prompt": prompt, "notify": bool(data.get("notify", False))}


class ScheduledTaskService:
    def __init__(self, config: Optional[Config] = None, db: Optional[DatabaseManager] = None) -> None:
        self.config = config or get_config()
        self.db = db or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_tasks(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        return self.db.list_scheduled_tasks(enabled_only=enabled_only)

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        return self.db.get_scheduled_task(int(task_id))

    def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        task_type = str(data.get("task_type") or "").strip()
        name = str(data.get("name") or "").strip()
        if not name:
            raise ScheduledTaskValidationError("name 不能为空")
        _parse_time(str(data.get("time_of_day") or ""))
        weekdays = _normalize_weekdays(data.get("weekdays"))
        payload = normalize_payload(task_type, data.get("payload"))
        return self.db.create_scheduled_task(
            name=name[:120],
            task_type=task_type,
            payload=payload,
            weekdays=weekdays,
            time_of_day=str(data.get("time_of_day")),
            enabled=bool(data.get("enabled", True)),
            trading_day_only=bool(data.get("trading_day_only", True)),
        )

    def update_task(self, task_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.get_task(task_id)
        if existing is None:
            raise KeyError(task_id)
        fields: Dict[str, Any] = {}
        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ScheduledTaskValidationError("name 不能为空")
            fields["name"] = name[:120]
        task_type = str(data.get("task_type") or existing["task_type"])
        if "task_type" in data:
            if task_type not in TASK_TYPES:
                raise ScheduledTaskValidationError(f"不支持的任务类型: {task_type}")
            fields["task_type"] = task_type
        if "payload" in data:
            fields["payload"] = normalize_payload(task_type, data.get("payload"))
        if "weekdays" in data:
            fields["weekdays"] = _normalize_weekdays(data.get("weekdays"))
        if "time_of_day" in data:
            _parse_time(str(data.get("time_of_day") or ""))
            fields["time_of_day"] = str(data.get("time_of_day"))
        if "enabled" in data:
            fields["enabled"] = bool(data.get("enabled"))
        if "trading_day_only" in data:
            fields["trading_day_only"] = bool(data.get("trading_day_only"))
        updated = self.db.update_scheduled_task(int(task_id), fields)
        if updated is None:
            raise KeyError(task_id)
        return updated

    def delete_task(self, task_id: int) -> bool:
        return self.db.delete_scheduled_task(int(task_id))

    def set_enabled(self, task_id: int, enabled: bool) -> Dict[str, Any]:
        return self.update_task(task_id, {"enabled": bool(enabled)})

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------
    def calendar(self, start: date, end: date) -> Dict[str, Any]:
        if end < start:
            raise ScheduledTaskValidationError("end 不能早于 start")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise ScheduledTaskValidationError(f"日历范围最多 {MAX_RANGE_DAYS} 天")
        now = datetime.now(MARKET_TZ)
        tasks = self.list_tasks()
        runs = self.db.list_scheduled_task_runs(
            since=datetime.combine(start, time.min),
            until=datetime.combine(end + timedelta(days=1), time.min),
            limit=1000,
        )
        runs_by_slot = {}
        for run in runs:
            if not run.get("scheduled_for"):
                continue
            try:
                slot_key = datetime.fromisoformat(run["scheduled_for"]).isoformat(timespec="minutes")
            except ValueError:
                continue
            runs_by_slot[slot_key] = run

        occurrences: List[Dict[str, Any]] = []
        current = start
        while current <= end:
            weekday = current.weekday()
            for task in tasks:
                if not task["enabled"]:
                    continue
                if weekday not in task["weekdays"]:
                    continue
                slot_time = _parse_time(task["time_of_day"])
                slot_at = datetime.combine(current, slot_time)
                occurrence = {"task": task, "scheduled_for": slot_at.isoformat(timespec="minutes")}
                if task["trading_day_only"] and not self._is_trading_day(task, current):
                    occurrence["status"] = "skipped"
                    occurrence["message"] = "非交易日"
                else:
                    run = runs_by_slot.get(occurrence["scheduled_for"])
                    if run is not None:
                        occurrence["status"] = run["status"]
                        occurrence["message"] = run.get("message")
                    elif slot_at < datetime.now():
                        occurrence["status"] = "missed"
                    else:
                        occurrence["status"] = "scheduled"
                occurrences.append(occurrence)
            current += timedelta(days=1)
        occurrences.sort(key=lambda item: (item["scheduled_for"], item["task"]["id"]))
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "generated_at": now.isoformat(timespec="seconds"),
            "occurrences": occurrences,
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def run_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Execute slots that became due in the current minute."""
        current = now or datetime.now(MARKET_TZ)
        # Storage uses naive local datetimes; normalize aware "now" before
        # comparing with the naive slot so the subtraction cannot fail.
        if current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        results: List[Dict[str, Any]] = []
        for task in self.list_tasks(enabled_only=True):
            if current.weekday() not in task["weekdays"]:
                continue
            slot_time = _parse_time(task["time_of_day"])
            slot_at = datetime.combine(current.date(), slot_time)
            if not (0 <= (current - slot_at).total_seconds() <= 120):
                continue
            results.append(self.execute(task, trigger_source="schedule", scheduled_for=slot_at))
        return results

    def execute(
        self,
        task: Dict[str, Any],
        *,
        trigger_source: str = "manual",
        scheduled_for: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        task_id = int(task["id"])
        slot_key = (
            f"{task_id}:{scheduled_for.isoformat(timespec='minutes')}"
            if scheduled_for is not None
            else None
        )
        run_id = self.db.save_scheduled_task_run(
            task_id=task_id,
            trigger_source=trigger_source,
            status="running",
            scheduled_for=scheduled_for,
            slot_key=slot_key,
        )
        if run_id is None:
            return {"task_id": task_id, "status": "skipped", "message": "该时点已执行过"}

        status = "success"
        message = ""
        try:
            if task["trading_day_only"] and not self._is_trading_day(task, scheduled_for.date() if scheduled_for else None):
                status = "skipped"
                message = "非交易日，跳过执行"
            else:
                status, message = self._dispatch(task)
        except Exception as exc:  # pragma: no cover - defensive branch
            status = "failed"
            message = str(exc)
            logger.warning("[ScheduledTask] task %s failed: %s", task_id, exc, exc_info=True)

        self.db.finish_scheduled_task_run(run_id, status=status, message=message)
        self.db.update_scheduled_task_state(task_id, status=status, message=message)
        return {"task_id": task_id, "run_id": run_id, "status": status, "message": message}

    def _is_trading_day(self, task: Dict[str, Any], day: Optional[date]) -> bool:
        from src.services.proactive_market_guard import is_trading_day

        market = _market_for_task(task["task_type"], task.get("payload") or {})
        if day is not None:
            check_at = datetime.combine(day, time(hour=10, minute=0), tzinfo=MARKET_TZ)
        else:
            check_at = None
        return is_trading_day(market, check_at)

    def _dispatch(self, task: Dict[str, Any]) -> tuple[str, str]:
        from src.services import site_task_submitter as submitter

        task_type = task["task_type"]
        payload = task.get("payload") or {}
        if task_type == "stock_analysis":
            codes = payload.get("stocks") or list(getattr(self.config, "stock_list", []) or [])
            if not codes:
                return "failed", "没有可分析的股票：请配置任务股票或自选股"
            if str(payload.get("mode") or "standard") == "quick":
                from src.services.quick_analysis_service import QuickAnalysisService

                summary = QuickAnalysisService(self.config).analyze(
                    codes,
                    notify=bool(payload.get("notify", False)),
                )
                return "success", summary[:4000]
            submitted = 0
            for code in codes:
                try:
                    submitter.submit_stock_analysis(
                        str(code),
                        report_type=str(payload.get("report_type") or "detailed"),
                        force_refresh=bool(payload.get("force_refresh", False)),
                    )
                    submitted += 1
                except Exception as exc:
                    logger.warning("[ScheduledTask] submit %s failed: %s", code, exc)
            if submitted == 0:
                return "failed", "个股分析任务提交失败"
            return "submitted", f"已提交 {submitted} 个个股分析任务"
        if task_type == "market_review":
            submitter.submit_market_review(
                send_notification=bool(payload.get("notify", False)),
                region=str(payload.get("region") or "cn"),
                config=self.config,
            )
            return "submitted", "大盘复盘任务已提交"
        if task_type == "screening":
            result = submitter.submit_screening(
                str(payload.get("strategy") or "capital_heat"),
                str(payload.get("market") or "cn"),
                int(payload.get("max_results") or 20),
                config=self.config,
            )
            task_id = result.get("task_id") if isinstance(result, dict) else None
            return "submitted", f"选股任务已提交{f'（{task_id}）' if task_id else ''}"
        if task_type == "data_refresh":
            from src.services.proactive_data_refresh_service import ProactiveDataRefreshService
            from src.services.task_queue import get_task_queue

            strategy = str(payload.get("strategy") or "capital_heat")
            market = str(payload.get("market") or "cn")
            max_results = int(payload.get("max_results") or 20)
            refresh = ProactiveDataRefreshService(self.config, db=self.db)

            def run_refresh() -> Optional[Dict[str, Any]]:
                return refresh.run(
                    slot=f"manual:{task['id']}",
                    force=True,
                    strategy=strategy,
                    market=market,
                    max_results=max_results,
                )

            queued = get_task_queue().submit_background_task(
                run_refresh,
                stock_code="data_refresh",
                stock_name=f"{strategy} / {market}",
                report_type="data_refresh",
                message="数据刷新任务已提交",
            )
            return "submitted", f"数据刷新任务已提交（{queued.task_id}）"
        if task_type == "paper_auto_trade":
            from src.services.paper_service import PaperService

            result = PaperService(self.db).auto_trade_once(
                lookback_days=int(payload.get("lookback_days") or 7),
            )
            if result.get("status") == "account_not_found":
                return "failed", "模拟盘账户不存在或已归档"
            unavailable = int(result.get("unavailable") or 0)
            errors = int(result.get("errors") or 0)
            message = (
                f"模拟盘自动交易：处理 {int(result.get('processed') or 0)} 条，"
                f"跳过 {int(result.get('skipped') or 0)} 条，"
                f"缺行情 {unavailable} 条，错误 {errors} 条"
            )
            if unavailable or errors:
                # Keep the run visibly failed so the next scheduled occurrence
                # retries; signals whose bar was missing are not consumed.
                return "failed", message
            return "success", message
        if task_type == "backtest":
            submitter.submit_backtest(
                code=payload.get("code"),
                force=bool(payload.get("force", False)),
                limit=int(payload.get("limit") or 200),
            )
            return "submitted", "回测任务已提交"

        prompt = str(payload.get("prompt") or "").strip()
        from src.agent.factory import build_general_chat_executor

        # Scheduled prompts use the general single Agent on purpose: the
        # multi-agent pipeline can take minutes and is meant for interactive
        # stock analysis, not background instructions.
        executor = build_general_chat_executor(self.config)
        result = executor.chat(message=prompt, session_id=f"scheduled:{task['id']}")
        content = str(getattr(result, "content", "") or "").strip()
        if not content:
            return "success", "助手任务已执行，但没有返回内容"
        return "success", content[:4000]
