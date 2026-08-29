# -*- coding: utf-8 -*-
"""Regression tests for backtest agent tool eval-window default resolution."""

from unittest.mock import patch

from src.agent.tools.backtest_tools import _resolve_default_eval_window


def test_resolve_default_eval_window_uses_configured_window_when_none():
    """A missing/omitted window must resolve to the configured backtest window.

    Summaries are only built for the window a backtest ran with, so a hardcoded
    tool default that diverges from ``backtest_eval_window_days`` would query a
    window that was never computed and return "no summary".
    """
    cfg = type("Cfg", (), {"backtest_eval_window_days": 25})()
    with patch("src.config.get_config", return_value=cfg):
        assert _resolve_default_eval_window(None) == 25


def test_resolve_default_eval_window_falls_back_to_10_when_unset():
    """If the config attribute is absent, fall back to the historical default."""
    cfg = type("Cfg", (), {})()
    with patch("src.config.get_config", return_value=cfg):
        assert _resolve_default_eval_window(None) == 10


def test_resolve_default_eval_window_preserves_explicit_value():
    """An explicitly provided window must never be overridden."""
    assert _resolve_default_eval_window(30) == 30
    assert _resolve_default_eval_window(7) == 7
