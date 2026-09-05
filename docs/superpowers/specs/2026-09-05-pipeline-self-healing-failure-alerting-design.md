# 管线自愈 + 失败告警 — 设计文档（A）

- 日期：2026-09-05
- 状态：待实现
- 范围：A `管线自愈 + 失败告警`（B/C/E 为后续独立子项目，不在本 spec 实现）

## 背景与目标

现有生产环境里，每日股票分析由 server 内置 `RuntimeScheduler` 触发（`src/services/runtime_scheduler.py`），
已具备跨进程分析锁（`CrossProcessAnalysisLock`）、失败记录（`_last_error` / `_last_failed_at` /
`_last_success_at` / `_consecutive_failures`）、有界重试（`MAX_SCHEDULED_RETRIES=3`、`RETRY_DELAY_SECONDS=300`，防竞态）。

目标不是从零搭监控，而是**补齐可靠性缺口**，让「每日分析」真正无人值守：

1. 失败不再只 `logger.exception`，而是**主动通知到人**（复用既有通知管道）。
2. 运行健康状态不再只藏在进程内存，**Web/桌面可透视**。
3. 数据源降级不再永久静默，**连续失败自动隔离 + 自动恢复 + 提醒**。
4. 跨发行日的分析提供**有上限的自动补跑**。
5. 以上全部走「不配置可跑，配置后增强」的开关，且对现有股票链路零破坏。

## 现状与可复用 / 需新增（复用矩阵）

| 层 | 现有实现 | 处置 |
| --- | --- | --- |
| 调度编排 | `src/services/runtime_scheduler.py`（锁 + 重试 + 状态记录） | 复用，补钩子 |
| 通知管道 | `src/notification.py` `NotificationService` | 复用（发失败告警） |
| 失败路由通道 | `src/config.py` `notification_system_error_channels`（`NOTIFICATION_SYSTEM_ERROR_CHANNELS`，.env.example:716） | 复用；未配置回退报告主渠道 |
| 数据源管理 | `data_provider/` `DataFetcherManager`（多源 + fallback 已有） | 复用，补隔离计数 |
| 运行诊断 | `src/services/run_flow.py`（run 快照） | 参考，不重构 |
| 健康状态暴露 | 无（`_last_*` 仅内存） | 新增 endpoint + Web 面板 |
| 补跑 | 仅 CLI/回填路径有 | runtime 路径新增 |

## 设计

### A1. 失败告警钩子

**入口**：`_run_analysis_locked` 的 `except` 块，以及 `_schedule_retry_if_needed` 中「重试耗尽」分支。
在这些位置追加一次失败告警发送。

**载荷**：纯系统级消息（一句话失败原因 + 连续失败次数 + 最近一次成功/失败时间 + 建议），
不走 `generate_aggregate_report` 报告载荷。可复用 `NotificationService`，新增一个极薄的
`send_system_alert(payload)` 便捷入口（若现有接口不支持纯文本消息，则补最小方法，不新增通道）。

**路由**：
- 优先 `NOTIFICATION_SYSTEM_ERROR_CHANNELS`；
- 未配置时回退 `NOTIFICATION_REPORT_CHANNELS`（即报告主渠道，符合用户选择）；
- 二者均未配置则跳过发送（不崩），仅保留日志。

**防噪护栏**（避免告警风暴与环路）：
- 同一天同类型（分析失败）只发一次，未配置 cooldown 时用「当日已发即跳过」简单去重；
- 告警自身发送失败不再次触发告警（不发它自己的失败），只记录告警失败日志；
- 分析成功后清零当日失败告警标记（恢复后不再有漏网）。

**开关**：`RUNTIME_ANALYSIS_FAILURE_ALERT_ENABLED`（默认 true）。

### A2. 运行健康面板

**后端**：暴露一个只读 endpoint（沿用现有 `/api/v1/...` 路由定位），返回
`last_success_at` / `last_failed_at` / `consecutive_failures` / `last_error` / `retries_remaining`。
数据源为 `RuntimeSchedulerService` 的实例状态；进程重启后回到干净初值（面板如实显示「本次进程计时」，不做持久化历史）。

**前端**：Web「设置中心 > 系统设置」下新增「调度健康」卡片，展示上述字段；无历史时给空态提示而非报错。
纯展示，不改调度语义。

**效果**：让「自动行为」可见 —— 用户无需翻日志即可看到每天最后一次是否成功、失败了几次、原因是什么。

### A3. 数据源隔离 + 自动恢复

**实测勘误**：隔离/恢复**骨架已存在**——`data_provider/realtime_types.py::CircuitBreaker`，`DataFetcherManager._daily_source_health = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)`
（data_provider/base.py:632，键控 `daily_data:{market}:{fetcher}`，状态 CLOSED→OPEN→HALF_OPEN）。隔离与自动恢复已由该熔断器完成，只是**阈值/冷却写死、熔断时静默无通知**（仅 `logger.debug`，`_daily_source_unavailable_error` 返回 "(CircuitOpen) 数据源短期熔断"）。

因此 A3 **不建熔断器**，只补两处：
- 把 `failure_threshold` / `cooldown_seconds` 暴露为配置（默认维持 3 / 300，不改变现有行为）。
- **熔断打开时通知一次**（复用 A1 通知路径，dedup_key 带 `source-quarantine:{market}:{fetcher}`，避免重复刷屏）。

**目标**：某个数据源连续熔断（隔离）不再静默被 fallback 吞掉，而是可见 + 提醒。

**实现**：在 `DataFetcherManager`（或数据源适配层）为每个源加**连续失败计数**（进程内，带容量上限防内存膨胀）。
- 某源连续失败 ≥ N（默认 3，`DATA_SOURCE_QUARANTINE_THRESHOLD`）→ 临时隔离：**优先级降级但绝不 fail-fast**，
  现有 fallback 继续正常工作（隔离只影响偏好，不阻断管线）；
- 隔离后**通知一次**（复用 A1 的通知路径，消息标明被隔离源）；
- **自动恢复**：超过 `DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS`（默认 86400 = 1 天）或下一交易日到来时，
  重新探测该源（一次轻量探活），成功即移除隔离，失败重新计时。

**风险控制**：隔离是 A 里唯一触碰 `data_provider/` 广度层的改动，必须做到：默认值不改变现有行为、
只影响优先级不影响正确性、探活失败不连锁报错。若实现中复杂度超出预期，允许把 A3 拆出为 A3' 单独小步，
先交付 A1/A2/A4（告警 + 面板 + 补跑）。

### A4. 补跑（跨交易日）

**入口**：日分析完成后（成功或最终失败）检测「最近一次成功时间是否早于上一交易日」。
- 若是，且 `RUNTIME_BACKFILL_ENABLED`（默认 true）→ 以 `RUNTIME_BACKFILL_MAX_DAYS`（默认 1）为上限自动补跑；
- 受跨进程分析锁保护，避免与正常定时并发；失败同样走 A1 告警 + 有界重试；
- 不无限回溯，超出上限仅告警提醒「存在可补的历史缺口」。

### A5. 配置 + 文档

新增配置项（`src/config.py` + `.env.example` 同步）：
- `RUNTIME_ANALYSIS_FAILURE_ALERT_ENABLED`（bool，默认 true）
- `DATA_SOURCE_QUARANTINE_THRESHOLD`（int，默认 3）
- `DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS`（int，默认 86400）
- `RUNTIME_BACKFILL_ENABLED`（bool，默认 true）
- `RUNTIME_BACKFILL_MAX_DAYS`（int，默认 1）

文档：`docs/CHANGELOG.md` `[Unreleased]` 增补；`docs/settings-help.md`（若存在）同步说明新开关；`.env.example` 注释说明。

## 错误处理与边界

- 告警发送失败：只记录告警失败日志，不中断主流程、不触发它自己的告警（防环路）。
- 隔离探活失败：仅在日志体现，不连锁影响其他源。
- 补跑与正常定时竞态：统一受跨进程分析锁保护。
- 进程重启：健康面板 / 隔离计数回到干净初值，上一进程的失败不持久化为「当前状态」。

## 测试

- 单测：失败 → 触发告警调用（mock `NotificationService`，断言载荷与路由回退）；同一天告警去重；重试耗尽告警。
- 单测：隔离计数递增 / 达阈值降级 / 恢复窗口后重新探活。
- 单测：补跑触发条件（最近成功早于上一交易日）与上限。
- 联调：`dry-run` 制造模拟失败，观察日志 + 告警 + 面板字段变化。
- 回归：确认重试、跨进程锁、非交易日门控行为不变。

## 未验证项 / 风险

- A3 数据源隔离是广度改动：默认值必须不扰动现有 fallback 行为，需在真实数据源上验证「隔离→探活→恢复」闭环。
- 通知路由回退链路（system_error → report）需真实渠道验证一次。
- 补跑上限与跨交易日判定依赖交易日历，需模拟跨日场景。

## 回滚方式

- 各开关默认值均为「不配置可跑」，回滚只需关闭对应 `*_ENABLED` 或移除新增配置项；
- 新增代码均挂在既有钩子（except 分支 / DataFetcherManager 计数），移除钩子即回到现行为；
- 无迁移、无数据契约变更，前端卡片为纯展示，删除卡片即可。
