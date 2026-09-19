# -*- coding: utf-8 -*-
"""Focused tests for internal risk override application facts."""

import pytest

from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.risk_override import (
    DashboardDecisionSignal,
    RiskApplicationReason,
    RiskOverrideApplication,
    build_risk_override_application,
    build_risk_override_plan,
)


def _application(*, current_signal="buy", override_enabled=True, risk_raw=None):
    ctx = AgentContext()
    if risk_raw is not None:
        ctx.add_opinion(AgentOpinion(agent_name="risk", signal="hold", raw_data=risk_raw))
    return build_risk_override_application(build_risk_override_plan(
        ctx,
        current_signal=current_signal,
        override_enabled=override_enabled,
    ))


@pytest.mark.parametrize(
    ("application", "reason"),
    [
        (_application(), RiskApplicationReason.NO_RISK_EVIDENCE),
        (_application(risk_raw={"risk_level": "high"}), RiskApplicationReason.NO_OVERRIDE_TRIGGER),
        (
            _application(risk_raw={"veto_buy": True}, override_enabled=False),
            RiskApplicationReason.OVERRIDE_DISABLED,
        ),
        (
            _application(current_signal="hold", risk_raw={"veto_buy": True}),
            RiskApplicationReason.POST_RISK_SIGNAL_ALREADY_WITHIN_RISK_LIMIT,
        ),
    ],
)
def test_non_applied_reasons_remain_distinct(application, reason):
    assert application.applied is False
    assert application.reason == reason
    assert application.from_signal is None
    assert application.to_signal is None


def test_veto_application_records_actual_transition():
    application = _application(current_signal="buy", risk_raw={"veto_buy": True})

    assert application == RiskOverrideApplication(
        evidence_present=True,
        override_enabled=True,
        trigger="risk_veto",
        applied=True,
        reason="risk_veto_applied",
        post_risk_signal="hold",
        from_signal="buy",
        to_signal="hold",
    )


def test_downgrade_application_records_actual_transition():
    application = _application(
        current_signal="hold",
        risk_raw={"signal_adjustment": "downgrade_one"},
    )

    assert application.reason == RiskApplicationReason.RISK_DOWNGRADE_APPLIED
    assert application.post_risk_signal == DashboardDecisionSignal.SELL
    assert application.from_signal == DashboardDecisionSignal.HOLD
    assert application.to_signal == DashboardDecisionSignal.SELL


@pytest.mark.parametrize("raw_value", ["false", "False", "no", "off", "0"])
def test_string_false_veto_does_not_apply_the_veto(raw_value):
    """模型按 prompt 的 `true|false` 槽位输出字符串 "false" 很常见。

    原实现用 `bool(risk_raw.get("veto_buy"))` 取真值，非空字符串恒为真，
    于是一份「低风险、显式不 veto」的报告会把 buy 降级成 hold，并向用户
    展示「风险否决」理由。
    """
    application = _application(current_signal="buy", risk_raw={
        "risk_level": "low",
        "risk_score": 20,
        "flags": [],
        "signal_adjustment": "none",
        "veto_buy": raw_value,
    })

    assert application.applied is False
    assert application.trigger == "none"
    assert application.post_risk_signal == "buy"


@pytest.mark.parametrize("raw_value", ["true", "True", "yes", "on", "1", True])
def test_truthy_veto_still_applies(raw_value):
    application = _application(current_signal="buy", risk_raw={"veto_buy": raw_value})

    assert application.applied is True
    assert application.trigger == "risk_veto"
    assert application.post_risk_signal == "hold"


@pytest.mark.parametrize("raw_value", ["unsure", "maybe"])
def test_unrecognized_veto_value_keeps_the_conservative_veto(raw_value):
    """无法识别的取值按「有否决」处理：风险控制宁可少买，不可漏判。

    只把明确表达「不否决」的写法（"false"/"no"/"off"/"0"）判为不否决。
    """
    application = _application(current_signal="buy", risk_raw={"veto_buy": raw_value})

    assert application.applied is True
    assert application.trigger == "risk_veto"
