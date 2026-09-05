# 管线自愈 + 失败告警 实现计划（A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每日股票分析真正无人值守——分析失败时主动告警到用户、运行健康状态可在 Web 透视、数据源熔断可配置并提醒、跨发行日可自动补跑。

**Architecture:** 全部改动挂在现有编排之上，不新造子系统。失败/健康/补跑利用 `RuntimeSchedulerService`（已有跨进程锁、失败记录、有界重试）；通知复用 `NotificationService.send_with_results(route_type="system_error")`；数据源隔离复用已存在的 `CircuitBreaker`，仅提升其可配性与可见性。告警发送是薄薄一层可复用的 `send_system_alert`，失败只记日志、绝不触发它自己的告警（防环路）。

**Tech Stack:** Python 3.11+ / FastAPI / uvicorn / pytest；前端 Vue/React（apps/dsa-web，Vite build）。

**Spec:** `docs/superpowers/specs/2026-09-05-pipeline-self-healing-failure-alerting-design.md`

## Global Constraints

- 最小改动、不新增平行实现；所有新配置默认「不配置可跑，配置后增强」。
- 新配置项必须同步 `src/config.py`（Config 字段 + env 解析）+ `.env.example`。
- 用户可见变更必须同步 `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式 `- [类型] 描述`）。
- 告警发送失败只记日志，不中断主流程、不触发它自己的告警（防环路）。
- 隔离/熔断只影响数据源**偏好**，绝不 fail-fast 阻断管线。
- 默认行为保持：`CircuitBreaker` 默认 `failure_threshold=3`、`cooldown_seconds=300.0`（与现状一致）。
- **提交须知（AGENTS.md）：commit 前须经用户确认**；本计划各 Task 的 commit 步骤在执行时先征询确认再运行。commit message 使用英文，不带 `Co-Authored-By`。
- 测试遵循 `tests/`（pytest）；联调网络用例标记 `-m network`，本地跑 `python -m pytest -m "not network"`。

---

### Task 1: 新增配置项 + 熔断器阈值可配置化（A5 + A3a）

**Files:**
- Modify: `src/config.py`（Config 类 ~1103 区域 新增字段；~2051 区域 新增 env 解析；~1203 附近已有 `trading_day_check_enabled` 参考）
- Modify: `data_provider/base.py:632`（`_daily_source_health` 改懒加载 + 从 config 读阈值/冷却）及 811-842 相应访问器
- Test: `tests/test_data_source_quarantine_config.py`

**Interfaces:**
- Consumes: `src.config.get_config()`（`Config` 实例）；`data_provider.realtime_types.CircuitBreaker`
- Produces: Config 新字段 `runtime_analysis_failure_alert_enabled`、`data_source_quarantine_threshold`、`data_source_quarantine_recovery_seconds`、`runtime_backfill_enabled`、`runtime_backfill_max_days`；`DataFetcherManager._get_daily_source_health() -> CircuitBreaker`（懒加载、按 config 构建）

- [ ] **Step 1: 在 `src/config.py` Config 类加字段**（在 `trading_day_check_enabled` 附近，约 line 1203）

```python
    runtime_analysis_failure_alert_enabled: bool = True
    data_source_quarantine_threshold: int = 3
    data_source_quarantine_recovery_seconds: float = 300.0
    runtime_backfill_enabled: bool = True
    runtime_backfill_max_days: int = 1
```

- [ ] **Step 2: 在 `src/config.py` env 解析处加对应取值**（`get_config` 内，约 line 2174 后；参考 `trading_day_check_enabled` 的 `os.getenv(...).lower() != 'false'` 写法）

```python
            runtime_analysis_failure_alert_enabled=(
                os.getenv('RUNTIME_ANALYSIS_FAILURE_ALERT_ENABLED', 'true').lower() != 'false'
            ),
            data_source_quarantine_threshold=int(os.getenv('DATA_SOURCE_QUARANTINE_THRESHOLD', '3')),
            data_source_quarantine_recovery_seconds=float(
                os.getenv('DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS', '300')
            ),
            runtime_backfill_enabled=os.getenv('RUNTIME_BACKFILL_ENABLED', 'true').lower() != 'false',
            runtime_backfill_max_days=int(os.getenv('RUNTIME_BACKFILL_MAX_DAYS', '1')),
```

- [ ] **Step 3: 改 `data_provider/base.py` 让熔断器阈值可配置**（替换 line 632 与 811-842 的访问器）

替换 line 632：

```python
    _daily_source_health: Optional["CircuitBreaker"] = None
    _daily_source_health_config_sig: Optional[tuple] = None

    @classmethod
    def _get_daily_source_health(cls) -> "CircuitBreaker":
        cfg = None
        try:
            from src.config import get_config
            cfg = get_config()
        except Exception:  # pragma: no cover - config unavailable fallback
            cfg = None
        threshold = getattr(cfg, "data_source_quarantine_threshold", 3)
        cooldown = getattr(cfg, "data_source_quarantine_recovery_seconds", 300.0)
        sig = (threshold, cooldown)
        if (
            cls._daily_source_health is None
            or cls._daily_source_health_config_sig != sig
        ):
            from .realtime_types import CircuitBreaker
            cls._daily_source_health = CircuitBreaker(
                failure_threshold=threshold, cooldown_seconds=cooldown
            )
            cls._daily_source_health_config_sig = sig
        return cls._daily_source_health
```

把 811-842 里对 `cls._daily_source_health` 的直用改为 `cls._get_daily_source_health()`（`_is_daily_source_available` 的 `is_available`、`_record_daily_source_success`/`_record_daily_source_failure` 的 `record_success`/`record_failure`、`reset_daily_source_health` 的 `reset`）。

- [ ] **Step 4: 写失败测试** `tests/test_data_source_quarantine_config.py`

```python
import os
from unittest import mock

from data_provider.base import DataFetcherManager


def test_daily_source_health_respects_config_threshold_cooldown():
    with mock.patch.dict(
        os.environ,
        {
            "DATA_SOURCE_QUARANTINE_THRESHOLD": "5",
            "DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS": "42",
        },
        clear=False,
    ):
        from src.config import get_config
        DataFetcherManager.reset_daily_source_health()
        breaker = DataFetcherManager._get_daily_source_health()
        assert breaker.failure_threshold == 5
        assert breaker.cooldown_seconds == 42
        # 取消 reset 后的缓存，恢复默认
        DataFetcherManager.reset_daily_source_health()
```

- [ ] **Step 5: 运行验证失败＋通过**

Run: `python -m pytest tests/test_data_source_quarantine_config.py -v`
Expected: FAIL（`_get_daily_source_health` / 字段未定义）→ Step3 实现后 PASS。

- [ ] **Step 6: 提交**

```bash
git add src/config.py data_provider/base.py tests/test_data_source_quarantine_config.py
git commit -m "feat: expose data-source quarantine threshold and cooldown as config"
```

---

### Task 2: 通用系统告警发送器（A1 底座，供 A1 与 A3b 复用）

**Files:**
- Create: `src/services/system_alert.py`
- Test: `tests/test_system_alert.py`

**Interfaces:**
- Consumes: `src.notification.NotificationService.send_with_results(content, route_type="system_error", dedup_key=...)`
- Produces: `send_system_alert(content: str, *, dedup_key: str) -> bool`（复用上述路由；失败只记日志返回 False；受 `runtime_analysis_failure_alert_enabled` 门控）

- [ ] **Step 1: 写失败测试** `tests/test_system_alert.py`

```python
import logging
from unittest import mock

import src.services.system_alert as sa


def test_send_system_alert_dispatches_with_system_error_route():
    sent = {}

    class _Svc:
        def send_with_results(self, content, **kw):
            sent["content"] = content
            sent["kw"] = kw
            return mock.Mock(success=True)

    with mock.patch.object(sa, "get_config", return_value=mock.Mock(
            runtime_analysis_failure_alert_enabled=True)):
        with mock.patch.object(sa, "NotificationService", return_value=_Svc()):
            ok = sa.send_system_alert("失败：LLM 超时", dedup_key="analysis-failure:2026-09-05")
    assert ok is True
    assert sent["kw"]["route_type"] == "system_error"
    assert sent["kw"]["dedup_key"] == "analysis-failure:2026-09-05"


def test_send_system_alert_returns_false_on_send_failure_no_loop():
    with mock.patch.object(sa, "get_config", return_value=mock.Mock(
            runtime_analysis_failure_alert_enabled=True)):
        with mock.patch.object(sa, "NotificationService", side_effect=RuntimeError("boom")):
            ok = sa.send_system_alert("x", dedup_key="k")
    assert ok is False  # 失败只返回 False，不抛、不再次触发告警
```

- [ ] **Step 2: 运行确认 FAIL**

Run: `python -m pytest tests/test_system_alert.py -v` → FAIL（`sa.send_system_alert` 不存在）

- [ ] **Step 3: 实现** `src/services/system_alert.py`

```python
# -*- coding: utf-8 -*-
"""系统级告警发送：用于分析失败、数据源熔断等运维事件。

复用 NotificationService 的 system_error 路由（未配置时自动回退报告/主渠道）。
发送失败只记日志，绝不触发它自己的告警，避免告警环路。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_msg_dedup_keys = set()  # 进程内当日去重（增强：避免同场景刷屏）


def send_system_alert(content: str, *, dedup_key: str, enabled: Optional[bool] = None) -> bool:
    """Send a one-shot system alert. Returns True on successful dispatch.

    Falls back to the report/primary channel when system_error channels are
    unconfigured (handled inside NotificationService routing). Never raises.
    """
    if enabled is None:
        try:
            from src.config import get_config

            enabled = bool(getattr(get_config(), "runtime_analysis_failure_alert_enabled", True))
        except Exception:  # pragma: no cover - defensive
            enabled = True
    if not enabled:
        logger.info("系统告警已关闭，跳过: %s", dedup_key)
        return False
    if dedup_key in _msg_dedup_keys:
        logger.info("系统告警已在本进程去重，跳过: %s", dedup_key)
        return False
    _msg_dedup_keys.add(dedup_key)
    try:
        from src.notification import NotificationService

        result = NotificationService().send_with_results(content, route_type="system_error", dedup_key=dedup_key)
        if bool(result.success):
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
```

- [ ] **Step 4: 运行确认 PASS**

Run: `python -m pytest tests/test_system_alert.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/system_alert.py tests/test_system_alert.py
git commit -m "feat: add reusable system alert sender with dedup"
```

---

### Task 3: 调度失败告警钩子（A1 主体）

**Files:**
- Modify: `src/services/runtime_scheduler.py`（`__init__` 加 `alert_sender` 注入；`_run_analysis_locked` except 287-299 与 `_schedule_retry_if_needed` 208-220 处加钩子；`_last_success_at` 置空处 + 302-319 重试耗尽处）
- Test: `tests/test_runtime_scheduler_failure_alert.py`

**Interfaces:**
- Consumes: Task2 `send_system_alert`；本类现有 `_last_error/_consecutive_failures/_last_failed_at/_last_success_at`
- Produces: `RuntimeSchedulerService(..., alert_sender: Optional[Callable[[str], bool]] = None)`；私有 `_send_failure_alert(reason: str)`；字段 `_last_failure_alert_date: Optional[str]`

- [ ] **Step 1: 构造注入点**：`runtime_scheduler.py` `__init__` 加参数与字段

在 `__init__` 签名（约 line 189-199）加：

```python
        alert_sender: Optional[Callable[[str], bool]] = None,
```

在 `_consecutive_failures = 0`（line 229）附近加：

```python
        self._last_failure_alert_date: Optional[str] = None
        if alert_sender is None:
            def _default_alert(content: str) -> bool:
                from src.services.system_alert import send_system_alert
                return send_system_alert(content, dedup_key=f"analysis-failure:{date.today().isoformat()}")
            alert_sender = _default_alert
        self._alert_sender = alert_sender
```

（确保 `from datetime import date` 已导入；`Callable` 已导入。）

- [ ] **Step 2: 在 except 分支加告警 + 在成功分支清日期门控**

`_run_analysis_locked` 的成功分支（line 290-293）改为：

```python
            self._last_success_at = datetime.now().isoformat()
            self._last_error = None
            self._last_failed_at = None
            self._consecutive_failures = 0
            self._last_failure_alert_date = None
```

`except` 分支（line 294-299）改为：

```python
        except Exception as exc:  # noqa: BLE001 - scheduled runs must not kill API process.
            self._last_error = str(exc)
            self._last_failed_at = datetime.now().isoformat()
            self._consecutive_failures += 1
            logger.exception("Runtime scheduled analysis failed: %s", exc)
            self._send_failure_alert("analyze")
            self._schedule_retry_if_needed()
```

- [ ] **Step 3: 新增 `_send_failure_alert`**（同日只发一次，成功后清零在 Step2）

```python
    def _send_failure_alert(self, source: str) -> None:
        today = datetime.now().date().isoformat()
        if self._last_failure_alert_date == today:
            return  # 当日已告警，抑制重复
        self._last_failure_alert_date = today
        message = (
            f"⚠️ 每日股票分析失败（连续 {self._consecutive_failures} 次）\n"
            f"最近一次失败时间：{self._last_failed_at}\n"
            f"最近一次成功时间：{self._last_success_at or '无'}\n"
            f"失败原因：{self._last_error or '未知'}"
        )
        try:
            self._alert_sender(message)
        except Exception:  # noqa: BLE001 - alert must never break the scheduler
            logger.warning("分析失败告警发送异常（已忽略）", exc_info=True)
```

- [ ] **Step 4: 在重试耗尽分支加最终告警**（`_schedule_retry_if_needed`，line 302-307）

```python
        if self._consecutive_failures >= MAX_SCHEDULED_RETRIES:
            logger.warning(
                "Runtime scheduled analysis failed %d consecutive times; no more retries",
                self._consecutive_failures,
            )
            self._send_failure_alert("exhausted")
            return
```

- [ ] **Step 5: 写测试** `tests/test_runtime_scheduler_failure_alert.py`

```python
from unittest import mock

from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc(alert_calls, runner):
    def alert(content):
        alert_calls.append(content)
        return True
    return RuntimeSchedulerService(
        config_provider=mock.Mock(),
        task_runner=runner,
        alert_sender=alert,
        owns_schedule=False,
    )


def test_failure_triggers_alert_once_when_runner_returns_false():
    calls = []
    runner = mock.Mock(return_value=False)  # runner returns False => raise
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)
    assert len(calls) == 1
    assert "每日股票分析失败" in calls[0]


def test_failure_alert_suppressed_same_day():
    calls = []
    runner = mock.Mock(return_value=False)
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)
    svc._run_analysis_locked(None)  # 第二次同日 -> 不重复
    assert len(calls) == 1


def test_success_resets_failure_alert_gate():
    calls = []
    runner = mock.Mock(side_effect=[False, True])  # 先失败后成功
    svc = _svc(calls, runner)
    svc._run_analysis_locked(None)       # 失败 -> alert (date=day)
    svc._run_analysis_locked(None)       # 成功 -> reset gate
    svc._run_analysis_locked(None)       # 再失败 -> 允许再 alert
    assert len(calls) == 2
```

- [ ] **Step 6: 运行验证**

Run: `python -m pytest tests/test_runtime_scheduler_failure_alert.py -v`
Expected: FAIL（日志告警未接入）→ 实现后 PASS。注意 `_send_failure_alert` 的日期门控依赖真实 `datetime.now()`，跨天边界由模拟 `_last_failure_alert_date` 避免。

- [ ] **Step 7: 提交**

```bash
git add src/services/runtime_scheduler.py tests/test_runtime_scheduler_failure_alert.py
git commit -m "feat: alert on scheduled analysis failure with same-day dedup"
```

---

### Task 4: 数据源熔断打开时通知（A3b）

**Files:**
- Modify: `data_provider/base.py`（`_record_daily_source_failure`，line 836-837；新增 `_source_quarantine_alert_fired` 集合）
- Test: `tests/test_source_quarantine_alert.py`

**Interfaces:**
- Consumes: Task2 `send_system_alert`；`_daily_health_key`、`_get_daily_source_health()`
- Produces: 熔断从可用→熔断的**跃迁处**发一次告警，dedup_key=`source-quarantine:{market}:{fetcher}`

- [ ] **Step 1: 实现跃迁检测与通知**（替换 836-837）

```python
    _source_quarantine_alert_fired: set = set()

    @classmethod
    def _record_daily_source_failure(cls, fetcher: BaseFetcher, market: str, error: str) -> None:
        key = cls._daily_health_key(fetcher, market)
        breaker = cls._get_daily_source_health()
        was_available = breaker.is_available(key)
        breaker.record_failure(key, error=error)
        if not breaker.is_available(key) and was_available:
            # 刚从可用转为熔断（隔离）：只通知一次
            if key not in cls._source_quarantine_alert_fired:
                cls._source_quarantine_alert_fired.add(key)
                try:
                    from src.services.system_alert import send_system_alert
                    send_system_alert(
                        f"⚠️ 数据源 {fetcher.name}（{market}）连续失败，已进入短期熔断隔离。"
                        f"已自动切换/降级到其他数据源，短时间后将自动尝试恢复。错误：{error}",
                        dedup_key=f"source-quarantine:{market}:{fetcher.name}",
                    )
                except Exception:  # noqa: BLE001 - never break fetch path
                    logger.warning("数据源熔断告警发送失败（已忽略）", exc_info=True)
```

在 `reset_daily_source_health`（line 840-842）里追加 `cls._source_quarantine_alert_fired.clear()`。

- [ ] **Step 2: 写测试** `tests/test_source_quarantine_alert.py`

```python
from unittest import mock

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import CircuitBreaker


def _mk_fetcher(name="akshare"):
    f = mock.Mock(name=name)
    f.name = name
    f.priority = 1
    return f


def test_quarantine_open_fires_alert_once(monkeypatch):
    calls = []
    monkeypatch.setattr(DataFetcherManager, "_get_daily_source_health",
                        classmethod(lambda cls: CircuitBreaker(failure_threshold=1, cooldown_seconds=300.0)))
    monkeypatch.setattr(
        "data_provider.base.send_system_alert",
        lambda content, dedup_key: calls.append((content, dedup_key)) or True,
    )
    DataFetcherManager.reset_daily_source_health()
    f = _mk_fetcher()
    DataFetcherManager._record_daily_source_failure(f, "cn", "timeout")
    assert len(calls) == 1
    assert calls[0][1].startswith("source-quarantine:cn:akshare")
    # 再次失败（已熔断）不再重复
    DataFetcherManager._record_daily_source_failure(f, "cn", "timeout")
    assert len(calls) == 1
```

（若 `send_system_alert` 在 base.py 内以 `from src.services.system_alert import send_system_alert` 形式被引用，用 `monkeypatch.setattr("src.services.system_alert.send_system_alert", ...)` 而不是 `data_provider.base.send_system_alert`；按实际导入方式择一。）

- [ ] **Step 3: 运行验证**

Run: `python -m pytest tests/test_source_quarantine_alert.py -v`
Expected: FAIL → PASS。同时确认 `test_data_source_quarantine_config.py` 不回归。

- [ ] **Step 4: 提交**

```bash
git add data_provider/base.py tests/test_source_quarantine_alert.py
git commit -m "feat: alert once when a data source circuit opens"
```

---

### Task 5: 调度健康快照方法（A2 后端）

**Files:**
- Modify: `src/services/runtime_scheduler.py`（`RuntimeSchedulerService` 加 `health_snapshot()`）
- Test: `tests/test_runtime_scheduler_health_snapshot.py`

**Interfaces:**
- Consumes: 本类字段 `_last_run_at/_last_success_at/_last_error/_last_failed_at/_consecutive_failures/_last_skipped_at/_last_skip_reason`
- Produces: `RuntimeSchedulerService.health_snapshot() -> dict`（键：`last_run_at/last_success_at/last_failed_at/last_error/consecutive_failures/last_skipped_at/last_skip_reason`）

- [ ] **Step 1: 写失败测试** `tests/test_runtime_scheduler_health_snapshot.py`

```python
from unittest import mock
from src.services.runtime_scheduler import RuntimeSchedulerService


def test_health_snapshot_returns_expected_keys():
    svc = RuntimeSchedulerService(
        config_provider=mock.Mock(), task_runner=mock.Mock(return_value=True), owns_schedule=False)
    snap = svc.health_snapshot()
    assert set(snap.keys()) == {
        "last_run_at", "last_success_at", "last_failed_at",
        "last_error", "consecutive_failures",
        "last_skipped_at", "last_skip_reason",
    }
    assert snap["consecutive_failures"] == 0
    assert svc.health_snapshot()["last_error"] is None
```

- [ ] **Step 2: 运行确认 FAIL**（`health_snapshot` 不存在）

- [ ] **Step 3: 实现**

```python
    def health_snapshot(self) -> dict:
        return {
            "last_run_at": self._last_run_at,
            "last_success_at": self._last_success_at,
            "last_failed_at": self._last_failed_at,
            "last_error": self._last_error,
            "consecutive_failures": self._consecutive_failures,
            "last_skipped_at": self._last_skipped_at,
            "last_skip_reason": self._last_skip_reason,
        }
```

- [ ] **Step 4: 运行验证** → PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/runtime_scheduler.py tests/test_runtime_scheduler_health_snapshot.py
git commit -m "feat: expose runtime scheduler health snapshot"
```

---

### Task 6: 调度健康 endpoint（A2 后端暴露）

**Files:**
- Modify: `api/v1/endpoints/health.py`（加 `GET /scheduler-health`）
- Test: 复用 health endpoint 测试模式（若存在 `tests/` 中 health 相关测试则追加，否则人工联调验证）

**Interfaces:**
- Consumes: Task5 `RuntimeSchedulerService.health_snapshot()`
- Produces: `GET /api/v1/health/scheduler-health` 返回 health_snapshot dict

- [ ] **Step 1: 实现**（在 `health.py` 的 `router` 下加路由）

```python
from fastapi import APIRouter
from src.services.runtime_scheduler import RuntimeSchedulerService

router = APIRouter()


@router.get("/scheduler-health")
async def scheduler_health() -> dict:
    """运行调度器健康状态（最近成功/失败、连续失败次数、最近原因）。"""
    from src.config import get_config
    svc = RuntimeSchedulerService(config_provider=get_config, owns_schedule=False)
    return svc.health_snapshot()
```

注意：应复用应用启动时已构造的 `RuntimeSchedulerService` 单例（而非每次新建）。若 `api/app.py` 已把该实例挂到 app.state，则改为读取它；否则保持轻量构造。执行时按 `api/app.py` 实际注入方式对齐，避免重复实例导致状态分叉。

- [ ] **Step 2: 验证**

Run: `uv run python -c "from api.v1.endpoints.health import router; print('ok', [r.path for r in router.routes])"`
Expected: 包含 `/scheduler-health`。联调 `curl http://127.0.0.1:8000/api/v1/health/scheduler-health` 返回 JSON（需 server 运行中）。

- [ ] **Step 3: 提交**

```bash
git add api/v1/endpoints/health.py
git commit -m "feat: expose runtime scheduler health endpoint"
```

---

### Task 7: Web 运行健康卡片（A2 前端）

**Files:**
- Modify: `apps/dsa-web/src/pages/`（系统设置/settings 页面，新增「调度健康」卡片）
- Modify: `apps/dsa-web/src/locales/featureText.ts`（中英文案）
- Test: 前端 lint + build（无单测按现有设置页测试模式补齐可选项）

**Interfaces:**
- Consumes: `GET /api/v1/health/scheduler-health`
- Produces: 设置中心「系统设置」分类下新增调度健康卡片（纯展示），空态提示而非报错

- [ ] **Step 1: 找到系统设置页卡片入口**：在 `apps/dsa-web/src/pages` 内定位「系统设置」分类对应的展示组件（参考现有设置项门控 `activeCategory === 'system'`），新增一块 `SchedulerHealth` 区域。

- [ ] **Step 2: 请求 `scheduler-health`**：用现有 api 请求封装拉取，字段映射到展示（最近成功/最近失败/连续失败次数/最近原因），无数据或失败时显示空态文案。

- [ ] **Step 3: 加中英文案** 到 `apps/dsa-web/src/locales/featureText.ts`。

- [ ] **Step 4: 验证**（本地已在 8000 运行 server，拉取 endpoint 有数据）。构建检查：

Run: `cd apps/dsa-web && npm run lint && npm run build`
Expected: 无 lint/build 错误。

- [ ] **Step 5: 提交**

```bash
cd apps/dsa-web && git add -A
git commit -m "feat(web): show runtime scheduler health card in system settings"
```

---

### Task 8: 跨发行日自动补跑（A4）

**Files:**
- Modify: `src/services/runtime_scheduler.py`（成功分支后触发补跑检测；新增 `_maybe_trigger_backfill`、`_trading_day_gap_since`）
- Test: `tests/test_runtime_scheduler_backfill.py`

**Interfaces:**
- Consumes: `src.core.trading_calendar.get_effective_trading_date`、`Config.runtime_backfill_enabled/runtime_backfill_max_days`；本类 `_last_success_at`、`_run_analysis_once`
- Produces: 成功分支后判断是否存在跨发行日缺口，存在则上限内自动再跑一次

- [ ] **Step 1: 写失败测试** `tests/test_runtime_scheduler_backfill.py`

```python
from datetime import date
from unittest import mock
from src.services.runtime_scheduler import RuntimeSchedulerService


def test_backfill_triggered_when_gap_beyond_max_days():
    runs = []
    runner = mock.Mock(side_effect=lambda config, args, stocks: (runs.append(1), True)[1])
    svc = RuntimeSchedulerService(
        config_provider=mock.Mock(), task_runner=runner, owns_schedule=False)
    svc._last_success_at = "2026-09-01T18:00:00"  # 距有效交易日（09-05）差 4 天 > max_days=1
    with mock.patch("src.services.runtime_scheduler._effective_trading_date", return_value=date(2026, 9, 5)):
        svc._maybe_trigger_backfill(None)
    assert len(runs) == 1
```

> 注：`_effective_trading_date` 为任务内实现的薄包装（适配 `src.core.trading_calendar.get_effective_trading_date`），测试以 mock 隔离真实日历，避免网络/异步依赖。若执行时 `get_effective_trading_date` 可直接用（无额外封装），则测试改用该名并去掉该包装。

- [ ] **Step 2: 实现**：加 `_maybe_trigger_backfill(stock_codes)` 与 `_trading_day_gap_since(last_success_iso)`：

```python
    def _maybe_trigger_backfill(self, stock_codes: Optional[List[str]]) -> None:
        config = self._reload_config()
        if not getattr(config, "runtime_backfill_enabled", True):
            return
        if not self._last_success_at:
            return
        gap = self._trading_day_gap_since(self._last_success_at)
        max_days = getattr(config, "runtime_backfill_max_days", 1)
        if gap is not None and gap > max_days:
            logger.info("检测到跨发行日缺口（gap=%d），触发一次自动补跑", gap)
            self._run_analysis_once(stock_codes)

    def _trading_day_gap_since(self, last_success_iso: str) -> Optional[int]:
        try:
            last_date = datetime.fromisoformat(last_success_iso).date()
        except (ValueError, TypeError):
            return None
        try:
            from src.core.trading_calendar import get_effective_trading_date
            eff_date = get_effective_trading_date()  # 按现有签名调用；若需 market 参数则由执行者补
            return (eff_date - last_date).days
        except Exception:  # pragma: no cover - calendar unavailable
            return None
```

在 `_run_analysis_locked` 成功分支末尾（`self._last_failure_alert_date = None` 之后）加：

```python
            self._maybe_trigger_backfill(stock_codes)
```

- [ ] **Step 3: 运行验证** → FAIL→PASS（以实际日历签名对齐）。确认 `test_runtime_scheduler_failure_alert.py` 不回归（成功分支新增一行不影响其失败断言）。

- [ ] **Step 4: 提交**

```bash
git add src/services/runtime_scheduler.py tests/test_runtime_scheduler_backfill.py
git commit -m "feat: auto backfill scheduled analysis across missed trading days"
```

---

### Task 9: 文档与 .env.example（A5 文案）

**Files:**
- Modify: `.env.example`（追加 5 个新开关注释说明）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 追加扁平条目）
- Modify: `docs/settings-help.md`（若存在：说明新开关）
- Test: 无（文档变更，核对命令与文件名）

**Constraints:** CHANGELOG `[Unreleased]` 使用扁平格式，每条独立一行 `- [类型] 描述`，不新增 `###` 标题。

- [ ] **Step 1: `.env.example` 追加**（参考 `TRADING_DAY_CHECK_ENABLED` 样式）

```shell
# ---------- 管线自愈 / 失败告警 ----------
# 分析失败时是否发送系统告警到通知渠道（未配置 system_error 渠道时回退报告主渠道）
# RUNTIME_ANALYSIS_FAILURE_ALERT_ENABLED=true
# 数据源连续失败多少次后进入短期熔断（隔离）；默认 3
# DATA_SOURCE_QUARANTINE_THRESHOLD=3
# 数据源熔断后多久尝试自动恢复（秒）；默认 300（5 分钟，与现状一致）
# DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS=300
# 每日分析跨发行日时是否自动补跑
# RUNTIME_BACKFILL_ENABLED=true
# 自动补跑最大跨发行日天数；默认 1
# RUNTIME_BACKFILL_MAX_DAYS=1
```

- [ ] **Step 2: `docs/CHANGELOG.md`** `[Unreleased]` 追加：

```markdown
- [新功能] 管线自愈与失败告警：调度分析失败时主动推送系统告警（复用 system_error 路由，未配置回退报告主渠道，同类型当日去重一次）、设置中心「系统设置」新增调度健康卡片（最近成功/失败/连续失败次数/原因）、数据源连续失败达到阈值进入短期熔断并通知一次（熔断阈值与恢复冷却可配置为 `DATA_SOURCE_QUARANTINE_THRESHOLD` / `DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS`，默认 3 / 300，不改变现状）、跨发行日自动补跑（`RUNTIME_BACKFILL_ENABLED` / `RUNTIME_BACKFILL_MAX_DAYS`，默认开 / 1）
- [文档] `.env.example` 补管线自愈相关开关说明
```

- [ ] **Step 3: `docs/settings-help.md`** 若有系统说明节，追加上述开关的一句话说明。

- [ ] **Step 4: 核对**：`grep -n RUNTIME_ANALYSIS_FAILURE_ALERT_ENABLED .env.example` 存在；Config 字段名与 `.env.example` 键名一致（对照 Task 1）。

- [ ] **Step 5: 提交**

```bash
git add .env.example docs/CHANGELOG.md docs/settings-help.md
git commit -m "docs: document pipeline self-healing toggles"
```

---

## Self-Review（执行前自查结论）

- **Spec 覆盖**：A1→Task2/3；A2→Task5/6/7；A3→Task1/4（含勘误后收窄范围）；A4→Task8；A5→Task1/9。覆盖完整。
- **占位符**：A3 集成（Task7 Web 卡片、Task6 endpoint 注入方式、Task8 日历签名）按实际代码微调处已标注「按实际…对齐」，其余均有可执行代码与测试。
- **类型一致性**：`send_system_alert(content, dedup_key=...)` 在 Task2 定义、Task3/Task4 复用；`health_snapshot()` Task5 定义、Task6 消费；`_get_daily_source_health()` Task1 定义、Task4 消费。签名一致。
- AGENTS.md 合规：commit 步骤在执行时**先征询用户确认**再运行（不自动提交）。
