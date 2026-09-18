# 通知投递回执（可追溯 + Web 落地）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前零持久化的通用通知路线（report / system_error / 遗产 None）增加每渠道投递回执持久化，并提供 API + Web 设置页投递视图，让运营者可追溯"日报/大盘/系统错误是否送达、哪个渠道失败、原因与耗时"。

**Architecture:** 新增通用 `notification_deliveries` 表 + `NotificationDeliveryRepository`；在 `NotificationService.send_with_results()` 返回前对 `route_type ∈ {None, "report", "system_error"}` 落库（告警 `alert`/`event` 归 `AlertService.repo`，不动），全程 try/except 降级绝不改变发送语义；新增 `GET /api/v1/notifications/deliveries` 端点；Web 设置页新增「通知投递」区块。

**Tech Stack:** Python / SQLAlchemy / FastAPI；TypeScript / React（Vite）。

**Spec:** `docs/superpowers/specs/2026-09-05-notification-delivery-receipts-design.md`

## Global Constraints

- 不新建平行功能：告警回执（`alert_notifications` + `AlertService.repo`）保持不动，新回执只覆盖非告警路线。
- 回执落库失败**绝不**反向改变发送成功/失败语义（与 `AlertWorker._record_notification_attempts_safely` 同款护栏：try/except + warning + 惰性 repo 构造）。
- 发送主流程调用方（main.py / pipeline / market_review / system_alert / agent）一律不改——回执在 `send_with_results` 内部自动落库。
- 不添加 `route_type`（沿用现有 report/alert/event/system_error）。
- 不提交 git：AGENTS.md 禁止未经确认的 commit；实现者只写代码+测试并跑测试，不 commit；提交由协调者在末尾批量确认。
- 新增配置项同步 `.env.example` 与文档；`docs/CHANGELOG.md` 的 `[Unreleased]` 用**扁平**条目（无 `###` 子标题）。
- 提交信息为英文、无 `Co-Authored-By`。

---

### Task 1: 投递回执数据层（storage record + repository）

**Files:**
- Modify: `src/storage.py`（在 `AlertNotificationRecord` 之后新增 `NotificationDeliveryRecord`）
- Create: `src/repositories/notification_delivery_repo.py`
- Test: `tests/test_notification_delivery_repo.py`

**Interfaces:**
- Produces: `NotificationDeliveryRepository(db_manager: Optional[DatabaseManager] = None)` 暴露
  `record_delivery(fields: Dict[str, Any]) -> NotificationDeliveryRecord` 与
  `list_deliveries(*, route_type=None, channel=None, success=None, page=1, page_size=20) -> Tuple[List[NotificationDeliveryRecord], int]`。
  供 Task 3（写钩子）与 Task 4（API）消费。字段名沿用 `AlertNotificationRecord` 列名 + `route_type`。

- [ ] **Step 1: 在 storage.py 新增记录类**

在 `src/storage.py` 的 `AlertNotificationRecord`（:975）之后插入：

```python
class NotificationDeliveryRecord(Base):
    """Generic notification delivery attempt row (non-alert routes).

    Covers report / system_error / legacy route_type=None sends, which today
    have NO persisted receipt. Alert routes (alert/event) remain in
    ``alert_notifications`` owned by ``AlertService.repo``.
    """

    __tablename__ = 'notification_deliveries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_type = Column(String(32), nullable=False, default='default', index=True)
    channel = Column(String(32), nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    success = Column(Boolean, nullable=False, default=False, index=True)
    error_code = Column(String(64))
    retryable = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer)
    diagnostics = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_notification_delivery_route_channel_time', 'route_type', 'channel', 'created_at'),
    )
```

- [ ] **Step 2: 新增 repository 文件**

创建 `src/repositories/notification_delivery_repo.py`：

```python
# -*- coding: utf-8 -*-
"""Notification delivery repository.

DB access for generic (non-alert) notification delivery receipts
(``notification_deliveries``). Mirrors ``AlertRepository``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, NotificationDeliveryRecord


class NotificationDeliveryRepository:
    """DB access layer for generic notification delivery receipts."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def record_delivery(self, fields: Dict[str, Any]) -> NotificationDeliveryRecord:
        with self.db.get_session() as session:
            row = NotificationDeliveryRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_deliveries(
        self,
        *,
        route_type: Optional[str] = None,
        channel: Optional[str] = None,
        success: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[NotificationDeliveryRecord], int]:
        conditions = []
        if route_type:
            conditions.append(NotificationDeliveryRecord.route_type == route_type)
        if channel:
            conditions.append(NotificationDeliveryRecord.channel == channel)
        if success is not None:
            conditions.append(NotificationDeliveryRecord.success.is_(success))

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(NotificationDeliveryRecord.id))
                .select_from(NotificationDeliveryRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(NotificationDeliveryRecord)
                .where(where_clause)
                .order_by(
                    desc(NotificationDeliveryRecord.created_at),
                    desc(NotificationDeliveryRecord.id),
                )
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)
```

- [ ] **Step 3: 写测试**

创建 `tests/test_notification_delivery_repo.py`（前置参考 `tests/test_alert_repository.py` 的 fixture 风格，使用内存/测试 DB）：

```python
import pytest

from src.repositories.notification_delivery_repo import NotificationDeliveryRepository


@pytest.fixture
def repo():
    return NotificationDeliveryRepository()


def test_record_delivery(repo):
    fields = {
        "route_type": "report",
        "channel": "telegram",
        "attempt": 1,
        "success": True,
        "error_code": None,
        "retryable": False,
        "latency_ms": 120,
        "diagnostics": "ok",
    }
    row = repo.record_delivery(fields)
    assert row.id is not None
    assert row.route_type == "report"
    assert row.channel == "telegram"
    assert row.success is True
    assert row.attempt == 1


def test_list_deliveries_filters_and_pages(repo):
    repo.record_delivery({"route_type": "report", "channel": "telegram", "success": True})
    repo.record_delivery({"route_type": "report", "channel": "email", "success": False})
    repo.record_delivery({"route_type": "system_error", "channel": "telegram", "success": True})

    rows, total = repo.list_deliveries(route_type="report")
    assert total == 2
    assert len(rows) == 2

    rows, total = repo.list_deliveries(route_type="report", channel="telegram")
    assert total == 1
    assert rows[0].channel == "telegram"

    rows, total = repo.list_deliveries(success=False)
    assert total == 1
    assert rows[0].success is False

    rows, total = repo.list_deliveries(page=1, page_size=1)
    assert total == 3
    assert len(rows) == 1
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_notification_delivery_repo.py -v`
Expected: PASS（3 tests）。若 repo 依赖真实 DB，可改用 `conftest` 提供的测试 DB manager（参考 `test_alert_repository.py`）。

- [ ] **Step 5: 报告完成**（不 commit）

---

### Task 2: config 字段 + env + .env.example

**Files:**
- Modify: `src/config.py`（新增字段 + env parse）
- Modify: `.env.example`（新增注释 key）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

**Interfaces:**
- Consumes: Task 1 的 `NotificationDeliveryRepository`（本任务只加配置门控，供 Task 3 读取）。
- Produces: `config.notification_delivery_receipts_enabled: bool`（默认 `True`）。

- [ ] **Step 1: config.py 新增字段**

在 `src/config.py` 的 `markdown_to_image_channels`（:1153）字段块附近新增：

```python
    notification_delivery_receipts_enabled: bool = True
```

- [ ] **Step 2: config.py env parse**

在 `src/config.py` 对应 env parse 区域（`markdown_to_image_channels` parse 附近，参考
`parse_env_bool` 风格，见 :2196 `SIGNAL_OUTCOME_AUTO_EVAL_ENABLED`）新增：

```python
            notification_delivery_receipts_enabled=parse_env_bool(
                os.getenv('NOTIFICATION_DELIVERY_RECEIPTS_ENABLED', 'true'), default=True
            ),
```

- [ ] **Step 3: .env.example**

追加一行（与 `markdown_to_image_channels` 注释语义区一致）：

```
# NOTIFICATION_DELIVERY_RECEIPTS_ENABLED=true
```

- [ ] **Step 4: CHANGELOG 扁平条目**

`docs/CHANGELOG.md` 的 `[Unreleased]` 追加两行（保持扁平，无 `###`）：

```
- [新功能] 通知投递回执：全路径持久化每次渠道投递状态与耗时并可在设置页追溯
- [新功能] 通知投递列表 API（/api/v1/notifications/deliveries）与设置页投递视图
```

- [ ] **Step 5: 验证**

Run: `uv run python -c "from src.config import Config, get_config; print(getattr(get_config(), 'notification_delivery_receipts_enabled', 'MISSING'))"`
Expected: `True`。另确认 `SIGNAL_OUTCOME_AUTO_EVAL_ENABLED` 同级风格正确。

- [ ] **Step 6: 报告完成**（不 commit）

---

### Task 3: `send_with_results` 回执落库钩子

**Files:**
- Modify: `src/notification.py`
- Test: `tests/test_notification_delivery_receipts.py`

**Interfaces:**
- Consumes: Task 1 的 `NotificationDeliveryRepository`、Task 2 的
  `config.notification_delivery_receipts_enabled`；`NotificationService.send_with_results()` 返回
  `NotificationDispatchResult`（含 `.status/.success/.channel_results/.message`），`ChannelAttemptResult` 含
  `.channel/.success/.error_code/.retryable/.latency_ms/.diagnostics`。
- Produces: `NotificationService._record_general_delivery_receipts(...)`（供发送路径内部调用 + 测试）。

- [ ] **Step 1: 新增记录方法**

在 `src/notification.py` 的 `send_with_results` 方法（结尾 `return NotificationDispatchResult(...)` 之前）
注入一次记录，并新增两个方法（放在 `send_with_results` 之后、`send` 之前）：

```python
    # Non-alert routes that get a generic delivery receipt. Alert routes
    # ('alert'/'event') are owned by AlertWorker -> alert_notifications.
    _GENERAL_RECEIPT_ROUTES = frozenset({None, "report", "system_error"})

    def _record_general_delivery_receipts(
        self,
        route_type: Optional[str],
        dispatch: "NotificationDispatchResult",
    ) -> None:
        """Persist a delivery receipt for generic (non-alert) send routes.

        Never raises and never alters dispatch semantics: a receipt-write
        failure only logs a warning. Alert routes are skipped so the sole
        writer stays ``AlertWorker`` -> ``alert_notifications``.
        """
        if route_type not in self._GENERAL_RECEIPT_ROUTES:
            return
        if not getattr(self._config, "notification_delivery_receipts_enabled", True):
            return
        try:
            from src.repositories.notification_delivery_repo import (
                NotificationDeliveryRepository,
            )
            repo = NotificationDeliveryRepository()
        except Exception as exc:
            logger.warning(
                "通知投递回执仓储初始化失败，跳过回执记录：%s",
                self._sanitize_text(str(exc) or "notification delivery repo init failed"),
            )
            return

        route = str(route_type or "default")[:32]
        channel_results = list(dispatch.channel_results or [])
        if not channel_results or not any(
            str(item.channel or "").startswith("__") is False for item in channel_results
        ):
            channel_results = [self._synthetic_delivery_attempt(dispatch)]

        try:
            for attempt_index, item in enumerate(channel_results, start=1):
                repo.record_delivery(
                    {
                        "route_type": route,
                        "channel": str(item.channel or "__dispatch__")[:32],
                        "attempt": attempt_index,
                        "success": bool(item.success),
                        "error_code": item.error_code,
                        "retryable": bool(item.retryable),
                        "latency_ms": self._optional_int(item.latency_ms),
                        "diagnostics": self._sanitize_text(
                            item.diagnostics or dispatch.message
                        ),
                    }
                )
        except Exception as exc:
            logger.warning(
                "通知投递回执落库失败：%s",
                self._sanitize_text(str(exc) or "notification delivery record write failed"),
            )

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        return int(value) if value is not None else None

    @staticmethod
    def _synthetic_delivery_attempt(dispatch: "NotificationDispatchResult") -> "ChannelAttemptResult":
        status = str(dispatch.status or "unknown")
        channel_by_status = {
            "noise_suppressed": "__noise_suppressed__",
            "no_channel": "__no_channel__",
            "exception": "__dispatch__",
        }
        success = bool(dispatch.success)
        return ChannelAttemptResult(
            channel=channel_by_status.get(status, "__dispatch__"),
            success=success,
            error_code=None if success else status,
            retryable=status not in {"noise_suppressed", "no_channel"},
            diagnostics=dispatch.message,
        )
```

> 说明：`_GENERAL_RECEIPT_ROUTES` 约束了触发集合；`channel_results` 判定"是否无真实渠道"用
> `startswith("__") is False`（镜像 `AlertWorker._dispatch_has_real_channel_success` 语义，但把
> `__context__`（真实消息会话）视为非真实静态渠道，仅当存在静态渠道才逐条记录，否则合成一条）。

- [ ] **Step 2: 在 `send_with_results` 末尾注入调用**

在 `send_with_results` 的 `return NotificationDispatchResult(...)` 之前，组装到局部变量再返回：

把当前结尾：

```python
        if context_success:
            channel_results.insert(0, ChannelAttemptResult(channel="__context__", success=True))
        return NotificationDispatchResult(
            dispatched=True,
            success=success,
            status=status,
            channel_results=channel_results,
        )
```

替换为：

```python
        if context_success:
            channel_results.insert(0, ChannelAttemptResult(channel="__context__", success=True))
        result = NotificationDispatchResult(
            dispatched=True,
            success=success,
            status=status,
            channel_results=channel_results,
        )
        self._record_general_delivery_receipts(route_type, result)
        return result
```

（注意：其他早退路径——`noise_suppressed`、`no_channel`、`all_failed`、上下文跳过——也应调用
`self._record_general_delivery_receipts(route_type, <early result>)` 前记录，以保证这些状态也可追溯。
将早退处的构造改为「构建 result → 调记录 → return」。逐处替换，保持调用顺序为「构建后再记录、记录后再返回」。）

- [ ] **Step 3: 写测试**

创建 `tests/test_notification_delivery_receipts.py`，mock repo 与 config，验证：

```python
import pytest

from src.notification import ChannelAttemptResult, NotificationDispatchResult


def _dispatch(**kw):
    defaults = dict(dispatched=True, success=True, status="sent", channel_results=[
        ChannelAttemptResult(channel="telegram", success=True, latency_ms=50),
    ])
    defaults.update(kw)
    return NotificationDispatchResult(**defaults)


@pytest.fixture
def service(monkeypatch):
    from src.notification import NotificationService
    svc = NotificationService.__new__(NotificationService)
    type(svc)._config = type("Cfg", (), {"notification_delivery_receipts_enabled": True, "_sanitize_text": None})()
    svc._config._sanitize_text = lambda s: str(s or "")
    monkeypatch.setattr(
        "src.notification.NotificationService._sanitize_text",
        staticmethod(lambda self, s: str(s or "")),
    )
    return svc
```

补充断言用例（在 `service` fixture 基础上，monkeypatch `_GENERAL_RECEIPT_ROUTES` 相关 repo 调用并记录调用次数）：
- route_type in {None, "report", "system_error"} → 调用 repo.record_delivery。
- route_type in {"alert", "event"} → 不调用 repo。
- `notification_delivery_receipts_enabled=False` → 不调用 repo。
- 无真实渠道（仅 `__context__`）时产出一条合成 attempt。
- repo 构造/写入抛异常时，`_record_general_delivery_receipts` 不抛出且 dispatch 语义不变。

> 具体 mock 方式由实现者按项目测试惯例实现，务必备有上述 5 个行为断言。参考
> `tests/test_alert_worker.py` 对 `_record_notification_attempts_safely` 的 mock 手法。

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_notification_delivery_receipts.py -v`
Expected: PASS。再跑 `uv run python -m pytest tests/test_notification_delivery_repo.py tests/test_notification_delivery_receipts.py -q`。

- [ ] **Step 5: 回归确认**

Run: `uv run python -m pytest -m "not network" -q tests/test_notification_delivery_repo.py tests/test_alert_worker.py -q`
Expected: 通过（确认告警回执未被影响）。

- [ ] **Step 6: 报告完成**（不 commit）

---

### Task 4: 投递回执 API（schema + endpoint + router）

**Files:**
- Create: `api/v1/schemas/notifications.py`
- Create: `api/v1/endpoints/notification_deliveries.py`
- Modify: `api/v1/router.py`
- Test: `tests/test_notification_deliveries_api.py`

**Interfaces:**
- Consumes: Task 1 的 `NotificationDeliveryRepository.list_deliveries`、Task 3 的存储列名。
- Produces: `GET /api/v1/notifications/deliveries`（`admin_session_cookie` 鉴权，返回
  `NotificationDeliveryListResponse`）。供 Task 5 Web 消费。

- [ ] **Step 1: 新增 schema 文件**

创建 `api/v1/schemas/notifications.py`（镜像 `api/v1/schemas/alerts.py:130-150`）：

```python
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class NotificationDeliveryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    route_type: str
    channel: str
    attempt: int
    success: bool
    error_code: Optional[str] = None
    retryable: bool
    latency_ms: Optional[int] = None
    diagnostics: Optional[str] = None
    created_at: datetime


class NotificationDeliveryListResponse(BaseModel):
    items: List[NotificationDeliveryItem]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 2: 新增 endpoint**

创建 `api/v1/endpoints/notification_deliveries.py`，**镜像 `api/v1/endpoints/alerts.py` 的认证与错误映射惯例**
（告警通知端点 `list_notifications` 不设 router 级 `admin_session_cookie`，认证继承全局
`api/middlewares/auth.py` 中间件、由 `is_auth_enabled()` 门控；本端点作为其直接同类保持一致，不加重置依赖，
`router = APIRouter()`，无 `Security(admin_session_cookie)`）：

```python
# -*- coding: utf-8 -*-
"""Notification delivery receipts API (generic / non-alert routes)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.errors import api_error
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.notifications import (
    NotificationDeliveryItem,
    NotificationDeliveryListResponse,
)
from src.repositories.notification_delivery_repo import NotificationDeliveryRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return api_error(500, "internal_error", message)


@router.get(
    "/deliveries",
    response_model=NotificationDeliveryListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List notification delivery receipts",
)
def list_deliveries(
    route_type: Optional[str] = Query(None, description="Optional route type filter"),
    channel: Optional[str] = Query(None, description="Optional channel filter"),
    success: Optional[bool] = Query(None, description="Optional success filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> NotificationDeliveryListResponse:
    try:
        rows, total = NotificationDeliveryRepository().list_deliveries(
            route_type=route_type,
            channel=channel,
            success=success,
            page=page,
            page_size=page_size,
        )
        return NotificationDeliveryListResponse(
            items=[NotificationDeliveryItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List notification deliveries failed", exc)
```

> 认证：本端点与 `alerts.py:list_notifications` 一致，无 router 级 `Security(admin_session_cookie)`；
> 全局鉴权由 `api/middlewares/auth.py` + `is_auth_enabled()` 承担。请勿自行添加 `Depends`/`Security` 依赖
> （会与 alerts 通知端点行为不一致）。

- [ ] **Step 3: 注册路由**

`api/v1/router.py` 顶部 import 区（`from api.v1.endpoints import (...)`) 加入 `notification_deliveries`，
并在文件末尾追加：

```python
router.include_router(
    notification_deliveries.router,
    prefix="/notifications",
    tags=["Notifications"]
)
```

- [ ] **Step 4: 写测试**

创建 `tests/test_notification_deliveries_api.py`（镜像 `tests/test_alert_notifications_api.py`，使用 conftest 的
`client_and_db` fixture —— 参考 `tests/test_decision_signal_outcome_api.py:63` 的 `yield client, db`）：

```python
def test_list_deliveries_returns_items(client_and_db):
    client, db = client_and_db
    # 预置一条回执
    from src.repositories.notification_delivery_repo import NotificationDeliveryRepository
    NotificationDeliveryRepository(db).record_delivery({
        "route_type": "report", "channel": "telegram", "success": True, "latency_ms": 80,
    })
    resp = client.get("/api/v1/notifications/deliveries")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["routeType"] == "report"  # snake->camel 由前端 mapper 负责，此处为原始 snake
```

> 关键探索点：确认项目响应是否把 snake 字段直接返回（后端 `model_validate` 用 snake 字段名，前端再
> snake→camel），据此调整断言；若后端本身输出 camel，按实际为准。

- [ ] **Step 5: 运行测试**

Run: `uv run python -m pytest tests/test_notification_deliveries_api.py -v`
Expected: PASS。再 `uv run python -m pytest -m "not network" -q tests/test_notification_deliveries_api.py tests/test_alert_notifications_api.py -q`（确认不影响既有 alerts API）。

- [ ] **Step 6: 报告完成**（不 commit）

---

### Task 5: Web 投递视图（types + api + 设置页区块 + i18n）

**Files:**
- Create: `apps/dsa-web/src/types/notifications.ts`
- Create: `apps/dsa-web/src/api/notifications.ts`
- Modify: `apps/dsa-web/src/pages/SettingsPage.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`

**Interfaces:**
- Consumes: Task 4 的 `GET /api/v1/notifications/deliveries`（返回 items/total/page/pageSize）。
- Produces: `notificationsApi.getNotificationDeliveries(query)`；`Text` 字段渲染。

- [ ] **Step 1: 新增类型**

创建 `apps/dsa-web/src/types/notifications.ts`：

```typescript
export interface NotificationDeliveryItem {
  id: number;
  routeType: string;
  channel: string;
  attempt: number;
  success: boolean;
  errorCode: string | null;
  retryable: boolean;
  latencyMs: number | null;
  diagnostics: string | null;
  createdAt: string;
}

export interface NotificationDeliveryListResponse {
  items: NotificationDeliveryItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface NotificationDeliveryListQuery {
  routeType?: string;
  channel?: string;
  success?: boolean;
  page?: number;
  pageSize?: number;
}
```

- [ ] **Step 2: 新增 api client**

创建 `apps/dsa-web/src/api/notifications.ts`（镜像 `api/alerts.ts` 的 listNotifications + mapper：

```typescript
import { apiClient } from './client';
import { toCamelCase } from './mappers'; // 复用项目既有 snake->camel 工具
import type { NotificationDeliveryListQuery, NotificationDeliveryListResponse } from '../types/notifications';

function mapDeliveryItem(raw: Record<string, unknown>): NotificationDeliveryListResponse['items'][number] {
  return {
    id: Number(raw.id),
    routeType: String(raw.route_type ?? raw.routeType ?? 'default'),
    channel: String(raw.channel ?? ''),
    attempt: Number(raw.attempt ?? 1),
    success: Boolean(raw.success),
    errorCode: raw.error_code ?? raw.errorCode ?? null,
    retryable: Boolean(raw.retryable),
    latencyMs: raw.latency_ms ?? raw.latencyMs ?? null,
    diagnostics: raw.diagnostics ?? null,
    createdAt: String(raw.created_at ?? raw.createdAt ?? ''),
  };
}

export const notificationsApi = {
  async getNotificationDeliveries(query: NotificationDeliveryListQuery = {}): Promise<NotificationDeliveryListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/notifications/deliveries', { params: query });
    const body = response.data;
    const items = Array.isArray(body.items) ? body.items.map((it) => mapDeliveryItem(it as Record<string, unknown>)) : [];
    return {
      items,
      total: Number(body.total ?? 0),
      page: Number(body.page ?? 1),
      pageSize: Number(body.pageSize ?? body.page_size ?? 20),
    };
  },
};
```

> 实现者以 `api/alerts.ts` 的实际 import/client 命名（`apiClient`/`toCamelCase` 的真实路径与签名）为准。

- [ ] **Step 3: 设置页新增「通知投递」区块**

`apps/dsa-web/src/pages/SettingsPage.tsx`：新增一个卡片区块（镜像 `AlertsPage` 通知 tab 的渲染：
routeType/channel/success 筛选 + 分页 + 每行 per-channel 状态），调用 `notificationsApi.getNotificationDeliveries`；
复用/仿照 `formatNotificationChannel` 的渠道中文映射与 `formatNotificationStatus` 的成败样式。
提供「刷新」按钮。路由类型中文映射新增（report→日报/报告、system_error→系统错误、default→默认）。

（截图证据：见 task 报告附件说明）

- [ ] **Step 4: i18n**

`apps/dsa-web/src/i18n/uiText.ts`：zh + en 新增 key（参考 alerts 块的 `tabsNotifications` 等），
含路由类型中文、状态文案、筛选标签、分页/刷新。确保 `UiTextKey` 两侧一致。

- [ ] **Step 5: 验证**

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint + build 通过。可顺带 `npm run test -- --run`（若该项目有对应测试）。

- [ ] **Step 6: 报告完成**（不 commit）

---

### Task 6: 文档校对（notifications.md 校正）

**Files:**
- Modify: `docs/notifications.md`

**Interfaces:**
- Consumes: 全部上文变更。

- [ ] **Step 1: 校正 P7 表述**

`docs/notifications.md`：在描述持久化/重试处，明确：
- 告警回执已持久化到 `alert_notifications`（`AlertService.repo`）。
- report/system_error/遗产路线回执持久化到 `notification_deliveries`（`NotificationService.send_with_results`
  内自动落库，`notification_delivery_receipts_enabled` 门控）。
- 若干 sender（Discord/Telegram/Feishu）在 sender 内部重试；编排层不做重试（避免双重发送）。
- 补充 `NOTIFICATION_DELIVERY_RECEIPTS_ENABLED` 配置说明。

- [ ] **Step 2: 一致性核对**

确认 `docs/notifications.md` 中提到的表名、路由类型、配置 key 与实际代码一致（`notification_deliveries`、
`notification_delivery_receipts_enabled`、`/api/v1/notifications/deliveries`）。

- [ ] **Step 3: 报告完成**（不 commit）

---

## 验证总览

- Python：`./scripts/ci_gate.sh`；新增测试
  `tests/test_notification_delivery_repo.py`、`tests/test_notification_delivery_receipts.py`、
  `tests/test_notification_deliveries_api.py`；改动文件 `python -m py_compile`。
- Web：`cd apps/dsa-web && npm ci && npm run lint && npm run build`。
- 文档：`docs/notifications.md`、`docs/CHANGELOG.md`、`.env.example`。
