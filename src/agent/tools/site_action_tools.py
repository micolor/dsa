# -*- coding: utf-8 -*-
"""Low-risk, reversible site operations that execute immediately.

Every handler returns the ``action_executed`` envelope; the runner emits it to the
chat UI. These tools must be idempotent or cheaply reversible, and must declare an
``undo_hint`` so the user can always see how to reverse the effect.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
from src.services.watchlist_service import (
    list_named_watchlists,
    read_watchlist_codes,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
    write_watchlist_codes,
)

_DIRECT_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="direct",
    risk_level="low",
    undo_hint="再次调用本工具并把参数改为相反值即可恢复",
    cancellation_safe=False,
)

# Factory hooks. Tests may replace these with callables that return a service
# double; the default lazily imports the real service on first use.
_alert_service = None
_paper_service = None
_portfolio_service = None
_intelligence_service = None
_system_config_service = None


def _alert_service_instance():
    global _alert_service
    if _alert_service is None:
        from src.services.alert_service import AlertService

        _alert_service = AlertService
    return _alert_service()


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


def _system_config_service_instance():
    global _system_config_service
    if _system_config_service is None:
        from src.services.system_config_service import SystemConfigService

        _system_config_service = SystemConfigService
    return _system_config_service()


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _dump({"error": message})


def _watchlist_match_key(code: str) -> str:
    from data_provider.base import normalize_stock_code

    normalized = normalize_stock_code(code.strip())
    if re.fullmatch(r"\d{5}", normalized):
        return f"HK{normalized}"
    return normalized.upper()


def _resolve_named_list(list_name: str) -> Optional[str]:
    raw = str(list_name or "").strip()
    if not raw:
        return None
    service = _system_config_service_instance()
    named = list_named_watchlists(service)
    wanted = resolve_watchlist_key(raw)
    if any(item["key"] == wanted for item in named):
        return raw
    available = [
        item["name"] for item in named
        if resolve_watchlist_key(item["name"]) == item["key"]
    ]
    raise ValueError(
        f"自选列表「{raw}」不存在；可用列表：{'、'.join(available) or '无'}"
    )


def _handle_manage_watchlist(
    action: str,
    symbol: str,
    list_name: str = "",
) -> str:
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("add", "remove"):
        return _error(f"action 必须是 add/remove，收到 {action!r}")
    try:
        norm_code = validate_and_normalize_stock_code(str(symbol or ""))
        resolved_list = _resolve_named_list(list_name)
    except ValueError as exc:
        return _error(str(exc))

    service = _system_config_service_instance()
    codes: List[str] = read_watchlist_codes(service, resolved_list)
    keys = [_watchlist_match_key(code) for code in codes]
    target_key = _watchlist_match_key(norm_code)
    changed = False

    if norm_action == "add":
        if target_key not in keys:
            codes.append(str(symbol).strip())
            changed = True
    else:
        if target_key in keys:
            codes.pop(keys.index(target_key))
            changed = True

    if changed:
        write_watchlist_codes(service, codes, resolved_list)

    target = f"自选列表「{resolved_list}」" if resolved_list else "自选"
    verb = "加入" if norm_action == "add" else "移出"
    state = "" if changed else "（原本已是该状态，未做改动）"
    return _dump({
        "kind": "watchlist_change",
        "summary": f"已把「{norm_code}」{verb}{target}{state}",
        "result": {
            "action": norm_action,
            "stock_code": norm_code,
            "list_name": resolved_list,
            "stock_codes": codes,
            "unchanged": not changed,
        },
        "undo_hint": (
            "再次调用 manage_watchlist，传入相反的 action 即可恢复"
            if changed else "本次未改动任何数据，无需撤销"
        ),
    })


def _handle_set_alert_rule_enabled(rule_id: int, enabled: bool) -> str:
    try:
        parsed_id = int(rule_id)
    except (TypeError, ValueError):
        return _error(f"rule_id 必须是整数，收到 {rule_id!r}")
    rule = _alert_service_instance().enable_rule(parsed_id, bool(enabled))
    state = "启用" if enabled else "停用"
    return _dump({
        "kind": "alert_rule_toggle",
        "summary": f"已{state}告警规则 #{parsed_id}",
        "result": {"rule": rule},
        "undo_hint": f"再次调用并传 enabled={str(not enabled).lower()} 即可恢复",
    })


def _handle_dry_run_alert_rule(rule_id: int) -> str:
    try:
        parsed_id = int(rule_id)
    except (TypeError, ValueError):
        return _error(f"rule_id 必须是整数，收到 {rule_id!r}")
    result = _alert_service_instance().test_rule(parsed_id)
    evaluated = int(result.get("evaluated_count") or 0)
    triggered = int(result.get("triggered_count") or 0)
    if result.get("status") == "evaluation_error":
        match_text = f"评估出错（已评估 {evaluated} 个标的）"
    elif triggered:
        match_text = f"{triggered}/{evaluated} 个标的按当前数据会触发"
    else:
        match_text = f"{evaluated} 个标的按当前数据均不会触发"
    return _dump({
        "kind": "alert_rule_test",
        "summary": (
            f"已预览告警规则 #{parsed_id}：{match_text}；"
            "仅做本地评估，未发送任何通知，外部通知渠道未被验证"
        ),
        "result": {
            "rule_id": parsed_id,
            "status": result.get("status"),
            "evaluated_count": evaluated,
            "triggered_count": triggered,
            "degraded_count": int(result.get("degraded_count") or 0),
            "skipped_count": int(result.get("skipped_count") or 0),
            # 明确告诉模型与前端：这次调用没有验证任何外部投递。
            "delivery_verified": False,
            "evaluation": result,
        },
        "undo_hint": "预览只读取数据并做本地判断，不发送通知、不修改规则，无需撤销",
    })


def _handle_refresh_fx_rates(
    account_id: Optional[int] = None,
    as_of: Optional[str] = None,
) -> str:
    from datetime import date

    try:
        parsed_as_of = date.fromisoformat(as_of) if as_of else None
        parsed_account = int(account_id) if account_id is not None else None
    except (TypeError, ValueError):
        return _error("account_id 必须是整数，as_of 必须是 YYYY-MM-DD")
    result = _portfolio_service_instance().refresh_fx_rates(
        account_id=parsed_account,
        as_of=parsed_as_of,
    )
    account_count = int(result.get("account_count") or 0)
    if not result.get("refresh_enabled", True):
        summary = "汇率在线刷新当前已禁用，未更新任何汇率"
    elif account_count == 0:
        summary = "当前没有活动账户，未刷新汇率"
    else:
        parts = [
            f"更新 {int(result.get('updated_count') or 0)} 对",
            f"沿用旧值 {int(result.get('stale_count') or 0)} 对",
        ]
        if int(result.get("error_count") or 0):
            parts.append(f"失败 {int(result.get('error_count') or 0)} 对")
        summary = f"已尝试刷新组合汇率（{'，'.join(parts)}）"
    return _dump({
        "kind": "fx_refresh",
        "summary": summary,
        "result": result,
        "undo_hint": "汇率缓存会被下次刷新覆盖，无需手动撤销",
    })


def _handle_refresh_paper_valuation(
    account_id: Optional[int] = None,
    as_of: Optional[str] = None,
    force: bool = False,
) -> str:
    from datetime import date

    try:
        account = int(account_id) if account_id is not None else None
        parsed_as_of = date.fromisoformat(as_of) if as_of else None
    except (TypeError, ValueError):
        # 只把参数解析失败归因为输入错误；服务调用失败必须原样抛出，
        # 不能被误报成「account_id 必须是整数」。
        return _error("account_id 必须是整数，as_of 必须是 YYYY-MM-DD")

    service = _paper_service_instance()
    if account is None:
        # 直执行动作允许按需创建默认模拟盘账户；真实载荷键是 ``account_id``
        # （见 PaperService._account_payload），不是 ``id``。
        account = int(service.get_or_create_account()["account_id"])
    result = service.run_daily_valuation(
        account_id=account, as_of_date=parsed_as_of, force=bool(force)
    )
    return _dump({
        "kind": "paper_refresh",
        "summary": "已获取模拟盘估值快照（同日快照已存在时会复用，不重复计价）",
        "result": result,
        "undo_hint": "估值快照会随下次刷新更新，无需手动撤销",
    })


def _handle_test_intelligence_source(source_id: int) -> str:
    try:
        parsed_id = int(source_id)
    except (TypeError, ValueError):
        return _error(f"source_id 必须是整数，收到 {source_id!r}")
    result = _intelligence_service_instance().fetch_source(parsed_id, dry_run=True)
    fetched = int(result.get("fetched_count") or 0)
    return _dump({
        "kind": "intel_source_test",
        "summary": f"已试抓取情报源 #{parsed_id}：获取 {fetched} 条，未写入情报库",
        "result": result,
        "undo_hint": "试抓取只读取外部数据，不写入情报库，无需撤销",
    })


def _handle_test_llm_channel(name: str) -> str:
    import os

    from src.config import get_config

    channel_name = str(name or "").strip()
    channels = getattr(get_config(), "llm_channels", []) or []
    channel = next(
        (item for item in channels if str(item.get("name", "")).strip() == channel_name),
        None,
    )
    if channel is None:
        available = "、".join(str(item.get("name")) for item in channels) or "无"
        return _error(f"LLM 渠道「{channel_name}」不存在；可用渠道：{available}")

    keys = channel.get("api_keys") or []
    if not keys:
        return _error(f"LLM 渠道「{channel_name}」没有可用的 API Key")
    if os.getenv("DSA_AGENT_ALLOW_SECRET_TEST", "true").lower() == "false":
        return _error("当前环境禁止由助手直接测试 LLM 渠道")

    result = _system_config_service_instance().test_llm_channel(
        name=channel_name,
        protocol=str(channel.get("protocol") or ""),
        api_surface=str(channel.get("api_surface") or "chat_completions"),
        base_url=str(channel.get("base_url") or ""),
        api_key=str(keys[0]),
        models=list(channel.get("models") or []),
        enabled=bool(channel.get("enabled", True)),
    )
    if result.get("success"):
        summary = f"LLM 渠道「{channel_name}」连接测试通过"
    else:
        reason = str(result.get("message") or result.get("error") or "未知原因")
        summary = f"LLM 渠道「{channel_name}」连接测试未通过：{reason[:120]}"
    return _dump({
        "kind": "llm_channel_test",
        "summary": summary,
        "result": {key: value for key, value in result.items() if "key" not in key.lower()},
        "undo_hint": "测试不修改配置，无需撤销",
    })


manage_watchlist = ToolDefinition(
    name="manage_watchlist",
    description=(
        "把股票加入或移出自选（默认列表或已存在的命名列表）。这是低风险直执行操作，"
        "会立即写入配置并返回执行结果。列表名不存在时必须报错，不得臆造新列表。"
    ),
    parameters=[
        ToolParameter(name="action", type="string", description="add|remove", required=True),
        ToolParameter(name="symbol", type="string", description="股票代码", required=True),
        ToolParameter(name="list_name", type="string", description="命名自选列表，省略为默认自选", required=False),
    ],
    handler=_handle_manage_watchlist,
    category="site_action",
    policy=_DIRECT_POLICY,
)

set_alert_rule_enabled = ToolDefinition(
    name="set_alert_rule_enabled",
    description="启用或停用一条已存在的告警规则。",
    parameters=[
        ToolParameter(name="rule_id", type="integer", description="告警规则 ID", required=True),
        ToolParameter(name="enabled", type="boolean", description="true 启用 / false 停用", required=True),
    ],
    handler=_handle_set_alert_rule_enabled,
    category="site_action",
    policy=_DIRECT_POLICY,
)

dry_run_alert_rule = ToolDefinition(
    name="dry_run_alert_rule",
    description=(
        "对一条告警规则做一次只读评估（试运行）：按当前数据判断它会命中哪些标的、"
        "是否会触发，但不发送任何通知，也不验证外部通知渠道。"
    ),
    parameters=[
        ToolParameter(name="rule_id", type="integer", description="告警规则 ID", required=True),
    ],
    handler=_handle_dry_run_alert_rule,
    category="site_action",
    policy=_DIRECT_POLICY,
)

refresh_fx_rates = ToolDefinition(
    name="refresh_fx_rates",
    description="刷新组合汇率缓存。",
    parameters=[
        ToolParameter(name="account_id", type="integer", description="账户 ID，省略为全部账户", required=False),
        ToolParameter(name="as_of", type="string", description="汇率日期 YYYY-MM-DD", required=False),
    ],
    handler=_handle_refresh_fx_rates,
    category="site_action",
    policy=_DIRECT_POLICY,
)

refresh_paper_valuation = ToolDefinition(
    name="refresh_paper_valuation",
    description="重新计算模拟盘当日估值。",
    parameters=[
        ToolParameter(name="account_id", type="integer", description="模拟盘账户 ID", required=False),
        ToolParameter(name="as_of", type="string", description="估值日期 YYYY-MM-DD", required=False),
        ToolParameter(name="force", type="boolean", description="覆盖已有同日快照", required=False),
    ],
    handler=_handle_refresh_paper_valuation,
    category="site_action",
    policy=_DIRECT_POLICY,
)

test_intelligence_source = ToolDefinition(
    name="test_intelligence_source",
    description="对已保存的情报源做一次试抓取，不写入情报库。",
    parameters=[
        ToolParameter(name="source_id", type="integer", description="情报源 ID", required=True),
    ],
    handler=_handle_test_intelligence_source,
    category="site_action",
    policy=_DIRECT_POLICY,
)

test_llm_channel = ToolDefinition(
    name="test_llm_channel",
    description="测试一个已保存的 LLM 渠道是否可用；密钥由服务端读取，绝不作为参数传递。",
    parameters=[
        ToolParameter(name="name", type="string", description="已保存渠道名", required=True),
    ],
    handler=_handle_test_llm_channel,
    category="site_action",
    policy=_DIRECT_POLICY,
)

ALL_SITE_ACTION_TOOLS = [
    manage_watchlist,
    set_alert_rule_enabled,
    dry_run_alert_rule,
    refresh_fx_rates,
    refresh_paper_valuation,
    test_intelligence_source,
    test_llm_channel,
]
