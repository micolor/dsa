# -*- coding: utf-8 -*-
"""Kinds that the agent may dispatch to the chat UI."""

from __future__ import annotations

ACTION_PROPOSAL_KINDS = frozenset({
    "alert_create", "alert_update", "alert_delete",
    "portfolio_trade",
    "portfolio_cash_create", "portfolio_cash_delete",
    "portfolio_corporate_create", "portfolio_corporate_delete",
    "portfolio_account_create", "portfolio_account_update", "portfolio_account_deactivate",
    "signal_create", "signal_status", "signal_feedback", "signal_reassess",
    "paper_reset",
    "history_delete_records", "history_delete_by_code",
    "intelligence_source_create", "intelligence_defaults_create",
    "config_update",
    "scheduler_run",
    "notification_test",
})

ACTION_EXECUTED_KINDS = frozenset({
    "watchlist_change",
    "alert_rule_toggle",
    "alert_rule_test",
    "fx_refresh",
    "paper_refresh",
    "intel_source_test",
    "llm_channel_test",
})

ACTION_TASK_KINDS = frozenset({
    "stock_analysis",
    "market_review",
    "backtest_run",
    "screening_run",
    "intelligence_fetch",
    "fund_nav_backfill",
    "fund_nav_run",
    "signal_outcomes_run",
    "paper_backfill",
})

# Proposal tools introduced by the site-operations feature. When
# AGENT_CHAT_SITE_OPERATIONS_ENABLED is off all of these except
# ``propose_alert_rule_change`` are unregistered so the tool surface returns to
# the pre-feature set. That one is listed because the feature renamed/expanded
# the old ``propose_alert``, but it replaces pre-existing behavior and must stay
# registered on its own.
SITE_OPERATION_PROPOSAL_TOOL_NAMES = frozenset({
    "propose_history_deletion",
    "propose_intelligence_source_change",
    "propose_config_update",
    "propose_scheduler_run",
    "propose_notification_test",
    "propose_alert_rule_change",
    "propose_signal_change",
    "propose_paper_reset",
    "propose_portfolio_ledger_entry",
    "propose_portfolio_account_change",
})
