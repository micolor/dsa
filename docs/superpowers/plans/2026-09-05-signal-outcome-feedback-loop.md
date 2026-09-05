# 信号后验闭环：自动化 + Skill 层落地展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让信号后验闭环能自动运转（每日自动评估 decision-signal 与 skill 意见的后验），并把当前只存在于内部、无 API/Web 的 skill 层命中率聚合展示出来。

**Architecture:** 后端在 `RuntimeSchedulerService` 增加一个配置门控、幂等的每日后台任务，周期调用两个已存在 outcome service 的 `run_outcomes()`；后端补两个 skill 层 endpoint（`POST /skill-outcomes/run`、`GET /skill-outcomes/stats`）；Web 在决策信号页新增「skill 表现」聚合面板。决策信号层的已有展示不重建。

**Tech Stack:** Python / FastAPI / pydantic；React + TypeScript。复用现有 `run_outcomes` service、`admin_session_cookie` 认证、Web toCamelCase mapper 与 Card 布局。

**Spec:** `docs/superpowers/specs/2026-09-05-signal-outcome-feedback-loop-design.md`

## Global Constraints

- 覆盖的原子项目范围：只做「自动化 + Skill 层落地展示」，**不重建**已存在的 decision-signal 后验展示，不改 `backtest_service.get_skill_summary` 那条硬桩。
- 新增配置 `signal_outcome_auto_eval_enabled`（默认 `True`）需同步 `.env.example` 与 `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目，无 `### 子标题`）。
- 后台任务与单 service 均 `try/except` + `logger.warning` 防御，**禁止**让异常污染调度循环。
- 沿用现有 window/命名惯例：后台任务 `name="signal_outcome_evaluation"`；endpoint `operation_id` 参考既有命名。
- `api/v1/endpoints/decision_signals.py` 集成 `AUTH_RESPONSE`、`_bad_request`、`_internal_error` 这些既有 helper。
- **实现者不执行 `git commit`**（AGENTS.md 硬性规定：未经用户明确确认不得 commit）。实现者只写代码 + 测试 + 跑测试；提交由协调者事后批量、确认后执行（与 A 子项目一致）。
- 修改 `src/config.py` 的新字段 + env parse 两处必须同步（:1211 区域 + :2184 区域），且补 `.env.example`。

---

### Task 1: 配置项 + 调度后台任务（自动化）

**Files:**
- Modify: `src/config.py:1211`（字段区）与 `src/config.py:2184`（env parse 区）
- Modify: `src/services/runtime_scheduler.py`（新增 `_current_signal_outcome_background_tasks` + 接线进 `_current_background_tasks`，:406-411）
- Modify: `.env.example`（新增注释 key）
- Test: `tests/test_runtime_scheduler_signal_outcome_tasks.py`

**Interfaces:**
- Consumes: `SkillOpinionOutcomeService().run_outcomes()`（`src/services/skill_opinion_outcome_service.py:49`）、`DecisionSignalOutcomeService().run_outcomes()`（`src/services/decision_signal_outcome_service.py:96`），两 service 在任务闭包内**懒导入**。
- Produces: `RuntimeSchedulerService._current_signal_outcome_background_tasks(self, config)` → `List[Dict[str, Any]]`，注册名 `signal_outcome_evaluation`；
  `self._current_background_tasks(config)` 现返回 agent_event_monitor + paper_valuation + signal_outcome 三组任务。
- Produces: 新配置字段 `signal_outcome_auto_eval_enabled: bool = True`。

- [ ] **Step 1: 写失败测试** `tests/test_runtime_scheduler_signal_outcome_tasks.py`

```python
# -*- coding: utf-8 -*-
"""RuntimeSchedulerService signal-outcome background task unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from src.services.runtime_scheduler import RuntimeSchedulerService


def _svc():
    svc = RuntimeSchedulerService(config_provider=mock.Mock(), owns_schedule=False)
    svc._analysis_lock_path = lambda config: "/tmp/test-signal-outcome.lock"
    return svc


enabled_config = SimpleNamespace(signal_outcome_auto_eval_enabled=True)
disabled_config = SimpleNamespace(signal_outcome_auto_eval_enabled=False)


def test_task_registered_when_enabled():
    svc = _svc()
    tasks = svc._current_signal_outcome_background_tasks(enabled_config)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "signal_outcome_evaluation"
    assert tasks[0]["run_immediately"] is True
    assert tasks[0]["interval_seconds"] == 1800
    assert callable(tasks[0]["task"])


def test_task_removed_when_disabled():
    svc = _svc()
    svc._background_task_cache["signal_outcome_evaluation"] = {
        "task": lambda: None,
        "interval_seconds": 1800,
    }
    svc._background_task_registered_names.add("signal_outcome_evaluation")
    assert svc._current_signal_outcome_background_tasks(disabled_config) == []
    assert "signal_outcome_evaluation" not in svc._background_task_cache
    assert "signal_outcome_evaluation" not in svc._background_task_registered_names


def test_task_invokes_both_services():
    svc = _svc()
    task = svc._current_signal_outcome_background_tasks(enabled_config)[0]["task"]
    skill = mock.Mock()
    decision = mock.Mock()
    with mock.patch(
        "src.services.skill_opinion_outcome_service.SkillOpinionOutcomeService",
        return_value=skill,
    ), mock.patch(
        "src.services.decision_signal_outcome_service.DecisionSignalOutcomeService",
        return_value=decision,
    ):
        task()
    skill.run_outcomes.assert_called_once()
    decision.run_outcomes.assert_called_once()


def test_task_survives_service_failure():
    svc = _svc()
    task = svc._current_signal_outcome_background_tasks(enabled_config)[0]["task"]
    skill = mock.Mock()
    skill.run_outcomes.side_effect = RuntimeError("boom")
    decision = mock.Mock()
    with mock.patch(
        "src.services.skill_opinion_outcome_service.SkillOpinionOutcomeService",
        return_value=skill,
    ), mock.patch(
        "src.services.decision_signal_outcome_service.DecisionSignalOutcomeService",
        return_value=decision,
    ):
        task()  # must not raise
    decision.run_outcomes.assert_called_once()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_runtime_scheduler_signal_outcome_tasks.py -v`
Expected: FAIL — `AttributeError: 'RuntimeSchedulerService' object has no attribute '_current_signal_outcome_background_tasks'`。

- [ ] **Step 3: 读现有实现**（克隆目标）——读 `src/services/runtime_scheduler.py` 的 `_current_paper_valuation_background_tasks`(:413) 与 `_current_background_tasks`(:406)，照其模式写新方法。

- [ ] **Step 4: 写最小实现**

`src/config.py` 字段区（在 `runtime_backfill_max_days: int = 1` 之后，:1212 后面追加）：

```python
    # 是否启用每日信号后验自动评估（决策信号 + skill 意见）
    signal_outcome_auto_eval_enabled: bool = True
```

`src/config.py` env parse 区（在 `runtime_backfill_max_days=...` 之后，:2192 后面追加）：

```python
            signal_outcome_auto_eval_enabled=(
                os.getenv('SIGNAL_OUTCOME_AUTO_EVAL_ENABLED', 'true').lower() != 'false'
            ),
```

`src/services/runtime_scheduler.py` — `_current_background_tasks` 改为：

```python
    def _current_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        if self._background_tasks_provider is not None:
            return self._background_tasks_provider(config)
        tasks = list(self._current_agent_event_monitor_background_tasks(config))
        tasks.extend(self._current_paper_valuation_background_tasks(config))
        tasks.extend(self._current_signal_outcome_background_tasks(config))
        return tasks
```

追加新方法（放在 `_current_paper_valuation_background_tasks` 之后）：

```python
    def _current_signal_outcome_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        name = "signal_outcome_evaluation"
        if not getattr(config, "signal_outcome_auto_eval_enabled", True):
            self._background_task_cache.pop(name, None)
            self._background_task_registered_names.discard(name)
            return []

        cached = self._background_task_cache.get(name)
        if cached is None:
            interval_seconds = 1800  # 30 min; run_outcomes is idempotent per outcome key

            def signal_outcome_task() -> None:
                from src.services.skill_opinion_outcome_service import (
                    SkillOpinionOutcomeService,
                )
                from src.services.decision_signal_outcome_service import (
                    DecisionSignalOutcomeService,
                )

                try:
                    SkillOpinionOutcomeService().run_outcomes()
                except Exception as exc:  # pragma: no cover - defensive branch
                    logger.warning("skill opinion outcome evaluation failed: %s", exc)
                try:
                    DecisionSignalOutcomeService().run_outcomes()
                except Exception as exc:  # pragma: no cover - defensive branch
                    logger.warning("decision signal outcome evaluation failed: %s", exc)

            cached = {"task": signal_outcome_task, "interval_seconds": interval_seconds}
            self._background_task_cache[name] = cached

        run_immediately = name not in self._background_task_registered_names
        self._background_task_registered_names.add(name)
        return [{
            "task": cached["task"],
            "interval_seconds": int(cached["interval_seconds"]),
            "run_immediately": run_immediately,
            "name": name,
        }]
```

`.env.example`（在 `RUNTIME_BACKFILL_MAX_DAYS` 注释后面）追加：

```bash
# 是否启用每日信号后验自动评估（决策信号 + skill 意见）
#SIGNAL_OUTCOME_AUTO_EVAL_ENABLED=true
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run python -m pytest tests/test_runtime_scheduler_signal_outcome_tasks.py -v`
Expected: PASS（4 个测试）。

Run: `uv run python -m pytest tests/test_runtime_scheduler_service.py tests/test_runtime_scheduler_backfill.py -q`
Expected: 无新增回归（既有测试通过；已知存在的环境相关失败不属本次 diff）。

- [ ] **Step 6: 编译检查**

Run: `uv run python -m py_compile src/services/runtime_scheduler.py src/config.py`
Expected: 无输出（编译成功）。

---

### Task 2: Skill 层 API endpoint + schemas（展示）

**Files:**
- Modify: `api/v1/schemas/decision_signals.py`（新增 4 个模型）
- Modify: `api/v1/endpoints/decision_signals.py`（新增 2 个 endpoint + imports）
- Test: `tests/test_skill_outcome_api.py`
- Docs: `docs/CHANGELOG.md`（`[Unreleased]` 追加扁平条目）

**Interfaces:**
- Consumes: `SkillOpinionOutcomeService.run_outcomes()` 返回 `{items[], processed_keys, created, updated, skipped, failed, errors[], limit_unit, engine_version}`（:131-141）；`SkillOpinionPerformanceService.get_stats(skill_id=None, skill_ids=None, horizons=None)` 返回 `{engine_version, minimum_evaluated_sample_size, buckets[]}`（:35-85）。
- Consumes: `api/v1/schemas/decision_signals.py` 既有 base `pydantic.BaseModel`；`api/v1/endpoints/decision_signals.py` 既有 `_bad_request`、`_internal_error`、`AUTH_RESPONSE`、`admin_session_cookie`、`ErrorResponse`。
- Produces: `POST /api/v1/decision-signals/skill-outcomes/run`（body `SkillOpinionOutcomeRunRequest` → `SkillOpinionOutcomeRunResponse`）；`GET /api/v1/decision-signals/skill-outcomes/stats`（query `skill_id`/`skill_ids`/`horizons` → `SkillOpinionPerformanceStatsResponse`）。

- [ ] **Step 1: 写失败测试** `tests/test_skill_outcome_api.py`

镜像 `tests/test_decision_signal_outcome_api.py` 的 `client_and_db` fixture 与 `_reset_auth_globals`：

```python
# -*- coding: utf-8 -*-
"""API tests for skill-opinion outcome run + performance stats endpoints."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


@pytest.fixture()
def client_and_db(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "skill_outcome_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    _reset_auth_globals()
    Config.reset_instance()
    DatabaseManager.reset_instance()
    app = create_app(static_dir=Path(static_dir))
    client = TestClient(app)
    try:
        yield client
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        _reset_auth_globals()
        if old_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = old_env_file
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def test_run_skill_outcomes_returns_engine_version(client_and_db):
    client, *_ = client_and_db
    response = client.post("/api/v1/decision-signals/skill-outcomes/run", json={"limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine_version"] == "skill-opinion-outcome-v1"
    assert body["limit_unit"] == "outcome_key"
    assert "items" in body and "created" in body and "skipped" in body


def test_run_skill_outcomes_rejects_bad_limit(client_and_db):
    client, *_ = client_and_db
    response = client.post("/api/v1/decision-signals/skill-outcomes/run", json={"limit": 0})
    assert response.status_code == 422


def test_skill_outcome_stats_shape(client_and_db):
    client, *_ = client_and_db
    response = client.get("/api/v1/decision-signals/skill-outcomes/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine_version"] == "skill-opinion-outcome-v1"
    assert body["minimum_evaluated_sample_size"] == 30
    assert isinstance(body["buckets"], list)


def test_skill_outcome_stats_rejects_bad_horizon(client_and_db):
    client, *_ = client_and_db
    response = client.get(
        "/api/v1/decision-signals/skill-outcomes/stats", params={"horizons": ["bad"]}
    )
    assert response.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_skill_outcome_api.py -v`
Expected: FAIL — 路径 `/api/v1/decision-signals/skill-outcomes/run` 返回 405/404。

- [ ] **Step 3: 写 schemas**（追加到 `api/v1/schemas/decision_signals.py` 末尾）

```python
class SkillOpinionOutcomeItem(BaseModel):
    id: int
    skill_opinion_sample_id: int
    analysis_history_id: Optional[int] = None
    stock_code: str
    skill_id: str
    signal: Optional[Any] = None
    horizon: str
    engine_version: str
    eval_status: str
    outcome: Optional[str] = None
    direction_correct: Optional[bool] = None
    unable_reason: Optional[str] = None
    analysis_date: Optional[str] = None
    start_trade_date: Optional[str] = None
    end_trade_date: Optional[str] = None
    start_price: Optional[float] = None
    end_close: Optional[float] = None
    stock_return_pct: Optional[float] = None
    directional_return_pct: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SkillOpinionOutcomeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: Optional[int] = Field(None, gt=0)
    analysis_history_id: Optional[int] = Field(None, gt=0)
    skill_id: Optional[str] = Field(None, json_schema_extra={"maxLength": 64})
    stock_code: Optional[str] = Field(None, json_schema_extra={"maxLength": 32})
    horizons: Optional[List[str]] = None
    limit: int = Field(100, ge=1, le=500)


class SkillOpinionOutcomeRunResponse(BaseModel):
    items: List[SkillOpinionOutcomeItem] = Field(default_factory=list)
    processed_keys: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    limit_unit: str
    engine_version: str


class SkillOpinionPerformanceBucket(BaseModel):
    skill_id: str
    horizon: str
    engine_version: str
    total: int
    pending: int
    evaluated: int
    observational: int
    unable: int
    hit: int
    miss: int
    sample_sufficient: bool
    sample_status: str
    hit_rate_pct: Optional[float] = None
    miss_rate_pct: Optional[float] = None
    avg_directional_return_pct: Optional[float] = None
    unable_rate_pct: Optional[float] = None


class SkillOpinionPerformanceStatsResponse(BaseModel):
    engine_version: str
    minimum_evaluated_sample_size: int
    buckets: List[SkillOpinionPerformanceBucket] = Field(default_factory=list)
```

- [ ] **Step 4: 写 endpoints**（追加到 `api/v1/endpoints/decision_signals.py` 末尾）

新增 imports（与其他 service import 并列）：

```python
from src.services.skill_opinion_outcome_service import SkillOpinionOutcomeService
from src.services.skill_opinion_performance_service import SkillOpinionPerformanceService
```

在 schemas import 块新增：
```python
    SkillOpinionOutcomeRunRequest,
    SkillOpinionOutcomeRunResponse,
    SkillOpinionPerformanceStatsResponse,
```

两个 endpoint：

```python
@router.post(
    "/skill-outcomes/run",
    response_model=SkillOpinionOutcomeRunResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "请求字段非法"},
        422: {"model": ErrorResponse, "description": "请求体校验失败"},
        500: {"model": ErrorResponse, "description": "后验计算失败"},
    },
    summary="触发 skill 意见后验评估",
    description=(
        "显式触发 skill-level outcome 计算；默认只处理缺失/待评估 outcome key，"
        "limit 控制单次最多评估的 key 数。"
    ),
    operation_id="runSkillOpinionOutcomes",
)
def run_skill_outcomes(request: SkillOpinionOutcomeRunRequest) -> SkillOpinionOutcomeRunResponse:
    service = SkillOpinionOutcomeService()
    try:
        return SkillOpinionOutcomeRunResponse(
            **service.run_outcomes(
                sample_id=request.sample_id,
                analysis_history_id=request.analysis_history_id,
                skill_id=request.skill_id,
                stock_code=request.stock_code,
                horizons=request.horizons,
                limit=request.limit,
            )
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Run skill opinion outcomes failed", exc)


@router.get(
    "/skill-outcomes/stats",
    response_model=SkillOpinionPerformanceStatsResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "查询参数非法"},
        422: {"model": ErrorResponse, "description": "查询参数校验失败"},
        500: {"model": ErrorResponse, "description": "统计失败"},
    },
    summary="查询 skill 意见后验统计",
    description=(
        "按 skill / horizon 聚合命中率统计；低于最小评估样本数（30）的 bucket "
        "标记为 observational（sample_sufficient=false），hit_rate_pct 为 null。"
    ),
    operation_id="getSkillOpinionOutcomeStats",
)
def get_skill_outcome_stats(
    skill_id: Optional[str] = Query(None),
    skill_ids: Optional[List[str]] = Query(None),
    horizons: Optional[List[str]] = Query(None),
) -> SkillOpinionPerformanceStatsResponse:
    service = SkillOpinionPerformanceService()
    try:
        return SkillOpinionPerformanceStatsResponse(
            **service.get_stats(
                skill_id=skill_id,
                skill_ids=skill_ids,
                horizons=horizons,
            )
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get skill opinion outcome stats failed", exc)
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run python -m pytest tests/test_skill_outcome_api.py -v`
Expected: PASS（4 个测试）。

Run: `uv run python -m py_compile api/v1/schemas/decision_signals.py api/v1/endpoints/decision_signals.py`
Expected: 无输出。

- [ ] **Step 6: 更新 CHANGELOG**（`docs/CHANGELOG.md` 的 `[Unreleased]`，扁平、无 `###` 子标题）

```markdown
- [新功能] 信号后验自动化：每日自动评估决策信号与 skill 意见后验
- [新功能] Skill 表现聚合 API（/decision-signals/skill-outcomes/*）与 Web 面板
```

---

### Task 3: Web skill 表现面板（落地展示）

**Files:**
- Modify: `apps/dsa-web/src/types/decisionSignals.ts`
- Modify: `apps/dsa-web/src/api/decisionSignals.ts`
- Modify: `apps/dsa-web/src/pages/DecisionSignalsPage.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`

**Interfaces:**
- Consumes: `GET /api/v1/decision-signals/skill-outcomes/stats` → `{engine_version, minimum_evaluated_sample_size, buckets[]}`；`POST /api/v1/decision-signals/skill-outcomes/run` → `{...engine_version}`。
- Produces: 面板函数 `loadSkillOutcomeStats()`、`runSkillOutcomes()`；类型 `SkillOpinionPerformanceStatsResponse`、`SkillOpinionPerformanceBucket`、`SkillOpinionOutcomeRunResponse`、`SkillOpinionOutcomeRunRequest`；i18n key 组 `decisionSignals.skillStatsTitle/...`（zh+en）。
- 依赖既有：`apiClient`、`toCamelCase`、`Card`、`ApiErrorAlert`、`t()`。

- [ ] **Step 1: 读现有实现**——读 `apps/dsa-web/src/pages/DecisionSignalsPage.tsx` 的 `loadOutcomeStats()`（:1450 附近）、`outcomeStats` state、`DecisionSignalProfileCalibration` 组件用法、`formatStatPercent`、`useApiRequest` 或等效数据拉取方式；照此模式写 skill 面板。

- [ ] **Step 2: 新增类型**（追加到 `apps/dsa-web/src/types/decisionSignals.ts`）

```typescript
export interface SkillOpinionPerformanceBucket {
  skillId: string;
  horizon: string;
  engineVersion: string;
  total: number;
  pending: number;
  evaluated: number;
  observational: number;
  unable: number;
  hit: number;
  miss: number;
  sampleSufficient: boolean;
  sampleStatus: string;
  hitRatePct: number | null;
  missRatePct: number | null;
  avgDirectionalReturnPct: number | null;
  unableRatePct: number | null;
}

export interface SkillOpinionPerformanceStatsResponse {
  engineVersion: string;
  minimumEvaluatedSampleSize: number;
  buckets: SkillOpinionPerformanceBucket[];
}

export interface SkillOpinionOutcomeRunRequest {
  sampleId?: number;
  analysisHistoryId?: number;
  skillId?: string;
  stockCode?: string;
  horizons?: string[];
  limit?: number;
}

export interface SkillOpinionOutcomeRunResponse {
  engineVersion: string;
  processedKeys: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
}
```

- [ ] **Step 3: 新增 API 方法**（追加到 `apps/dsa-web/src/api/decisionSignals.ts`，参照既有 mapper 风格）

```typescript
function toSkillOpinionPerformanceStatsResponse(data: Record<string, unknown>): SkillOpinionPerformanceStatsResponse {
  const response = toCamelCase<SkillOpinionPerformanceStatsResponse>(data);
  response.buckets = Array.isArray(data.buckets)
    ? data.buckets.map((bucket) => toCamelCase<SkillOpinionPerformanceBucket>(bucket as Record<string, unknown>))
    : [];
  return response;
}

function toSkillOpinionOutcomeRunResponse(data: Record<string, unknown>): SkillOpinionOutcomeRunResponse {
  return toCamelCase<SkillOpinionOutcomeRunResponse>(data);
}

async getSkillOutcomeStats(
  params: { skillId?: string; skillIds?: string[]; horizons?: string[] } = {},
): Promise<SkillOpinionPerformanceStatsResponse> {
  const response = await apiClient.get<Record<string, unknown>>('/api/v1/decision-signals/skill-outcomes/stats', { params });
  return toSkillOpinionPerformanceStatsResponse(response.data);
}

async runSkillOutcomes(params: SkillOpinionOutcomeRunRequest): Promise<SkillOpinionOutcomeRunResponse> {
  const response = await apiClient.post<Record<string, unknown>>('/api/v1/decision-signals/skill-outcomes/run', params);
  return toSkillOpinionOutcomeRunResponse(response.data);
}
```

- [ ] **Step 4: 新增 i18n key**（`apps/dsa-web/src/i18n/uiText.ts`，zh + en 各加一组）

zh（`decisionSignals` 命名空间内）：
```
skillStatsTitle: 'Skill 表现',
skillStatsDescription: '各 skill 信号在 1d/3d/5d/10d 窗口的后验命中率（样本低于最小阈值时仅作观察）',
skillStatsSampleSize: '最小评估样本数',
skillStatsPending: '待评估',
skillStatsNewItems: '新增评估',
skillStatsEvaluated: '已评估',
skillStatsHitRate: '命中率',
skillStatsAvgReturn: '平均收益',
skillStatsSampleStatus: '样本充分性',
skillStatsRefresh: '刷新',
skillStatsRun: '手动评估',
skillStatsErrorTitle: '加载 Skill 表现失败',
```
en：对应英文文案（`Skill Performance`、`Hit rate of each skill signal over 1d/3d/5d/10d windows`、`Min evaluated sample size`、`Pending`、`Newly evaluated`、`Evaluated`、`Hit rate`、`Avg return`、`Sample sufficiency`、`Refresh`、`Run evaluation`、`Failed to load skill performance`）。

- [ ] **Step 5: 新增 Web 面板**（`apps/dsa-web/src/pages/DecisionSignalsPage.tsx`）

在页面新增 state + 拉取函数（镜像 `loadOutcomeStats`）：

```typescript
const [skillStats, setSkillStats] = useState<SkillOpinionPerformanceStatsResponse | null>(null);
const [skillStatsLoading, setSkillStatsLoading] = useState(false);
const [skillStatsError, setSkillStatsError] = useState<unknown>(null);

const loadSkillOutcomeStats = useCallback(async () => {
  setSkillStatsLoading(true);
  setSkillStatsError(null);
  try {
    const data = await decisionSignalsApi.getSkillOutcomeStats();
    setSkillStats(data);
  } catch (err) {
    setSkillStatsError(err);
  } finally {
    setSkillStatsLoading(false);
  }
}, []);

const handleRunSkillOutcomes = useCallback(async () => {
  try {
    await decisionSignalsApi.runSkillOutcomes({});
    await loadSkillOutcomeStats();
  } catch (err) {
    setSkillStatsError(err);
  }
}, [loadSkillOutcomeStats]);
```

（`useEffect(() => { void loadSkillOutcomeStats(); }, [loadSkillOutcomeStats])`；如页面用其它方式初次拉取，按既有上下文接入。）

渲染（在既有 stats Card 之后、或作为独立 Card 以 `Card title={t('decisionSignals.skillStatsTitle')}` 呈现），遍历 `skillStats.buckets` 展示每行 `skillId / horizon / total / pending / hit / miss / hitRatePct / sampleStatus`，并提供「刷新」「手动评估」按钮（`onClick={handleRunSkillOutcomes}`）。

- [ ] **Step 6: 构建验证**

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint + build 通过。

---

### Task 4: 交付核对（文档 + 一致性）

**Files:**
- Review: `.env.example`、`docs/CHANGELOG.md`、`src/config.py`（字段 + env parse 两处一致）
- Review: `api/v1/endpoints/decision_signals.py` 路由无 `/{signal_id}` 冲突

**Interfaces:**
- 无需新接口。

- [ ] **Step 1: 核对配置一致性**——确认 `src/config.py` 字段区与 env parse 区各有一处 `signal_outcome_auto_eval_enabled`，且 `.env.example` 有对应注释 key。

- [ ] **Step 2: 核对 CHANGELOG**——确认 `[Unreleased]` 两条扁平条目已存在、无 `###` 子标题。

- [ ] **Step 3: 核对后端测试无回归**

Run: `./scripts/ci_gate.sh`
Expected: 通过（若 CI gate 依赖网络/长时间，可改为 `uv run python -m pytest tests/test_runtime_scheduler_signal_outcome_tasks.py tests/test_skill_outcome_api.py -q`）。

- [ ] **Step 4: docs/K dokument**——本子项目无 `AGENTS.md`/`.claude/skills` 改动，不触发 `check_ai_assets.py`；在交付说明写明。
