# 通知投递回执：可追溯 + Web 落地 — 设计文档

> 子项目 C（通知可靠性/可追溯）设计。范围在 brainstorm 阶段已与用户确认：
> **只做回执持久化 + Web 视图 + docs 校对（推荐）**，不做编排层重试。

## 1. 背景与现状

仓库的通知子系统（`src/notification.py` 的 `NotificationService`，14 个渠道）已具备：

- **逐渠道失败隔离**：`send_with_results()` 对每个渠道独立 try/except，单渠道失败不拖垮整体。
- **路由**：按 `route_type`（report / alert / event / system_error）过滤渠道
  （`notification_routing.py` 的 `NOTIFICATION_ROUTE_CONFIGS`）。
- **去重**：广播级别的、进程内、默认关闭。
- **回执持久化 —— 只对告警路径**：`AlertService.repo`（`alert_repo.py`）把每次投递写入
  `alert_notifications` 表（`AlertNotificationRecord`），ID 挂 `trigger_id`。Web `AlertsPage`
  已有「通知」tab 展示 per-channel 状态。

**三个真实缺口（本子项目只补这些）：**

1. **report / 日报 / 大盘 / system_error 路线无持久化回执**：主流程（`main.py`、`core/pipeline.py`、
   `market_review.py`、`system_alert.py`）调用 `notifier.send()/send_with_results()` 后 **丢弃结果**，
   `channel_results` 未被落库。运营者无法追溯"今天的日报是否真的送达了 Telegram / 邮件 / 飞书，
   哪个渠道失败、原因是什么、耗时多少"。
2. **Web 无投递状态视图**：告警有「通知」tab，但 report / system_error 的投递状态在 UI 完全缺失。
3. **`docs/notifications.md` 与实现漂移**：文档 P7 声称"无跨进程持久化/无重试"，但现实是
   `AlertWorker` 已持久化 `alert_notifications`、部分 sender（Discord/Telegram/Feishu）已内部重试。

## 2. 变更范围

> 只补上述缺口，**不重建**现有发送、隔离、去重、告警回执。告警路径（`alert`/`event`）已有
> `AlertService.repo` + `alert_notifications` 承载，**保持不动**；新回执只覆盖**当前零持久化**的
> 通用路线（report / system_error / 遗产 route_type=None）。

### 变更 1：通用投递回执表 + 仓储（后端数据层）

- **表 `notification_deliveries`**：新增 `NotificationDeliveryRecord`（`src/storage.py`），
  列与 `AlertNotificationRecord` 对齐但**无 `trigger_id`**、**新增 `route_type`**：
  - `id`，`route_type: String(32), index`，`channel: String(32), index`，
    `attempt: Integer, default 1`，`success: Boolean, default False, index`，
    `error_code: String(64)`，`retryable: Boolean, default False`，
    `latency_ms: Integer`，`diagnostics: Text`，`created_at: DateTime, default now, index`。
  - 复合索引 `(route_type, channel, created_at)`。
- **仓储 `NotificationDeliveryRepository`**（`src/repositories/notification_delivery_repo.py`），
  镜像 `AlertRepository`：
  - `record_delivery(fields: Dict[str, Any]) -> NotificationDeliveryRecord`（插入+commit+refresh）。
  - `list_deliveries(*, route_type=None, channel=None, success=None, page=1, page_size=20)
    -> Tuple[List[NotificationDeliveryRecord], int]`（条件筛选，按 `created_at desc, id desc` 排序，
    分页）。

### 变更 2：`send_with_results` 落库钩子（写路径）

在 `NotificationService.send_with_results()` **返回前**追加一次通用回执记录：

- **触发集合 `_GENERAL_RECEIPT_ROUTES = {None, "report", "system_error"}`**：仅当
  `route_type in _GENERAL_RECEIPT_ROUTES` 时落库。`alert`/`event` 归 `AlertWorker`，跳过，避免双写。
- **门控**：config `notification_delivery_receipts_enabled`（默认 `True`）。为 `False` 时跳过（无副作用）。
- **记录逻辑（镜像 `AlertWorker._record_notification_attempts` + `_synthetic_attempt_for_dispatch`）**：
  - 遍历 `dispatch.channel_results`，逐条以 `{"route_type": str(route_type or "default"), "channel": str(channel)[:32], "attempt": 序号, "success", "error_code", "retryable", "latency_ms", "diagnostics"}` 落库。
  - 若 `channel_results` 无**真实**渠道条目（仅 `__context__`/`__dispatch__`/`__noise_suppressed__`/
    `__no_channel__` 或为空），写**一条合成行**：`channel` 取自 `_synthetic_attempt_for_dispatch` 的
    channel-by-status 映射，`success`/`error_code`/`retryable`/`diagnostics` 按 `dispatch.status`/`message` 填充。
- **健壮性**：整个记录过程外包 try/except + warning，**绝不让回执失败反向变成"汇报失败"**；
  repo 惰性构造（`NotificationDeliveryRepository()`），构造失败同样降级为 warning。

> 这样所有主流程发送（report 日报/大盘、system_error、遗产 None）无需改动 main/pipeline/
> market_review/system_alert 调用方，即可自动获得可追溯回执。

### 变更 3：投递回执 API（后端展示）

- **`api/v1/schemas/notifications.py`（新）**：`NotificationDeliveryItem`（id, routeType, channel,
  attempt, success, errorCode, retryable, latencyMs, diagnostics, createdAt，snake→camel 序列化）、
  `NotificationDeliveryListResponse`（items, total, page, pageSize）。
- **`api/v1/endpoints/notification_deliveries.py`（新）**：`GET /notifications/deliveries`，参数
  `route_type? / channel? / success? / page / page_size`，沿用 `admin_session_cookie` 认证 + error
  `_internal_error` 惯例，镜像 `alerts.list_notifications`（读 `NotificationDeliveryRepository().list_deliveries(...)`）。
- **挂载**：`api/v1/router.py` 新增 `router.include_router(notification_deliveries.router, prefix="/notifications", tags=["Notifications"])`。

### 变更 4：Web 投递状态视图（前端落地）

在 `apps/dsa-web` **设置页**新增「通知投递」区块，列出最近投递回执（可联动后端筛选）：

- `src/types/notifications.ts`（新）：`NotificationDeliveryItem`、`NotificationDeliveryListResponse`
  （与后端 camelCase 对齐）。
- `src/api/notifications.ts`（新）：`getNotificationDeliveries(query)` 调用
  `GET /api/v1/notifications/deliveries`，含 snake→camel 归一 mapper。
- `src/pages/SettingsPage.tsx`：新增「通知投递」区块，镜像 `AlertsPage` 通知 tab 渲染
  （route_type / channel / success 过滤 + 分页），展示 per-channel 的成功/失败/耗时/错误码；
  提供「刷新」。
- `src/i18n/uiText.ts`：新增 zh + en 文案 key（含 route_type 中文映射、渠道名映射复用
  `formatNotificationChannel` 逻辑）。

### 变更 5：文档与配置校对

- `src/config.py`：新增 `notification_delivery_receipts_enabled: bool = True`（字段块 `markdown_to_image_channels`
  附近）+ env parse `NOTIFICATION_DELIVERY_RECEIPTS_ENABLED`（`parse_env_bool` 风格）。
- `.env.example`：新增 `# NOTIFICATION_DELIVERY_RECEIPTS_ENABLED=true`。
- `docs/notifications.md`：校正 P7 关于"无持久化/无重试"的表述，注明告警回执已持久化到
  `alert_notifications`、report/system_error 回执持久化到 `notification_deliveries`、部分 sender
  内部重试；补充新配置项说明。
- `docs/CHANGELOG.md` `[Unreleased]`：追加**扁平**条目：
  - `- [新功能] 通知投递回执：全路径持久化每次渠道投递状态与耗时并可在设置页追溯`
  - `- [新功能] 通知投递列表 API（/api/v1/notifications/deliveries）与设置页投递视图`

## 3. 配置项（新增）

| 字段 | 类型 | 默认 | env | 说明 |
| --- | --- | --- | --- | --- |
| `notification_delivery_receipts_enabled` | `bool` | `True` | `NOTIFICATION_DELIVERY_RECEIPTS_ENABLED` | 是否对 report/system_error/遗产路线持久化投递回执 |

## 4. 验证矩阵

- **Python 后端**：`./scripts/ci_gate.sh`；改动文件 `python -m py_compile`。
  新增测试：
  - `tests/test_notification_delivery_repo.py`：`record_delivery` 落库、`list_deliveries` 条件筛选 + 分页排序。
  - `tests/test_notification_delivery_receipts.py`：`send_with_results` 对 report/system_error/None 落库、
    alert/event 不落库、`notification_delivery_receipts_enabled=False` 不落库、repo 异常不破坏发送返回值。
  - `tests/test_notification_deliveries_api.py`：`GET /notifications/deliveries` 正常返回 + 筛选 + 鉴权。
- **Web**：`cd apps/dsa-web && npm ci && npm run lint && npm run build`。
- **文档/治理**：无 `AGENTS.md`/`.claude/skills` 改动，不触发 `check_ai_assets.py`。

## 5. 风险与回滚

- **风险**：回执落库在发送热路径上，若 DB 慢/挂会拖慢发送 → 用 try/except + 惰性 repo 构造 + warning
  降级，**回执失败绝不改变发送成功/失败语义**（与 `AlertWorker._record_notification_attempts_safely` 同款护栏）。
- **风险**：新表与 `alert_notifications` 语义有重叠隐患 → 明确**只覆盖非告警路线**，告警归 `AlertService.repo`，
  无双写、无平行功能（非告警路径当前零持久化，属真实补缺）。
- **回滚**：改回均为新增方法/表/端点/区块，独立可回退；将 `notification_delivery_receipts_enabled` 置
  `false` 即停用通用回执（既有告警回执、发送主流程不受影响）。

## 6. 不在本子项目范围（明确不做）

- 不做编排层重试/去重（避免与 Discord/Telegram/Feishu 内部重试双重发送；用户确认此步裁剪）。
- 不改 `AlertService.repo` / `alert_notifications` / `/alerts/notifications` API（告警已有完整回执）。
- 不新增逐用户/逐渠道订阅开关、不退订机制（超出"可追溯+可靠性"边界）。
- 不新增 `route_type`（沿用现有 report/alert/event/system_error）。
