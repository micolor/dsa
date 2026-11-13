# -*- coding: utf-8 -*-
"""Risk-profile parsing, prompt injection and portfolio checks."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.risk_profile import (
    for_account,
    from_config,
    portfolio_warnings,
    portfolio_warnings_for_accounts,
    prompt_section,
)


def _config(**overrides):
    values = {
        "agent_risk_profile_horizon": "long",
        "agent_risk_profile_max_drawdown_pct": 12.0,
        "agent_risk_profile_max_position_pct": 30.0,
        "agent_risk_profile_notes": "不碰高波动题材",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_from_config_normalizes_values():
    profile = from_config(_config(agent_risk_profile_horizon="unknown"))

    assert profile.horizon == "medium"
    assert profile.max_drawdown_pct == 12.0
    assert profile.max_position_pct == 30.0
    assert "不碰高波动题材" in prompt_section(profile)


def test_portfolio_warnings_flag_concentration_and_drawdown():
    profile = from_config(_config())
    snapshot = {
        "total_equity": 100000.0,
        "accounts": [{"positions": [{"symbol": "600519", "market_value_base": 50000.0}]}],
    }
    risk = {"drawdown": {"current_drawdown_pct": -20.0}}

    warnings = portfolio_warnings(profile, snapshot, risk)

    assert any("单票集中度" in warning for warning in warnings)
    assert any("组合回撤" in warning for warning in warnings)


def test_no_warnings_when_within_profile():
    profile = from_config(_config())
    snapshot = {
        "total_equity": 100000.0,
        "accounts": [{"positions": [{"symbol": "600519", "market_value_base": 10000.0}]}],
    }
    risk = {"drawdown": {"current_drawdown_pct": -3.0}}

    assert portfolio_warnings(profile, snapshot, risk) == []


def test_per_account_override_is_applied():
    config = _config(
        agent_risk_profile_accounts_json='{"2": {"max_position_pct": 60, "horizon": "short"}}',
    )
    assert for_account(config, 2).max_position_pct == 60.0
    assert for_account(config, 2).horizon == "short"
    assert for_account(config, 3).max_position_pct == 30.0

    snapshot = {
        "accounts": [
            {"account_id": 2, "account_name": "A", "total_equity": 100.0,
             "positions": [{"symbol": "600519", "market_value_base": 50.0}]},
            {"account_id": 3, "account_name": "B", "total_equity": 100.0,
             "positions": [{"symbol": "000001", "market_value_base": 50.0}]},
        ],
    }
    warnings = portfolio_warnings_for_accounts(config, snapshot, None)

    assert any("[A]" in warning for warning in warnings) is False
    assert any("[B]" in warning and "000001" in warning for warning in warnings) is True
