# 信号后验闭环：自动化 + Skill 层落地展示 — 设计文档

> 子项目 B（信号后验闭环）设计。范围在 brainstorm 阶段已与用户确认：
> **自动化 + 落地展示**。决策信号（decision-signal）层的后验聚合展示已存在，
> 本设计只补两个真实的缺口：(a) 后验评估没有自动触发；(b) 单个 skill 的表现
>（命中率）没有 API / Web 展示。

## 1. 背景与现状

仓库已建成信号后验闭环的大部分后端能力：

- **决策信号层**（`DecisionSignal`）：
  - `DecisionSignalOutcomeService.run_outcomes()`（`src/services/decision_signal_outcome_service.py:96`）
    显式计算 signal-level 后验，返回 `{items, evaluated, created, updated, skipped, engine_version}`。
  - `DecisionSignalOutcomeService.get_stats()`（:322）聚合统计，含 `breakdowns` 与 `profile_calibration`。
  - API `POST /api/v1/decision-signals/outcomes/run`、`GET /api/v1/decision-signals/outcomes/stats`、
    `GET /api/v1/decision-signals/outcomes` 均已存在，且**已在 Web 展示**（`DecisionSignalsPage.tsx:1444` 起）。
- **Skill 意见层**（`SkillOpinion`，来自 `agent/` 的各 skill）：
  - `SkillOpinionOutcomeService.run_outcomes()`（`src/services/skill_opinion_outcome_service.py:49`）
    评估缺失/待定 outcome key，返回
    `{items, processed_keys, created, updated, skipped, failed, errors, limit_unit, engine_version}`。
  - `SkillOpinionPerformanceService.get_stats()`（`src/services/skill_opinion_performance_service.py:35`）
    返回 `{engine_version, minimum_evaluated_sample_size, buckets}`；每个 bucket 含
    `skill_id, horizon, total, pending, evaluated, observational, unable, hit, miss,
    sample_sufficient, sample_status, hit_rate_pct, miss_rate_pct, avg_directional_return_pct, unable_rate_pct`。

**两个真实缺口**：

1. **自动触发缺失**：无论 decision-signal 还是 skill 层，`run_outcomes` 都只有人工触发
   （`POST /decision-signals/outcomes/run`），没有定时自动批量评估，闭环无法自我运转。
2. **Skill 层无落地展示**：`SkillOpinionPerformanceService.get_stats()` 是**纯内部**使用
   （仅被 `skill_opinion_weight_service.py` → `agent/skills/aggregator.py` 消费），
   **无 API endpoint、无 Web 组件**。用户无法看到"哪个 skill 的预测命中率是多少"。

## 2. 变更范围

> 只补上述两个缺口，不重建已有闭环（AGENTS.md「不新增平行实现」「克制重构」）。

### 变更 1：自动化调度（补"自动"）

在 `RuntimeSchedulerService` 增加一个**配置门控的、幂等的每日后台任务**，周期执行
`SkillOpinionOutcomeService.run_outcomes()` 与 `DecisionSignalOutcomeService.run_outcomes()`，
让后验闭环每天自动补齐缺失的 outcome key。

- **宿主**：`src/services/runtime_scheduler.py`，镜像现成的
  `_current_paper_valuation_background_tasks()`（:413）模式：
  - 新增 `_current_signal_outcome_background_tasks(self, config)`。
  - 门控：新配置 `signal_outcome_auto_eval_enabled`（默认 `True`）。为 `False` 时
    `pop` 缓存 + `discard` 注册名（与 paper 一致），返回 `[]`。
  - 未缓存时构造 `task`：分别调用两个 service 的 `run_outcomes()`，各自 `try/except`
    包裹，失败只记 warning，**绝不抛出**（paper_valuation_task 同款防御）。
  - `interval_seconds = 1800`（30 分钟；`run_outcomes` 天然幂等，只处理待评估 key）。
  - `run_immediately = name not in self._background_task_registered_names`（进程内首次注册即触发一次，随后周期性）。
  - `name = "signal_outcome_evaluation"`。
- **接入**：`_current_background_tasks()`（:406）中
  `tasks.extend(self._current_signal_outcome_background_tasks(config))`。
- **幂等性**：两次 `run_outcomes()` 之间，已 `completed`/终态 `unable` 的 key 会被跳过
  （service 只选缺失/可恢复待定候选），因此重复触发安全。

### 变更 2：Skill 层 API（补"展示"）

在 `api/v1/endpoints/decision_signals.py` 新增两个 endpoint（沿用现有
`admin_session_cookie` 认证、error 映射惯例）：

- `POST /api/v1/decision-signals/skill-outcomes/run`
  - 请求 `SkillOpinionOutcomeRunRequest`：`{sample_id?, analysis_history_id?, skill_id?, stock_code?, horizons?, limit=100}`。
    (字段可空；`horizons` 取值见 `SUPPORTED_SKILL_OUTCOME_HORIZONS` = 1d/3d/5d/10d；`limit` 1..500)
  - 响应 `SkillOpinionOutcomeRunResponse`：`{items[], processed_keys, created, updated, skipped, failed, errors[], limit_unit, engine_version}`。
  - 语义：`SkillOpinionOutcomeService().run_outcomes(sample_id=..., analysis_history_id=..., skill_id=..., stock_code=..., horizons=..., limit=...)`。
- `GET /api/v1/decision-signals/skill-outcomes/stats`
  - 参数：`skill_id?`, `skill_ids?`, `horizons?`
  - 响应 `SkillOpinionPerformanceStatsResponse`：`{engine_version, minimum_evaluated_sample_size, buckets[]}`，
    bucket 字段按 `SkillOpinionPerformanceService.get_stats()` 序列化输出。
  - 语义：`SkillOpinionPerformanceService().get_stats(skill_id=..., skill_ids=..., horizons=...)`，
    过滤当前 engine_version（`skill-opinion-outcome-v1`）。

> 新增 schema 类放入 `api/v1/schemas/decision_signals.py`（沿用该文件风格）。

### 变更 3：Skill 层 Web（补"落地"）

在 `apps/dsa-web` 的决策信号页新增「skill 表现」聚合面板，镜像现有 decision-signal stats 渲染：

- `src/types/decisionSignals.ts`：新增 `SkillOpinionPerformanceBucket`、`SkillOpinionPerformanceStatsResponse`、
  `SkillOpinionOutcomeRunRequest`、`SkillOpinionOutcomeRunResponse` 接口。
- `src/api/decisionSignals.ts`：新增 `getSkillOutcomeStats(params)` 与 `runSkillOutcomes(params)`，
  含 toCamelCase mapper、snake→camel 归一（`minimum_evaluated_sample_size`→`minimumEvaluatedSampleSize` 等）。
- `src/pages/DecisionSignalsPage.tsx`：新增 skill 表现卡片，调用 `getSkillOutcomeStats`，展示
  每 skill×horizon bucket 的 `total/pending/evaluated/hit/miss/sample_status/hit_rate_pct/avg_directional_return_pct`；
  提供「刷新」与「手动评估」按钮（后者调用 `runSkillOutcomes`）。
- `src/i18n/uiText.ts`：新增 zh + en 文案 key。

> 决策信号层展示已存在，**不重复建**。skill 层为新增面板。

## 3. 配置项（新增）

| 字段 | 类型 | 默认 | env | 说明 |
| --- | --- | --- | --- | --- |
| `signal_outcome_auto_eval_enabled` | `bool` | `True` | `SIGNAL_OUTCOME_AUTO_EVAL_ENABLED` | 是否启用每日后验自动评估 |

- `src/config.py` 增加字段（放 `runtime_backfill_*` 之后，见 :1211 区域）并同步 env parse（:2184 区域）。
- `.env.example` 增加对应注释 key。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 追加**扁平**条目：
  - `- [新功能] 信号后验自动化：每日自动评估决策信号与 skill 意见后验`
  - `- [新功能] Skill 表现聚合 API 与 Web 面板`

## 4. 验证矩阵

- **Python 后端**：`./scripts/ci_gate.sh`；改动文件 `python -m py_compile`。
  新增测试：
  - `tests/test_runtime_scheduler_signal_outcome_tasks.py`：enabled 时注册任务、disabled 时清除注册名、
    任务体在单 service 异常时不向上抛。
  - `tests/test_skill_outcome_api.py`：`POST /skill-outcomes/run`（400 校验、正常返回）、
    `GET /skill-outcomes/stats`（bucket 序列化）。
- **Web**：`cd apps/dsa-web && npm ci && npm run lint && npm run build`。
- **文档/治理**：无 `AGENTS.md`/`.claude/skills` 改动，不触发 `check_ai_assets.py`。

## 5. 风险与回滚

- **风险**：后台任务的 `run_outcomes` 意外抛异常会污染调度循环 → 用 try/except + warning 防住，
  与 `paper_valuation_task`、`agent_event_monitor` 同款护栏。
- **风险**：skill `run_outcomes` 默认 `limit=100`，重启后靠 `run_immediately` 首触 + 幂等逐步收敛，不会一次全量卡死。
- **回滚**：改回的仅为新增方法/端点/面板，均独立可回退；将 `signal_outcome_auto_eval_enabled` 置 `false`
  即停用自动调度（后台任务清缓存、不再注册），不破坏任何既有路径。

## 6. 不在本子项目范围（明确不做）

- 不重建 decision-signal 后验展示（已存在）。
- 不加决策信号层 API 之外的新 schema/字段，不改 `backtest_service.get_skill_summary` 那条硬桩。
- 不做 skill 表现的历史止损/校准曲线等增强（YAGNI，超出"自动化+落地展示"边界）。
