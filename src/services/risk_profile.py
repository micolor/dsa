# -*- coding: utf-8 -*-
"""User risk profile shared by the proactive brief, weekly review and chat prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


_HORIZON_LABELS = {
    "short": "短期（数天到数周）",
    "medium": "中期（数周到数月）",
    "long": "长期（半年以上）",
}


@dataclass(frozen=True)
class RiskProfile:
    horizon: str = "medium"
    max_drawdown_pct: float = 15.0
    max_position_pct: float = 35.0
    notes: str = ""

    @property
    def horizon_label(self) -> str:
        return _HORIZON_LABELS.get(self.horizon, self.horizon)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "horizon": self.horizon,
            "horizon_label": self.horizon_label,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_position_pct": self.max_position_pct,
            "notes": self.notes,
        }


def from_config(config: Any) -> RiskProfile:
    horizon = str(getattr(config, "agent_risk_profile_horizon", "medium") or "medium").strip().lower()
    if horizon not in _HORIZON_LABELS:
        horizon = "medium"
    try:
        max_drawdown = float(getattr(config, "agent_risk_profile_max_drawdown_pct", 15.0))
    except (TypeError, ValueError):
        max_drawdown = 15.0
    try:
        max_position = float(getattr(config, "agent_risk_profile_max_position_pct", 35.0))
    except (TypeError, ValueError):
        max_position = 35.0
    notes = str(getattr(config, "agent_risk_profile_notes", "") or "").strip()
    return RiskProfile(
        horizon=horizon,
        max_drawdown_pct=max(1.0, min(max_drawdown, 100.0)),
        max_position_pct=max(1.0, min(max_position, 100.0)),
        notes=notes,
    )


def _account_overrides(config: Any) -> Dict[str, Dict[str, Any]]:
    raw = str(getattr(config, "agent_risk_profile_accounts_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): value
        for key, value in parsed.items()
        if isinstance(value, dict)
    }


def for_account(config: Any, account_id: Any) -> RiskProfile:
    """Return the global profile with an optional per-account override.

    ``AGENT_RISK_PROFILE_ACCOUNTS_JSON`` is a JSON object keyed by account id,
    e.g. ``{"2": {"max_position_pct": 60, "horizon": "short"}}``. Invalid values
    fall back to the global profile.
    """
    base = from_config(config)
    override = _account_overrides(config).get(str(account_id))
    if not override:
        return base

    horizon = str(override.get("horizon") or base.horizon).strip().lower()
    if horizon not in _HORIZON_LABELS:
        horizon = base.horizon
    try:
        max_drawdown = float(override.get("max_drawdown_pct", base.max_drawdown_pct))
    except (TypeError, ValueError):
        max_drawdown = base.max_drawdown_pct
    try:
        max_position = float(override.get("max_position_pct", base.max_position_pct))
    except (TypeError, ValueError):
        max_position = base.max_position_pct
    notes = str(override.get("notes", base.notes) or "").strip()
    return RiskProfile(
        horizon=horizon,
        max_drawdown_pct=max(1.0, min(max_drawdown, 100.0)),
        max_position_pct=max(1.0, min(max_position, 100.0)),
        notes=notes,
    )


def prompt_section(profile: RiskProfile) -> str:
    lines = [
        "## 用户风险画像（建议必须遵守）",
        f"- 投资期限：{profile.horizon_label}",
        f"- 单票上限：{profile.max_position_pct:.1f}%",
        f"- 最大回撤容忍：{profile.max_drawdown_pct:.1f}%",
    ]
    if profile.notes:
        lines.append(f"- 补充说明：{profile.notes}")
    lines.append(
        "当建议与画像冲突时（例如单票超限、回撤超限），优先提示减仓/分散/降低波动，"
        "不要推荐加仓；不要假设用户能承受超过画像的风险。"
    )
    return "\n".join(lines)


def portfolio_warnings(
    profile: RiskProfile,
    snapshot: Optional[Dict[str, Any]],
    risk_report: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return profile-based warnings from a portfolio snapshot / risk report."""
    warnings: List[str] = []
    snapshot = snapshot or {}
    total_equity = _number(snapshot.get("total_equity"))
    positions: List[Dict[str, Any]] = []
    for account in snapshot.get("accounts", []) or []:
        positions.extend(account.get("positions", []) or [])

    if total_equity and total_equity > 0:
        for position in positions:
            market_value = _number(position.get("market_value_base"))
            if market_value is None:
                continue
            weight = market_value / total_equity * 100.0
            if weight > profile.max_position_pct:
                symbol = str(position.get("symbol") or "")
                warnings.append(
                    f"单票集中度：{symbol} 占 {weight:.1f}%，超过画像上限 {profile.max_position_pct:.1f}%"
                )

    if risk_report:
        drawdown = risk_report.get("drawdown") or {}
        current = _number(drawdown.get("current_drawdown_pct"))
        if current is None:
            current = _number(drawdown.get("max_drawdown_pct"))
        if current is not None and abs(current) > profile.max_drawdown_pct:
            warnings.append(
                f"组合回撤 {current:.1f}%，超过画像容忍 {profile.max_drawdown_pct:.1f}%"
            )
    return warnings


def portfolio_warnings_for_accounts(
    config: Any,
    snapshot: Optional[Dict[str, Any]],
    risk_report: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Profile warnings with per-account overrides applied to concentration."""
    snapshot = snapshot or {}
    warnings: List[str] = []
    for account in snapshot.get("accounts", []) or []:
        account_id = account.get("account_id")
        account_name = str(account.get("account_name") or account_id or "账户")
        profile = for_account(config, account_id)
        equity = _number(account.get("total_equity"))
        positions = account.get("positions", []) or []
        if equity and equity > 0:
            for position in positions:
                market_value = _number(position.get("market_value_base"))
                if market_value is None:
                    continue
                weight = market_value / equity * 100.0
                if weight > profile.max_position_pct:
                    symbol = str(position.get("symbol") or "")
                    warnings.append(
                        f"[{account_name}] 单票集中度：{symbol} 占 {weight:.1f}%，"
                        f"超过画像上限 {profile.max_position_pct:.1f}%"
                    )

    # Drawdown is portfolio-wide; use the global profile for it.
    global_profile = from_config(config)
    if risk_report:
        drawdown = risk_report.get("drawdown") or {}
        current = _number(drawdown.get("current_drawdown_pct"))
        if current is None:
            current = _number(drawdown.get("max_drawdown_pct"))
        if current is not None and abs(current) > global_profile.max_drawdown_pct:
            warnings.append(
                f"组合回撤 {current:.1f}%，超过画像容忍 {global_profile.max_drawdown_pct:.1f}%"
            )
    return warnings


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
