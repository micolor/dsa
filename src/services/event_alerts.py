# -*- coding: utf-8 -*-
"""Runtime helpers for event-driven alert rules.

Event alerts surface structured market facts rather than indicator/quote math:
- ``event_dragon_tiger``: 该股近期是否上龙虎榜（龙虎榜上榜记录数）。
- ``event_capital_flow``: 主力资金净流入绝对值是否达到阈值。
- ``event_announcement``: 近期是否有重要公告/事件。

事实来源复用现成 ``DataFetcherManager``（fail-open + retry + 缓存）的
``get_dragon_tiger_context`` / ``get_capital_flow_context``，以及
``search_service.get_search_service`` 的事件检索。所有事实获取都收敛到
模块级 ``_fetch_event_fact`` 单一边界，便于离线单测 mock。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from data_provider.base import normalize_stock_code


EVENT_ALERT_TYPES = frozenset({
    "event_dragon_tiger",
    "event_capital_flow",
    "event_announcement",
})

EVENT_DATA_SOURCES = {
    "event_dragon_tiger": "dragon_tiger",
    "event_capital_flow": "capital_flow",
    "event_announcement": "stock_events",
}

# 默认阈值：龙虎榜上榜 ≥1 次；主力净流入绝对值 ≥1 亿；重要事件 ≥1 条。
_DEFAULT_MIN_RECENT_COUNT = 1
_DEFAULT_MIN_ABS_INFLOW = 100_000_000.0
_DEFAULT_MIN_COUNT = 1


@dataclass
class EventAlert:
    """Runtime alert for a per-symbol event-driven rule."""

    target_scope: str
    target: str
    alert_type: str
    parameters: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    stock_code: str = ""

    def __post_init__(self) -> None:
        if not self.stock_code:
            self.stock_code = normalize_stock_code(self.target)


def normalize_event_alert_parameters(alert_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Validate/normalize event alert parameters, mirroring the other alert families."""
    if alert_type not in EVENT_ALERT_TYPES:
        raise ValueError(f"unsupported event alert_type: {alert_type}")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")

    if alert_type == "event_dragon_tiger":
        min_recent_count = _positive_int(parameters.get("min_recent_count"), "min_recent_count", _DEFAULT_MIN_RECENT_COUNT)
        return {"min_recent_count": min_recent_count}

    if alert_type == "event_capital_flow":
        min_abs_inflow = _positive_number(parameters.get("min_abs_inflow"), "min_abs_inflow", _DEFAULT_MIN_ABS_INFLOW)
        return {"min_abs_inflow": min_abs_inflow}

    if alert_type == "event_announcement":
        min_count = _positive_int(parameters.get("min_count"), "min_count", _DEFAULT_MIN_COUNT)
        return {"min_count": min_count}

    raise ValueError(f"unsupported event alert_type: {alert_type}")


def event_threshold(alert_type: str, parameters: Dict[str, Any]) -> Optional[float]:
    """Return the numeric threshold used for display/dedup, or None when not applicable."""
    if alert_type == "event_dragon_tiger":
        return float(parameters.get("min_recent_count") or _DEFAULT_MIN_RECENT_COUNT)
    if alert_type == "event_capital_flow":
        return float(parameters.get("min_abs_inflow") or _DEFAULT_MIN_ABS_INFLOW)
    if alert_type == "event_announcement":
        return float(parameters.get("min_count") or _DEFAULT_MIN_COUNT)
    return None


def evaluate_event_alert(
    rule: EventAlert,
    *,
    cache: Optional[Dict[Any, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate a single event rule against the latest event facts for its symbol.

    Sync + fail-open: any fetch failure surfaces as ``not_triggered`` / ``degraded``
    rather than raising, so one broken source never blocks the alert pipeline.
    """
    alert_type = rule.alert_type
    threshold = event_threshold(alert_type, rule.parameters)

    try:
        fact = _cached_event_fact(alert_type, rule.stock_code, rule.metadata.get("stock_name"), cache)
    except Exception as exc:
        return _event_result(
            rule,
            triggered=False,
            observed_value=None,
            threshold=threshold,
            message=f"event fact unavailable: {exc}",
            record_status="degraded",
            diagnostics={"error": str(exc)[:200]},
        )

    status = str(fact.get("status") or "not_supported")

    if alert_type == "event_dragon_tiger":
        recent_count = int(fact.get("recent_count", 0) or 0)
        observed = float(recent_count)
        triggered = bool(fact.get("is_on_list", False)) and recent_count >= int(threshold or 0)
        message = (
            f"{rule.stock_code} 近 {recent_count} 次登上龙虎榜（要求 ≥{int(threshold or 0)}）"
            if triggered
            else f"{rule.stock_code} 近期未达到龙虎榜条件"
        )
        diagnostics = {
            "event": "dragon_tiger",
            "is_on_list": bool(fact.get("is_on_list", False)),
            "recent_count": recent_count,
            "latest_date": fact.get("latest_date"),
            "data_status": status,
        }
        return _event_result(
            rule,
            triggered=triggered,
            observed_value=observed,
            threshold=threshold,
            message=message,
            record_status="triggered" if triggered else None,
            data_timestamp=_parse_event_date(fact.get("latest_date")),
            diagnostics=diagnostics,
        )

    if alert_type == "event_capital_flow":
        main_net_inflow = fact.get("main_net_inflow")
        abs_inflow = abs(float(main_net_inflow)) if main_net_inflow is not None else None
        triggered = abs_inflow is not None and abs_inflow >= float(threshold or 0)
        message = (
            f"{rule.stock_code} 主力净流入 {abs_inflow:,.0f} 元（要求 ≥{float(threshold or 0):,.0f}）"
            if triggered
            else f"{rule.stock_code} 主力资金净流入未到达阈值"
        )
        diagnostics = {
            "event": "capital_flow",
            "main_net_inflow": main_net_inflow,
            "abs_inflow": abs_inflow,
            "data_status": status,
        }
        return _event_result(
            rule,
            triggered=triggered,
            observed_value=abs_inflow,
            threshold=threshold,
            message=message,
            record_status="triggered" if triggered else None,
            diagnostics=diagnostics,
        )

    if alert_type == "event_announcement":
        count = int(fact.get("count", 0) or 0)
        observed = float(count)
        triggered = count >= int(threshold or 0)
        message = (
            f"{rule.stock_code} 近期 {count} 条重要事件（要求 ≥{int(threshold or 0)}）"
            if triggered
            else f"{rule.stock_code} 近期未发现重要事件"
        )
        diagnostics = {
            "event": "announcement",
            "count": count,
            "data_status": status,
        }
        return _event_result(
            rule,
            triggered=triggered,
            observed_value=observed,
            threshold=threshold,
            message=message,
            record_status="triggered" if triggered else None,
            diagnostics=diagnostics,
        )

    return _event_result(
        rule,
        triggered=False,
        observed_value=None,
        threshold=None,
        message=f"unsupported event alert_type: {alert_type}",
        record_status="failed",
        diagnostics={"error": "unsupported_event_alert_type"},
    )


def _cached_event_fact(
    alert_type: str,
    stock_code: str,
    stock_name: Optional[Any],
    cache: Optional[Dict[Any, Any]],
) -> Dict[str, Any]:
    cache_key = ("event", alert_type, normalize_stock_code(stock_code))
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    fact = _fetch_event_fact(alert_type, normalize_stock_code(stock_code), stock_name)
    if cache is not None:
        cache[cache_key] = fact
    return fact


def _fetch_event_fact(alert_type: str, stock_code: str, stock_name: Optional[Any] = None) -> Dict[str, Any]:
    """单一事实获取边界：仅此函数访问数据源，离线单测对其进行 mock。"""
    if alert_type == "event_dragon_tiger":
        return _fetch_dragon_tiger_fact(stock_code)
    if alert_type == "event_capital_flow":
        return _fetch_capital_flow_fact(stock_code)
    if alert_type == "event_announcement":
        return _fetch_announcement_fact(stock_code, stock_name)
    return {"status": "not_supported"}


def _fetch_dragon_tiger_fact(stock_code: str) -> Dict[str, Any]:
    from data_provider import DataFetcherManager

    block = DataFetcherManager().get_dragon_tiger_context(stock_code)
    data = block.get("data") or {}
    return {
        "status": block.get("status") or "not_supported",
        "is_on_list": bool(data.get("is_on_list", False)),
        "recent_count": int(data.get("recent_count", 0) or 0),
        "latest_date": data.get("latest_date"),
    }


def _fetch_capital_flow_fact(stock_code: str) -> Dict[str, Any]:
    from data_provider import DataFetcherManager

    block = DataFetcherManager().get_capital_flow_context(stock_code)
    data = block.get("data") or {}
    stock_flow = data.get("stock_flow") or {}
    return {
        "status": block.get("status") or "not_supported",
        "main_net_inflow": _safe_float(stock_flow.get("main_net_inflow")),
    }


def _fetch_announcement_fact(stock_code: str, stock_name: Optional[Any] = None) -> Dict[str, Any]:
    from src.search_service import get_search_service

    service = get_search_service()
    if service is None:
        return {"status": "not_supported", "count": 0}
    try:
        response = service.search_stock_events(stock_code, str(stock_name or stock_code))
        results = getattr(response, "results", None) or []
        return {"status": "ok", "count": int(len(results))}
    except Exception as exc:  # noqa: BLE001 - fail-open
        return {"status": "failed", "count": 0, "error": str(exc)[:200]}


def _event_result(
    rule: EventAlert,
    *,
    triggered: bool,
    observed_value: Optional[float],
    threshold: Optional[float],
    message: str,
    record_status: Optional[str] = None,
    data_timestamp: Optional[Any] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_status = "triggered" if triggered else "not_triggered"
    if triggered and record_status is None:
        record_status = "triggered"
    return {
        "rule_id": int(rule.metadata.get("persisted_rule_id", 0) or 0),
        "status": effective_status,
        "record_status": record_status,
        "triggered": triggered,
        "observed_value": observed_value,
        "threshold": threshold,
        "data_source": EVENT_DATA_SOURCES.get(rule.alert_type),
        "data_timestamp": data_timestamp,
        "reason": message,
        "message": message,
        "diagnostics": json.dumps(diagnostics or {}, ensure_ascii=False, sort_keys=True),
    }


def _parse_event_date(value: Any) -> Optional[Any]:
    if value is None:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.combine(__import__("datetime").date.fromisoformat(str(value)), datetime.min.time())
        except ValueError:
            return None


def _positive_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    if number < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return number


def _positive_number(value: Any, field_name: str, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return number


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
