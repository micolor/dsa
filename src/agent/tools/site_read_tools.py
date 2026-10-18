# -*- coding: utf-8 -*-
"""Read-only site views for the chat assistant.

Every tool in this module is side-effect free (read-only DB access) and
declares ``execution_mode="none"`` so the agent runner treats it as a plain
observation rather than an action.  Handlers return bounded JSON strings and
never expose sensitive configuration values.

``get_signal_view`` with ``view="stats"`` returns aggregate outcome stats and
supports only the ``status`` and ``horizon`` filters.  Combinations it cannot
honour (``stock_code``, ``signal_id``, ``action``, ``outcome``, ``eval_status``)
are rejected with an ``unsupported_filters_for_stats`` error rather than being
silently ignored.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    execution_mode="none",
    risk_level="read",
    cancellation_safe=True,
)

# Tools that accept a stock_code parameter must declare the stock scope
# dimension to satisfy strict tool-surface validation.
_STOCK_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    scope_dimensions=["stock"],
    execution_mode="none",
    risk_level="read",
    cancellation_safe=True,
)

# Factory hooks.  Tests (and callers) may replace these with callables that
# return a service double; the default lazily imports the real service.
_history_service = None
_alert_service = None
_task_queue = None
_config_service = None
_backtest_service = None
_screening_service = None
_signal_service = None
_outcome_service = None
_fund_review_service = None
_paper_service = None
_portfolio_service = None
_intelligence_service = None
_skill_performance_service = None
_data_quality_repository = None
_notification_delivery_repository = None


def _history_service_instance():
    global _history_service
    if _history_service is None:
        from src.services.history_service import HistoryService

        _history_service = HistoryService
    return _history_service()


def _alert_service_instance():
    global _alert_service
    if _alert_service is None:
        from src.services.alert_service import AlertService

        _alert_service = AlertService
    return _alert_service()


def _task_queue_instance():
    global _task_queue
    if _task_queue is None:
        from src.services.task_queue import get_task_queue

        _task_queue = get_task_queue
    return _task_queue()


def _config_service_instance():
    global _config_service
    if _config_service is None:
        from src.services.system_config_service import SystemConfigService

        _config_service = SystemConfigService
    return _config_service()


def _backtest_service_instance():
    global _backtest_service
    if _backtest_service is None:
        from src.services.backtest_service import BacktestService

        _backtest_service = BacktestService
    return _backtest_service()


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db

    return get_db()


def _screening_service_instance():
    global _screening_service
    if _screening_service is None:
        from src.config import get_config
        from src.services.screening_service import ScreeningService

        def _build_screening_service():
            # History queries need the DSA database; without it the service
            # raises ``screening_history_unavailable`` for history views.
            return ScreeningService(config=get_config(), db_manager=_get_db())

        _screening_service = _build_screening_service
    return _screening_service()


def _signal_service_instance():
    global _signal_service
    if _signal_service is None:
        from src.services.decision_signal_service import DecisionSignalService

        _signal_service = DecisionSignalService
    return _signal_service()


def _outcome_service_instance():
    global _outcome_service
    if _outcome_service is None:
        from src.services.decision_signal_outcome_service import (
            DecisionSignalOutcomeService,
        )

        _outcome_service = DecisionSignalOutcomeService
    return _outcome_service()


def _fund_review_service_instance():
    global _fund_review_service
    if _fund_review_service is None:
        from src.services.fund_nav_review_service import FundNavReviewService

        _fund_review_service = FundNavReviewService
    return _fund_review_service()


def _paper_service_instance():
    global _paper_service
    if _paper_service is None:
        from src.services.paper_service import PaperService

        _paper_service = PaperService
    return _paper_service()


def _portfolio_service_instance():
    global _portfolio_service
    if _portfolio_service is None:
        from src.services.portfolio_service import PortfolioService

        _portfolio_service = PortfolioService
    return _portfolio_service()


def _intelligence_service_instance():
    global _intelligence_service
    if _intelligence_service is None:
        from src.services.intelligence_service import IntelligenceService

        _intelligence_service = IntelligenceService
    return _intelligence_service()


def _skill_performance_service_instance():
    global _skill_performance_service
    if _skill_performance_service is None:
        from src.services.skill_opinion_performance_service import (
            SkillOpinionPerformanceService,
        )

        _skill_performance_service = SkillOpinionPerformanceService
    return _skill_performance_service()


def _data_quality_repository_instance():
    global _data_quality_repository
    if _data_quality_repository is None:
        from src.repositories.data_quality_discrepancy_repo import (
            DataQualityDiscrepancyRepository,
        )

        _data_quality_repository = DataQualityDiscrepancyRepository
    return _data_quality_repository()


def _notification_delivery_repository_instance():
    global _notification_delivery_repository
    if _notification_delivery_repository is None:
        from src.repositories.notification_delivery_repo import (
            NotificationDeliveryRepository,
        )

        _notification_delivery_repository = NotificationDeliveryRepository
    return _notification_delivery_repository()


def _paper_existing_account_id(account_id: Optional[int] = None) -> Optional[int]:
    """Resolve an existing paper account without creating one."""
    return _paper_service_instance().get_existing_account_id(account_id)


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _dump({"error": message})


def _task_payload(task: Any) -> Any:
    return task.to_dict() if hasattr(task, "to_dict") else vars(task)


def _handle_get_history_view(
    stock_code: Optional[str] = None,
    report_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    record_id: Optional[int] = None,
    part: str = "detail",
) -> str:
    service = _history_service_instance()
    if record_id is not None:
        norm_part = str(part or "detail").strip().lower()
        if norm_part == "news":
            return _dump(service.get_news_intel_by_record_id(int(record_id)))
        if norm_part == "markdown":
            markdown = service.get_markdown_report(str(record_id))
            if not markdown:
                return _dump({"error": f"markdown report for record {record_id} not found"})
            return _dump({"record_id": int(record_id), "markdown": markdown})
        if norm_part != "detail":
            return _error(
                f"unknown part: {part}; expected detail|news|markdown"
            )
        detail = service.get_history_detail_by_id(int(record_id))
        return _dump(detail or {"error": f"history record {record_id} not found"})
    return _dump(service.get_history_list(
        stock_code=stock_code,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        page=max(1, int(page)),
        limit=max(1, min(int(limit), 100)),
    ))


def _handle_get_alert_view(
    view: str = "rules",
    rule_id: Optional[int] = None,
    trigger_id: Optional[int] = None,
    enabled: Optional[bool] = None,
    alert_type: Optional[str] = None,
    target: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _alert_service_instance()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "rules":
        if rule_id is not None:
            return _dump(service.get_rule(int(rule_id)))
        return _dump(service.list_rules(
            enabled=enabled,
            alert_type=alert_type,
            target=target,
            page=page,
            page_size=page_size,
        ))
    if view == "triggers":
        return _dump(service.list_triggers(
            rule_id=rule_id,
            target=target,
            status=status,
            page=page,
            page_size=page_size,
        ))
    if view == "notifications":
        # ``trigger_id`` is the notification's parent trigger; ``rule_id`` keeps
        # its meaning for rules/triggers views and must not be reused here.
        return _dump(service.list_notifications(
            trigger_id=int(trigger_id) if trigger_id is not None else None,
            page=page,
            page_size=page_size,
        ))
    return _error(f"unknown view: {view}; expected rules|triggers|notifications")


def _handle_get_analysis_task_view(
    view: str = "list",
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    queue = _task_queue_instance()
    if view == "detail":
        if not task_id:
            return _error("task_id is required for view=detail")
        task = queue.get_task(task_id)
        if task is None:
            return _error(f"task {task_id} not found")
        return _dump({
            "task": _task_payload(task),
            "flow_events": queue.get_task_flow_events(task_id),
        })
    if view == "list":
        statuses = [item.strip() for item in status.split(",")] if status else None
        tasks = queue.list_all_tasks(limit=max(1, min(int(limit), 100)), status=statuses)
        return _dump({
            "tasks": [_task_payload(task) for task in tasks],
            "stats": queue.get_task_stats(),
        })
    return _error(f"unknown view: {view}; expected list|detail")


_CONFIG_USAGE_PERIODS = ("today", "month", "all")

_DATA_QUALITY_FIELDS = (
    "id", "market", "stock_code", "issue_type", "primary_source",
    "secondary_source", "primary_price", "secondary_price", "price_diff_pct",
    "primary_ts", "secondary_ts", "detail", "created_at",
)

_NOTIFICATION_DELIVERY_FIELDS = (
    "id", "route_type", "channel", "attempt", "success", "error_code",
    "retryable", "latency_ms", "diagnostics", "created_at",
)


def _row_dict(row: Any, fields: tuple) -> dict:
    return {name: getattr(row, name) for name in fields}


def _handle_get_config_view(
    view: str = "config",
    key: Optional[str] = None,
    period: str = "today",
    page: int = 1,
    page_size: int = 20,
) -> str:
    norm_view = str(view or "config").strip().lower()
    if norm_view == "config":
        service = _config_service_instance()
        schema = service.get_schema()
        config = service.get_config(include_schema=False)
        values = {item["key"]: item["value"] for item in config.get("items", [])}
        fields = []
        for category in schema.get("categories", []):
            for field in category.get("fields", []):
                if field.get("is_sensitive"):
                    continue
                field_key = field.get("key")
                if key and field_key != key:
                    continue
                fields.append({
                    "key": field_key,
                    "title": field.get("title"),
                    "category": category.get("category"),
                    "data_type": field.get("data_type"),
                    "is_editable": field.get("is_editable"),
                    "value": values.get(field_key),
                })
        if key and not fields:
            return _error(f"unknown or sensitive config key: {key}")
        return _dump({"config_version": config.get("config_version"), "fields": fields})

    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))

    if norm_view == "usage":
        norm_period = str(period or "today").strip().lower()
        if norm_period not in _CONFIG_USAGE_PERIODS:
            return _error(
                f"period must be {'/'.join(_CONFIG_USAGE_PERIODS)}, got {period!r}"
            )
        from datetime import datetime, timedelta, timezone

        # 与 REST 用量接口（api/v1/endpoints/usage.py::_date_range）保持同一口径：
        # 固定北京时间（UTC+8）的自然日/自然月边界，否则部署在非东八区时
        # 助手报的「今天消耗」会和用量页对不上。
        now = datetime.now(tz=timezone(timedelta(hours=8))).replace(tzinfo=None)
        if norm_period == "today":
            from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif norm_period == "month":
            from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            from_dt = datetime(2000, 1, 1)
        db = _get_db()
        return _dump({
            "view": "usage",
            "period": norm_period,
            "from": from_dt,
            "to": now,
            "summary": db.get_llm_usage_summary(from_dt, now),
            "records": db.get_llm_usage_records(from_dt, now, limit=page_size),
        })

    if norm_view == "data_quality":
        rows, total = _data_quality_repository_instance().list_discrepancies(
            page=page,
            page_size=page_size,
        )
        return _dump({
            "view": "data_quality",
            "items": [_row_dict(row, _DATA_QUALITY_FIELDS) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })

    if norm_view == "notifications":
        rows, total = _notification_delivery_repository_instance().list_deliveries(
            page=page,
            page_size=page_size,
        )
        return _dump({
            "view": "notifications",
            "items": [_row_dict(row, _NOTIFICATION_DELIVERY_FIELDS) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })

    return _error(
        f"unknown view: {view}; expected config|usage|data_quality|notifications"
    )


def _handle_get_backtest_view(
    view: str = "summary",
    scope: str = "overall",
    stock_code: Optional[str] = None,
    skill_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    eval_window_days: Optional[int] = None,
    limit: int = 20,
    analysis_phase: Optional[str] = None,
) -> str:
    service = _backtest_service_instance()
    if view == "recent":
        return _dump(service.get_recent_evaluations(
            code=stock_code,
            eval_window_days=eval_window_days,
            limit=max(1, min(int(limit), 100)),
            analysis_phase=analysis_phase,
        ))
    if view != "summary":
        return _error(f"unknown view: {view}; expected summary|recent")
    if scope == "stock":
        if not stock_code:
            return _error("stock_code is required for scope=stock")
        return _dump(service.get_stock_summary(stock_code, eval_window_days=eval_window_days))
    if scope == "skill":
        if not skill_id:
            return _error("skill_id is required for scope=skill")
        return _dump(service.get_skill_summary(skill_id, eval_window_days=eval_window_days))
    if scope == "strategy":
        if not strategy_id:
            return _error("strategy_id is required for scope=strategy")
        return _dump(service.get_strategy_summary(strategy_id, eval_window_days=eval_window_days))
    if scope == "overall":
        return _dump(service.get_global_summary(eval_window_days=eval_window_days))
    return _error(f"unknown scope: {scope}")


def _handle_get_screening_view(
    view: str = "strategies",
    run_id: Optional[str] = None,
    strategy: str = "",
    market: str = "",
    limit: int = 20,
    topic: Optional[str] = None,
    top: int = 12,
) -> str:
    service = _screening_service_instance()
    if view == "strategies":
        return _dump(service.strategies())
    if view == "status":
        return _dump(service.status())
    if view == "history":
        if run_id:
            from fastapi import HTTPException

            try:
                return _dump(service.history_detail(run_id))
            except HTTPException as exc:
                detail = exc.detail
                if isinstance(detail, dict):
                    return _dump({
                        "error": detail.get("error") or "screening_history_error",
                        "message": detail.get("message"),
                        "status_code": exc.status_code,
                    })
                return _dump({
                    "error": "screening_history_error",
                    "message": str(detail),
                    "status_code": exc.status_code,
                })
        return _dump(service.history(limit=max(1, min(int(limit), 100)), strategy=strategy, market=market))
    if view == "hotspots":
        if topic:
            return _dump(service.hotspot_detail(topic=topic))
        return _dump(service.hotspots(top=max(1, min(int(top), 50))))
    if view == "source_history":
        return _dump(service.source_history(limit=max(1, min(int(limit), 100))))
    return _error(
        f"unknown view: {view}; expected strategies|status|history|hotspots|source_history"
    )


def _handle_get_signal_view(
    view: str = "signals",
    signal_id: Optional[int] = None,
    stock_code: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    horizon: Optional[str] = None,
    outcome: Optional[str] = None,
    eval_status: Optional[str] = None,
    skill_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "skill_stats":
        try:
            return _dump(_skill_performance_service_instance().get_stats(
                skill_id=skill_id,
                horizons=[horizon] if horizon else None,
            ))
        except ValueError as exc:
            return _error(str(exc))
    if view == "signals":
        if signal_id is not None:
            return _dump(_signal_service_instance().get_signal(int(signal_id)))
        return _dump(_signal_service_instance().list_signals(
            stock_code=stock_code,
            status=status,
            action=action,
            page=page,
            page_size=page_size,
        ))
    if view == "latest_active":
        if not stock_code:
            return _error("stock_code is required for view=latest_active")
        return _dump(_signal_service_instance().get_latest_active(stock_code=stock_code))
    if view == "outcomes":
        return _dump(_outcome_service_instance().list_outcomes(
            signal_id=signal_id,
            horizon=horizon,
            eval_status=eval_status,
            outcome=outcome,
            page=page,
            page_size=page_size,
        ))
    if view == "stats":
        unsupported = {
            "signal_id": signal_id,
            "stock_code": stock_code,
            "action": action,
            "outcome": outcome,
            "eval_status": eval_status,
            # skill_id 只服务于 skill_stats；stats 分支若静默忽略它，模型会把
            # 全量统计当成单 skill 的结论汇报。
            "skill_id": skill_id,
        }
        provided = [name for name, value in unsupported.items() if value not in (None, "")]
        if provided:
            return _dump({
                "error": "unsupported_filters_for_stats",
                "filters": provided,
                "message": "stats view supports only status and horizon filters",
            })
        return _dump(_outcome_service_instance().get_stats(
            horizons=[horizon] if horizon else None,
            statuses=[status] if status else None,
        ))
    return _error(
        f"unknown view: {view}; expected signals|latest_active|outcomes|stats|skill_stats"
    )


def _handle_get_fund_review_view(
    view: str = "reviews",
    fund_code: Optional[str] = None,
    horizon: Optional[str] = None,
    eval_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    if view == "nav_range":
        from datetime import date

        if not fund_code:
            return _error("fund_code is required for view=nav_range")
        if not start_date or not end_date:
            return _error("start_date and end_date are required for view=nav_range")
        try:
            parsed_start = date.fromisoformat(str(start_date))
            parsed_end = date.fromisoformat(str(end_date))
        except (TypeError, ValueError):
            return _error("start_date and end_date must be YYYY-MM-DD")
        try:
            return _dump(_fund_review_service_instance().get_nav_range(
                fund_code=str(fund_code),
                start_date=parsed_start,
                end_date=parsed_end,
            ))
        except ValueError as exc:
            return _error(str(exc))
    if horizon:
        from src.services.fund_nav_review_service import (
            FUND_NAV_REVIEW_HORIZON_ORDER,
        )

        if horizon not in FUND_NAV_REVIEW_HORIZON_ORDER:
            return _dump({
                "error": "invalid_horizon",
                "allowed": list(FUND_NAV_REVIEW_HORIZON_ORDER),
            })
    service = _fund_review_service_instance()
    if view == "reviews":
        try:
            return _dump(service.list_reviews(
                fund_code=fund_code,
                horizon=horizon,
                eval_status=eval_status,
                page=max(1, int(page)),
                page_size=max(1, min(int(page_size), 100)),
            ))
        except ValueError as exc:
            return _error(str(exc))
    if view == "stats":
        return _dump(service.get_stats(horizons=[horizon] if horizon else None))
    return _error(f"unknown view: {view}; expected reviews|stats|nav_range")


_PAPER_EQUITY_POINT_CAP = 365


def _handle_get_paper_view(
    view: str = "snapshot",
    account_id: Optional[int] = None,
    limit: int = 50,
    equity_limit: int = _PAPER_EQUITY_POINT_CAP,
) -> str:
    if view not in {"snapshot", "positions", "trades", "signals", "equity"}:
        return _error(f"unknown view: {view}; expected snapshot|positions|trades|signals|equity")
    service = _paper_service_instance()
    account = _paper_existing_account_id(account_id)
    if account is None:
        return _dump({"account": None, "message": "no paper trading account"})
    limit = max(1, min(int(limit), 200))
    if view == "snapshot":
        return _dump(service.get_snapshot(account))
    if view == "positions":
        return _dump(service.get_positions(account, limit=limit))
    if view == "trades":
        return _dump(service.get_trades(account, limit=limit))
    if view == "signals":
        return _dump(service.get_signals(account, limit=limit))
    curve = service.get_equity_curve(account)
    point_limit = max(1, min(int(equity_limit), _PAPER_EQUITY_POINT_CAP))
    total = len(curve)
    return _dump({
        "items": curve[-point_limit:] if total > point_limit else curve,
        "total": total,
        "limit": point_limit,
        "limit_cap": _PAPER_EQUITY_POINT_CAP,
        "truncated": total > point_limit,
    })


def _handle_get_portfolio_ledger_view(
    view: str = "accounts",
    account_id: Optional[int] = None,
    include_inactive: bool = False,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    direction: Optional[str] = None,
    action_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _portfolio_service_instance()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    if view == "accounts":
        return _dump({"accounts": service.list_accounts(include_inactive=include_inactive)})
    if view == "snapshot":
        try:
            return _dump(service.get_portfolio_snapshot_readonly(account_id=account_id))
        except ValueError as exc:
            return _error(str(exc))
    if view == "trades":
        return _dump(service.list_trade_events(
            account_id=account_id, symbol=symbol, side=side, page=page, page_size=page_size,
        ))
    if view == "cash":
        return _dump(service.list_cash_ledger_events(
            account_id=account_id, direction=direction, page=page, page_size=page_size,
        ))
    if view == "corporate":
        return _dump(service.list_corporate_action_events(
            account_id=account_id, symbol=symbol, action_type=action_type,
            page=page, page_size=page_size,
        ))
    return _error(f"unknown view: {view}; expected accounts|snapshot|trades|cash|corporate")


def _handle_get_intelligence_view(
    view: str = "sources",
    source_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    service = _intelligence_service_instance()
    if view == "sources":
        return _dump(service.list_sources())
    if view == "templates":
        return _dump(service.list_source_templates())
    if view == "items":
        return _dump(service.list_items(
            source_id=source_id,
            page=max(1, int(page)),
            page_size=max(1, min(int(page_size), 100)),
        ))
    return _error(f"unknown view: {view}; expected sources|templates|items")


def _handle_get_scheduler_view() -> str:
    from src.services.runtime_scheduler import get_runtime_scheduler_service

    scheduler = get_runtime_scheduler_service()
    if scheduler is None:
        return _error("scheduler is not running in this process")
    return _dump(scheduler.status())


get_history_view = ToolDefinition(
    name="get_history_view",
    description=(
        "读取分析历史：列表（可按股票/报告类型/日期筛选）或单条详情（record_id）。"
        "用于回答「上次分析结论是什么」「最近分析过哪些股票」。"
    ),
    parameters=[
        ToolParameter(name="stock_code", type="string", description="股票代码，可省略", required=False),
        ToolParameter(name="report_type", type="string", description="报告类型，例如 detailed", required=False),
        ToolParameter(name="start_date", type="string", description="起始日期 YYYY-MM-DD", required=False),
        ToolParameter(name="end_date", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="limit", type="integer", description="每页条数，默认 20，最大 100", required=False),
        ToolParameter(name="record_id", type="integer", description="提供时返回该历史记录详情", required=False),
        ToolParameter(
            name="part",
            type="string",
            description="record_id 详情分段：detail（默认）|news（原始情报）|markdown（报告原文）",
            required=False,
        ),
    ],
    handler=_handle_get_history_view,
    category="site_read",
    policy=_STOCK_READ_POLICY,
)

get_alert_view = ToolDefinition(
    name="get_alert_view",
    description="读取告警规则、触发记录或通知记录，用于解释「为什么没收到告警」。",
    parameters=[
        ToolParameter(name="view", type="string", description="rules|triggers|notifications", required=False),
        ToolParameter(name="rule_id", type="integer", description="规则 ID；rules 视图返回该规则详情", required=False),
        ToolParameter(name="trigger_id", type="integer", description="触发记录 ID；notifications 视图按该触发记录筛选", required=False),
        ToolParameter(name="enabled", type="boolean", description="按启用状态筛选规则", required=False),
        ToolParameter(name="alert_type", type="string", description="告警类型", required=False),
        ToolParameter(name="target", type="string", description="标的代码或关键词", required=False),
        ToolParameter(name="status", type="string", description="触发记录状态", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，默认 20，最大 100", required=False),
    ],
    handler=_handle_get_alert_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_analysis_task_view = ToolDefinition(
    name="get_analysis_task_view",
    description="读取分析任务队列：任务列表与统计，或单任务详情与运行流程。",
    parameters=[
        ToolParameter(name="view", type="string", description="list|detail", required=False),
        ToolParameter(name="task_id", type="string", description="view=detail 时必填", required=False),
        ToolParameter(name="status", type="string", description="逗号分隔的状态筛选", required=False),
        ToolParameter(name="limit", type="integer", description="最多返回任务数，默认 20", required=False),
    ],
    handler=_handle_get_analysis_task_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_config_view = ToolDefinition(
    name="get_config_view",
    description=(
        "读取非敏感系统配置、LLM 用量、数据质量差异或通知投递记录"
        "（密钥、Token、密码类字段永远不会返回）。"
        "用于回答「当前自选股是什么」「调度是否开启」「今天用了多少 token」。"
    ),
    parameters=[
        ToolParameter(
            name="view",
            type="string",
            description="config（默认）|usage|data_quality|notifications",
            required=False,
        ),
        ToolParameter(name="key", type="string", description="config 视图只返回指定配置键", required=False),
        ToolParameter(name="period", type="string", description="usage 视图区间：today|month|all", required=False),
        ToolParameter(name="page", type="integer", description="data_quality/notifications 页码，默认 1", required=False),
        ToolParameter(name="page_size", type="integer", description="data_quality/notifications 每页条数，最大 100", required=False),
    ],
    handler=_handle_get_config_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_backtest_view = ToolDefinition(
    name="get_backtest_view",
    description=(
        "读取回测/后验统计：整体、单股、skill 或策略摘要，或最近评估明细。"
        "用于回答「最近判断准不准」「某只股票历史回测如何」。"
    ),
    parameters=[
        ToolParameter(name="view", type="string", description="summary|recent", required=False),
        ToolParameter(name="scope", type="string", description="overall|stock|skill|strategy", required=False),
        ToolParameter(name="stock_code", type="string", description="股票代码；scope=stock 时必填，recent 视图可按其筛选", required=False),
        ToolParameter(name="skill_id", type="string", description="scope=skill 时必填", required=False),
        ToolParameter(name="strategy_id", type="string", description="scope=strategy 时必填", required=False),
        ToolParameter(name="eval_window_days", type="integer", description="评估窗口天数", required=False),
        ToolParameter(name="analysis_phase", type="string", description="recent 视图的阶段筛选", required=False),
        ToolParameter(name="limit", type="integer", description="recent 返回条数，最大 100", required=False),
    ],
    handler=_handle_get_backtest_view,
    category="site_read",
    policy=_STOCK_READ_POLICY,
)

get_screening_view = ToolDefinition(
    name="get_screening_view",
    description="读取选股策略列表、运行状态、历史结果、热点快照或数据源历史。",
    parameters=[
        ToolParameter(name="view", type="string", description="strategies|status|history|hotspots|source_history", required=False),
        ToolParameter(name="run_id", type="string", description="history 视图返回单次详情", required=False),
        ToolParameter(name="strategy", type="string", description="history 筛选策略", required=False),
        ToolParameter(name="market", type="string", description="history 筛选市场", required=False),
        ToolParameter(name="limit", type="integer", description="history 返回条数，最大 100", required=False),
        ToolParameter(name="topic", type="string", description="提供时返回该热点详情", required=False),
        ToolParameter(name="top", type="integer", description="热点条数，最大 50", required=False),
    ],
    handler=_handle_get_screening_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_signal_view = ToolDefinition(
    name="get_signal_view",
    description="读取决策信号、某标的当前有效信号、信号后验结果、统计或技能历史胜率。",
    parameters=[
        ToolParameter(name="view", type="string", description="signals|latest_active|outcomes|stats|skill_stats", required=False),
        ToolParameter(name="signal_id", type="integer", description="信号 ID", required=False),
        ToolParameter(name="stock_code", type="string", description="股票代码", required=False),
        ToolParameter(name="status", type="string", description="信号状态筛选", required=False),
        ToolParameter(name="action", type="string", description="信号动作筛选", required=False),
        ToolParameter(name="horizon", type="string", description="后验周期 1d/3d/5d/10d 等", required=False),
        ToolParameter(name="outcome", type="string", description="后验结果筛选", required=False),
        ToolParameter(name="eval_status", type="string", description="后验评估状态筛选", required=False),
        ToolParameter(name="skill_id", type="string", description="skill_stats 视图按技能筛选；省略为全部技能", required=False),
        ToolParameter(name="page", type="integer", description="页码，默认 1", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_signal_view,
    category="site_read",
    policy=_STOCK_READ_POLICY,
)

get_fund_review_view = ToolDefinition(
    name="get_fund_review_view",
    description=(
        "读取场外基金净值复盘台账、统计（区间收益、最大回撤）"
        "或指定日期的净值区间序列（nav_range）。"
    ),
    parameters=[
        ToolParameter(name="view", type="string", description="reviews|stats|nav_range", required=False),
        ToolParameter(name="fund_code", type="string", description="基金代码，可带 fund: 前缀；nav_range 时必填", required=False),
        ToolParameter(name="horizon", type="string", description="1m/3m/6m/1y", required=False),
        ToolParameter(name="eval_status", type="string", description="pending/evaluated/unable", required=False),
        ToolParameter(name="start_date", type="string", description="nav_range 起始日期 YYYY-MM-DD", required=False),
        ToolParameter(name="end_date", type="string", description="nav_range 结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_fund_review_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_paper_view = ToolDefinition(
    name="get_paper_view",
    description="读取模拟盘账户快照、持仓、成交、信号或净值曲线。",
    parameters=[
        ToolParameter(name="view", type="string", description="snapshot|positions|trades|signals|equity", required=False),
        ToolParameter(name="account_id", type="integer", description="默认模拟盘账户", required=False),
        ToolParameter(name="limit", type="integer", description="明细条数，最大 200", required=False),
        ToolParameter(name="equity_limit", type="integer", description="净值曲线保留的最近点数，最大 365", required=False),
    ],
    handler=_handle_get_paper_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_portfolio_ledger_view = ToolDefinition(
    name="get_portfolio_ledger_view",
    description="读取真实持仓账户、组合快照、交易流水、资金流水或公司行动流水。",
    parameters=[
        ToolParameter(name="view", type="string", description="accounts|snapshot|trades|cash|corporate", required=False),
        ToolParameter(name="account_id", type="integer", description="账户 ID", required=False),
        ToolParameter(name="include_inactive", type="boolean", description="accounts 视图含停用账户", required=False),
        ToolParameter(name="symbol", type="string", description="标的筛选", required=False),
        ToolParameter(name="side", type="string", description="买卖方向", required=False),
        ToolParameter(name="direction", type="string", description="资金方向", required=False),
        ToolParameter(name="action_type", type="string", description="公司行动类型", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_portfolio_ledger_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_intelligence_view = ToolDefinition(
    name="get_intelligence_view",
    description="读取情报源配置、模板或已抓取情报条目。",
    parameters=[
        ToolParameter(name="view", type="string", description="sources|templates|items", required=False),
        ToolParameter(name="source_id", type="integer", description="items 视图按来源筛选", required=False),
        ToolParameter(name="page", type="integer", description="页码", required=False),
        ToolParameter(name="page_size", type="integer", description="每页条数，最大 100", required=False),
    ],
    handler=_handle_get_intelligence_view,
    category="site_read",
    policy=_READ_POLICY,
)

get_scheduler_view = ToolDefinition(
    name="get_scheduler_view",
    description="读取当前进程内调度器状态与下次运行时间。",
    parameters=[],
    handler=_handle_get_scheduler_view,
    category="site_read",
    policy=_READ_POLICY,
)

ALL_SITE_READ_TOOLS = [
    get_history_view,
    get_alert_view,
    get_analysis_task_view,
    get_config_view,
    get_backtest_view,
    get_screening_view,
    get_signal_view,
    get_fund_review_view,
    get_paper_view,
    get_portfolio_ledger_view,
    get_intelligence_view,
    get_scheduler_view,
]
