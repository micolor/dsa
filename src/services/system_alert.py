# -*- coding: utf-8 -*-
"""系统级告警发送：用于分析失败、数据源熔断等运维事件。

复用 NotificationService 的 system_error 路由（未配置时自动回退报告/主渠道）。
发送失败只记日志，绝不触发它自己的告警，避免告警环路。
"""
import logging
from typing import Optional

from src.config import get_config
from src.notification import NotificationService

logger = logging.getLogger(__name__)

_msg_dedup_keys = set()  # 进程内当日去重（增强：避免同场景刷屏）


def send_system_alert(content: str, *, dedup_key: str, enabled: Optional[bool] = None) -> bool:
    """Send a one-shot system alert. Returns True on successful dispatch.

    Falls back to the report/primary channel when system_error channels are
    unconfigured (handled inside NotificationService routing). Never raises.
    """
    if enabled is None:
        try:
            enabled = bool(getattr(get_config(), "runtime_analysis_failure_alert_enabled", True))
        except Exception:  # pragma: no cover - defensive
            enabled = True
    if not enabled:
        logger.info("系统告警已关闭，跳过: %s", dedup_key)
        return False
    if dedup_key in _msg_dedup_keys:
        logger.info("系统告警已在本进程去重，跳过: %s", dedup_key)
        return False
    try:
        result = NotificationService().send_with_results(content, route_type="system_error", dedup_key=dedup_key)
        if bool(result.success):
            # Only mark as sent AFTER a successful send, so a transient
            # failure leaves the key unlocked and retryable.
            _msg_dedup_keys.add(dedup_key)
            return True
        logger.warning("系统告警发送未成功: %s result=%s", dedup_key, getattr(result, "status", None))
        return False
    except Exception as exc:  # noqa: BLE001 - must never break the caller
        logger.warning("系统告警发送失败: %s err=%s", dedup_key, exc)
        return False


def clear_system_alert_dedup(key: Optional[str] = None) -> None:
    """For tests: clear the in-process dedup set (or a single key)."""
    if key is None:
        _msg_dedup_keys.clear()
    else:
        _msg_dedup_keys.discard(key)
