import pytest

from src.agent.tools.registry import (
    EXECUTION_MODES,
    RISK_LEVELS,
    ToolDefinition,
    ToolPolicy,
)


def _tool(**policy_kwargs):
    return ToolDefinition(
        name="demo",
        description="demo",
        parameters=[],
        handler=lambda: None,
        policy=ToolPolicy.declared(**policy_kwargs),
    )


def test_execution_enums_are_exact():
    assert EXECUTION_MODES == {"none", "direct", "proposal", "task"}
    assert RISK_LEVELS == {"read", "low", "high"}


def test_direct_requires_undo_hint():
    tool = _tool(read_only=False, execution_mode="direct", risk_level="low")
    with pytest.raises(ValueError, match="undo_hint"):
        tool.validate_action_policy()


def test_direct_with_undo_hint_passes():
    tool = _tool(
        read_only=False,
        execution_mode="direct",
        risk_level="low",
        undo_hint="再次调用并反向操作即可撤销",
    )
    tool.validate_action_policy()


def test_write_mode_cannot_be_read_only_or_cancellation_safe():
    for mode in ("direct", "proposal", "task"):
        tool = _tool(
            read_only=True,
            execution_mode=mode,
            risk_level="high",
            cancellation_safe=True,
            undo_hint="x",
        )
        with pytest.raises(ValueError):
            tool.validate_action_policy()


def test_none_mode_must_be_read_risk():
    tool = _tool(read_only=True, execution_mode="none", risk_level="high")
    with pytest.raises(ValueError):
        tool.validate_action_policy()


def test_unknown_mode_rejected():
    tool = _tool(read_only=True, execution_mode="write", risk_level="read")
    with pytest.raises(ValueError, match="execution_mode"):
        tool.validate_action_policy()


def test_default_registry_passes_strict_validation():
    from src.agent.factory import get_tool_registry

    issues = get_tool_registry().validate_tool_policies(strict=True)
    assert issues == []


def test_site_tools_absent_when_kill_switch_off(monkeypatch):
    import src.agent.factory as factory
    from src.agent.action_contract import SITE_OPERATION_PROPOSAL_TOOL_NAMES
    from src.agent.tools.site_action_tools import ALL_SITE_ACTION_TOOLS
    from src.agent.tools.site_read_tools import ALL_SITE_READ_TOOLS
    from src.agent.tools.site_task_tools import ALL_SITE_TASK_TOOLS

    read_names = {tool.name for tool in ALL_SITE_READ_TOOLS}
    direct_names = {tool.name for tool in ALL_SITE_ACTION_TOOLS}
    task_names = {tool.name for tool in ALL_SITE_TASK_TOOLS}
    assert len(read_names) == 12
    assert len(direct_names) == 7
    assert len(task_names) == 9

    # propose_alert_rule_change 是 pre-feature propose_alert 的改名替代，
    # 不在「功能新增、需要随开关移除」的集合里。
    new_proposal_names = SITE_OPERATION_PROPOSAL_TOOL_NAMES - {"propose_alert_rule_change"}
    assert len(new_proposal_names) == 9

    monkeypatch.setattr(
        factory, "_site_operations_enabled", lambda: False, raising=False
    )
    factory._TOOL_REGISTRY = None
    try:
        registry = factory.get_tool_registry()
        for name in sorted(read_names | direct_names | task_names | new_proposal_names):
            assert name not in registry, f"kill switch off 时仍注册了 {name}"
        # 功能上线前的提案工具必须保留，关闭开关不等于没收已有确认卡片。
        assert "propose_portfolio_trade" in registry
        assert "propose_alert_rule_change" in registry
        assert registry.validate_tool_policies(strict=True) == []
    finally:
        factory._TOOL_REGISTRY = None


def test_site_tools_present_by_default():
    from src.agent.factory import get_tool_registry

    registry = get_tool_registry()
    assert "get_history_view" in registry
    assert "get_config_view" in registry
    assert registry.get("get_history_view").policy.execution_mode == "none"
