"""Agent tool for proposing alert rule changes for user confirmation.

The ``propose_alert_rule_change`` tool **never persists anything**: it validates a
well-formed alert rule create/update/delete proposal (matching the backend
``AlertRuleCreateRequest`` / ``AlertRuleUpdateRequest`` contracts) and returns a
``{"kind": ..., "summary": ..., "proposal": ...}`` envelope so the runner can surface
it as an ``action_proposal`` SSE event. The actual rule is only written after the user
confirms, via the existing ``/api/v1/alerts/rules`` endpoints (triggered from the web
client). This keeps the "confirm before write" semantics and reuses the existing
validation, permission and notification paths.
"""

from typing import Any, Dict, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
# reason 的长度上限与 action_tools 的提案工具共用一份：它们的 reason 都汇进同一条
# summary → SSE 事件 → 确认卡片通道，各写一份上限迟早会漂移。
from src.agent.tools.action_tools import _normalize_reason, _require_positive_int
from src.services.alert_service import (
    AlertService,
    AlertServiceError,
    SUPPORTED_SEVERITIES,
    SYMBOL_ALERT_TYPES,
    normalize_alert_parameters,
)

# Canonical symbol alert types with a human-facing Chinese label (matches docs/alerts.md).
_ALERT_TYPE_LABELS: Dict[str, str] = {
    "price_cross": "价格上穿/下穿",
    "price_change_percent": "涨跌幅",
    "volume_spike": "成交量异动",
    "ma_price_cross": "均线交叉",
    "rsi_threshold": "RSI 阈值",
    "macd_cross": "MACD 交叉",
    "kdj_cross": "KDJ 交叉",
    "cci_threshold": "CCI 阈值",
}

_ALERT_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="proposal",
    risk_level="high",
    # Not cancellation-safe for the Codex read-only surface: proposal tools are an
    # interactive confirm-flow surface for the default (LiteLLM) chat path only.
    cancellation_safe=False,
)

# Parameter key documentation per type, mirroring docs/alerts.md so the LLM can
# produce valid payloads. `parameters` is passed through as a free-form object and
# validated by normalize_alert_parameters.
_PARAMETER_DOC = (
    "按 alert_type 选择参数对象：\n"
    "- price_cross: {direction: 'above'|'below', price: 目标价}\n"
    "- price_change_percent: {direction: 'up'|'down', change_pct: 涨跌幅百分比}\n"
    "- volume_spike: {multiplier: 较20日均量的放大倍数}\n"
    "- ma_price_cross: {direction: 'above'|'below', window: 均线周期}\n"
    "- rsi_threshold: {direction: 'above'|'below', period: 周期, threshold: 0-100}\n"
    "- macd_cross: {direction: 'bullish_cross'|'bearish_cross', fast_period, slow_period, signal_period}\n"
    "- kdj_cross: {direction: 'bullish_cross'|'bearish_cross', period, k_period, d_period}\n"
    "- cci_threshold: {direction: 'above'|'below', period, threshold}\n"
    "direction 含义：price_cross/ma_price_cross 用 above(上穿)/below(下穿)；"
    "price_change_percent 用 up(涨幅)/down(跌幅)；"
    "rsi_threshold/cci_threshold 用 above(高于)/below(低于)；"
    "macd_cross/kdj_cross 用 bullish_cross(金叉)/bearish_cross(死叉)。"
)


def _fmt_num(value: Any) -> str:
    """Format a normalized numeric parameter without a trailing ``.0`` for whole values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _build_summary(target: str, alert_type: str, parameters: Dict[str, Any]) -> str:
    """Build a short human-readable Chinese summary for the confirmation card."""
    if alert_type == "price_cross":
        verb = "上穿" if parameters.get("direction") == "above" else "下穿"
        return f"「{target}」价格{verb} ¥{_fmt_num(parameters.get('price'))}"
    if alert_type == "price_change_percent":
        direction = "涨幅" if parameters.get("direction") == "up" else "跌幅"
        return f"「{target}」{direction}达到 {_fmt_num(parameters.get('change_pct'))}%"
    if alert_type == "volume_spike":
        return f"「{target}」成交量放大 {_fmt_num(parameters.get('multiplier'))} 倍以上"
    if alert_type == "ma_price_cross":
        verb = "上穿" if parameters.get("direction") == "above" else "下穿"
        return f"「{target}」收盘价{verb} MA{_fmt_num(parameters.get('window'))}"
    if alert_type == "rsi_threshold":
        verb = "高于" if parameters.get("direction") == "above" else "低于"
        return f"「{target}」RSI({_fmt_num(parameters.get('period'))}) {verb} {_fmt_num(parameters.get('threshold'))}"
    if alert_type == "macd_cross":
        label = "金叉" if parameters.get("direction") == "bullish_cross" else "死叉"
        return f"「{target}」MACD {label}"
    if alert_type == "kdj_cross":
        label = "金叉" if parameters.get("direction") == "bullish_cross" else "死叉"
        return f"「{target}」KDJ {label}"
    if alert_type == "cci_threshold":
        verb = "高于" if parameters.get("direction") == "above" else "低于"
        return f"「{target}」CCI({_fmt_num(parameters.get('period'))}) {verb} {_fmt_num(parameters.get('threshold'))}"
    return f"「{target}」{_ALERT_TYPE_LABELS.get(alert_type, alert_type)}"


def _handle_propose_alert_rule_change(
    action: str,
    target: str = "",
    alert_type: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    name: str = "",
    severity: str = "info",
    rule_id: Optional[int] = None,
    reason: str = "",
) -> Dict[str, Any]:
    """Validate an alert rule create/update/delete proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("create", "update", "delete"):
        return {"error": f"action must be create/update/delete, got {action!r}"}
    if norm_action in ("update", "delete"):
        parsed_rule_id, err = _require_positive_int(rule_id, "rule_id")
        if err:
            return {"error": err}
        if norm_action == "delete":
            return {
                "kind": "alert_delete",
                "summary": f"删除告警规则 #{parsed_rule_id}",
                "proposal": {"rule_id": parsed_rule_id},
            }
    else:
        parsed_rule_id = None

    norm_type = str(alert_type or "").strip().lower()
    if not norm_type:
        return {"error": "alert_type 不能为空"}
    if norm_type not in SYMBOL_ALERT_TYPES:
        return {"error": f"unsupported alert_type: {norm_type}; supported: {sorted(SYMBOL_ALERT_TYPES)}"}
    norm_severity = str(severity or "info").strip().lower()
    if norm_severity not in SUPPORTED_SEVERITIES:
        return {"error": f"unsupported severity: {norm_severity}"}
    try:
        norm_params = normalize_alert_parameters(norm_type, parameters or {})
    except AlertServiceError as exc:
        return {"error": str(exc)}
    norm_target = str(target or "").strip()
    if not norm_target:
        return {"error": "target must not be empty"}
    # AlertRuleCreateRequest.target max_length=64; reject rather than emit a payload
    # the confirm-time POST will 422 on.
    if len(norm_target) > 64:
        return {"error": f"target 过长（最多 64 字符）: {norm_target!r}"}

    proposal = {
        "name": (str(name or "").strip() or AlertService._default_rule_name(
            target=norm_target,
            alert_type=norm_type,
            parameters=norm_params,
        ))[:64],
        "target_scope": "single_symbol",
        "target": norm_target,
        "alert_type": norm_type,
        "parameters": norm_params,
        "severity": norm_severity,
    }
    kind = "alert_update" if norm_action == "update" else "alert_create"
    if norm_action == "update":
        proposal["rule_id"] = int(parsed_rule_id)
    summary = _build_summary(norm_target, norm_type, norm_params)
    if norm_action == "update":
        summary = f"更新告警规则：{summary}"
    norm_reason = _normalize_reason(reason)
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"
    return {"kind": kind, "summary": summary, "proposal": proposal}


propose_alert_rule_change_tool = ToolDefinition(
    name="propose_alert_rule_change",
    description=(
        "生成告警规则的创建/更新/删除提案，交给用户确认；本工具不写入任何规则。"
        "创建和更新需要 target/alert_type/parameters；更新和删除需要 rule_id。\n"
        f"{_PARAMETER_DOC}"
    ),
    parameters=[
        ToolParameter(
            name="action",
            type="string",
            description="create|update|delete",
            required=True,
            enum=["create", "update", "delete"],
        ),
        ToolParameter(
            name="target",
            type="string",
            description="股票代码，支持裸码或带前缀（如 600519 / HK00700 / AAPL）；create/update 必填",
            required=False,
        ),
        ToolParameter(
            name="alert_type",
            type="string",
            description="告警类型：" + "、".join(sorted(SYMBOL_ALERT_TYPES)),
            required=False,
            enum=sorted(SYMBOL_ALERT_TYPES),
        ),
        ToolParameter(
            name="parameters",
            type="object",
            description=_PARAMETER_DOC,
            required=False,
        ),
        ToolParameter(
            name="name",
            type="string",
            description="可选规则名称；不填则按类型自动生成",
            required=False,
        ),
        ToolParameter(
            name="severity",
            type="string",
            description="严重级别",
            required=False,
            enum=sorted(SUPPORTED_SEVERITIES),
            default="info",
        ),
        ToolParameter(
            name="rule_id",
            type="integer",
            description="update/delete 时的规则 ID",
            required=False,
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="可选提案理由，用于在卡片上向用户说明",
            required=False,
        ),
    ],
    handler=_handle_propose_alert_rule_change,
    category="action",
    policy=_ALERT_PROPOSAL_POLICY,
)

ALL_ALERT_TOOLS = [propose_alert_rule_change_tool]
