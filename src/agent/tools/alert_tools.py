"""Agent tool for proposing alert rules for user confirmation.

The ``propose_alert`` tool is **read-only**: it validates a well-formed alert
proposal (matching the backend ``AlertRuleCreateRequest`` contract) and returns it
so the runner can surface it as an ``alert_proposal`` SSE event. The actual rule is
created only after the user confirms, via the existing ``POST /api/v1/alerts/rules``
endpoint (triggered from the web client). This keeps the "confirm before create"
semantics and reuses the existing validation, permission and notification paths.
"""

from typing import Any, Dict

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
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
    read_only=True,
    side_effects=[],
    permissions=[],
    scope_dimensions=[],
    # Not cancellation-safe for the Codex read-only surface: propose_alert is an
    # interactive confirm-flow tool for the default (LiteLLM) chat path only.
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


def _handle_propose_alert(
    target: str,
    alert_type: str,
    parameters: Dict[str, Any],
    name: str = "",
    severity: str = "info",
    reason: str = "",
) -> Dict[str, Any]:
    """Validate a single-symbol alert proposal and return it (no persistence)."""
    alert_type = str(alert_type or "").strip().lower()
    if alert_type not in SYMBOL_ALERT_TYPES:
        return {"error": f"unsupported alert_type: {alert_type}; supported: {sorted(SYMBOL_ALERT_TYPES)}"}

    severity = str(severity or "info").strip().lower()
    if severity not in SUPPORTED_SEVERITIES:
        return {"error": f"unsupported severity: {severity}; supported: {sorted(SUPPORTED_SEVERITIES)}"}

    try:
        norm_params = normalize_alert_parameters(alert_type, parameters or {})
    except AlertServiceError as exc:
        return {"error": str(exc)}

    norm_target = str(target or "").strip()
    if not norm_target:
        return {"error": "target must not be empty"}

    rule_name = str(name or "").strip() or AlertService._default_rule_name(
        target=norm_target,
        alert_type=alert_type,
        parameters=norm_params,
    )

    proposal = {
        "name": rule_name[:64],
        "target_scope": "single_symbol",
        "target": norm_target,
        "alert_type": alert_type,
        "parameters": norm_params,
        "severity": severity,
    }

    summary = _build_summary(norm_target, alert_type, norm_params)
    if str(reason or "").strip():
        summary = f"{summary}（{reason}）"

    return {"proposal": proposal, "summary": summary}


propose_alert_tool = ToolDefinition(
    name="propose_alert",
    description=(
        "当识别到某个股票值得监控的价格上穿/下穿、涨跌幅、成交量异动或技术指标（均线/RSI/MACD/KDJ/CCI）"
        "信号时，用本工具生成一个告警提案交给用户确认。只生成提案，不创建任何规则。\n"
        f"{_PARAMETER_DOC}"
    ),
    parameters=[
        ToolParameter(
            name="target",
            type="string",
            description="股票代码，支持裸码或带前缀（如 600519 / HK00700 / AAPL）",
            required=True,
        ),
        ToolParameter(
            name="alert_type",
            type="string",
            description="告警类型：" + "、".join(sorted(SYMBOL_ALERT_TYPES)),
            required=True,
            enum=sorted(SYMBOL_ALERT_TYPES),
        ),
        ToolParameter(
            name="parameters",
            type="object",
            description=_PARAMETER_DOC,
            required=True,
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
            name="reason",
            type="string",
            description="可选提案理由，用于在卡片上向用户说明",
            required=False,
        ),
    ],
    handler=_handle_propose_alert,
    category="action",
    policy=_ALERT_PROPOSAL_POLICY,
)

ALL_ALERT_TOOLS = [propose_alert_tool]
