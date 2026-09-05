# 跨源一致性对账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在选源成功后，用配置里的次选源对同一标的做一次跨源比对，发现价差超阈值 / 交易日错位 / 关键字段缺失时，记录一条数据质量异常并复用 `system_error` 路由去重告警；并提供列表 API + 设置页视图。

**Architecture:** 取数管理器在实时盘与日 K 路径「首个成功源结果返回前」插入一次对账调用。A 股实时盘复用主循环已取的下一个源 quote（零额外网络开销）；美股/港股与日 K 路径需按门控再取一次次选源。对账核心是纯函数（可单测、无 IO），命中后交给记录方法（落库 + 告警），整个过程 try/except 包住、绝不反向影响主取数。

**Tech Stack:** Python（SQLAlchemy + FastAPI）、React/TypeScript（Vite）、pytest。

**Spec:** `docs/superpowers/specs/2026-09-05-data-quality-cross-source-reconciliation-design.md`（本计划论证自该 spec；执行者需同时读 spec 与 plan）。

## Global Constraints

- 后端逻辑在 `data_provider/`、`src/`、`api/`；前端改动只在 `apps/dsa-web/`。
- 未经明确确认，不执行 `git commit` / `git push`。commit message 全英文，不加 `Co-Authored-By`。
- 对账是「观测 + 告警」，**不得**自动修正价格、不得自动改选源优先级、不做重取编排。
- 对账整体 try/except + warning 降级；次选源取数失败（None）静默跳过，绝不抛错、绝不反向损坏主取数结果、绝不改变发送/取数成功/失败语义。
- 新配置默认值必须满足「不配置也可运行，配置后增强」；新增配置同步 `.env.example` + 相关文档。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 用扁平格式（`- [类型] 描述`），**禁止**新增 `### 类目标题`。
- 不改任何通知 send 调用方；告警复用 `src/services/system_alert.py` 的 `send_system_alert`。
- 不要新增另一套平行的「检测」实现——所有对账都走本计划新增的纯函数 + 记录方法。

---

### Task 1: 数据层 — `data_quality_discrepancies` 表 + 仓储

**Files:**
- Modify: `src/storage.py`（在 `NotificationDeliveryRecord` 类之后，`AlertCooldownRecord` 之前，新增 `DataQualityDiscrepancyRecord`）
- Create: `src/repositories/data_quality_discrepancy_repo.py`
- Test: `tests/test_data_quality_discrepancy_repo.py`

**Interfaces:**
- Consumes: `src.storage.DatabaseManager`（`get_instance()` / `get_session()`）、`NotificationDeliveryRecord` 作为镜像模板。
- Produces:
  - `src.storage.DataQualityDiscrepancyRecord`（ORM 表 `data_quality_discrepancies`）。
  - `src.repositories.data_quality_discrepancy_repo.DataQualityDiscrepancyRepository`：
    `record_discrepancy(fields: Dict[str, Any]) -> DataQualityDiscrepancyRecord`；
    `list_discrepancies(*, market=None, stock_code=None, issue_type=None, page=1, page_size=20) -> Tuple[List[DataQualityDiscrepancyRecord], int]`。
  - 列名（Task 3/4/5 依赖）：`id / market / stock_code / issue_type / primary_source / secondary_source / primary_price / secondary_price / price_diff_pct / primary_ts / secondary_ts / detail / created_at`。`issue_type ∈ {price_discrepancy, date_mismatch, field_missing}`。

- [ ] **Step 1: 写失败测试** — `tests/test_data_quality_discrepancy_repo.py`

```python
# tests/test_data_quality_discrepancy_repo.py
# -*- coding: utf-8 -*-
"""Tests for DataQualityDiscrepancyRepository."""
from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository


def _seed(session):
    repo = DataQualityDiscrepancyRepository()
    repo.record_discrepancy({
        "market": "cn",
        "stock_code": "600519.SH",
        "issue_type": "price_discrepancy",
        "primary_source": "tencent",
        "secondary_source": "akshare_sina",
        "primary_price": 1500.0,
        "secondary_price": 1450.0,
        "price_diff_pct": 3.33,
        "primary_ts": "2026-09-05T10:00:00",
        "secondary_ts": "2026-09-05T10:00:00",
        "detail": "price mismatch",
    })
    repo.record_discrepancy({
        "market": "cn",
        "stock_code": "000001.SZ",
        "issue_type": "date_mismatch",
        "primary_source": "tencent",
        "secondary_source": "akshare_sina",
        "primary_price": None,
        "secondary_price": None,
        "price_diff_pct": None,
        "primary_ts": "2026-09-05T09:30:00",
        "secondary_ts": "2026-09-05T09:00:00",
        "detail": "date mismatch",
    })


def test_record_and_list_basic():
    repo = DataQualityDiscrepancyRepository()
    rows, total = repo.list_discrepancies(page=1, page_size=20)
    assert total >= 2
    assert isinstance(rows, list)
    assert rows[0].issue_type in {"price_discrepancy", "date_mismatch"}


def test_list_filters_by_issue_type():
    repo = DataQualityDiscrepancyRepository()
    rows, total = repo.list_discrepancies(issue_type="price_discrepancy", page=1, page_size=20)
    assert all(r.issue_type == "price_discrepancy" for r in rows)
    assert total >= 1


def test_list_filters_by_market_and_stock():
    repo = DataQualityDiscrepancyRepository()
    rows, total = repo.list_discrepancies(market="cn", stock_code="600519.SH", page=1, page_size=20)
    assert all(r.market == "cn" and r.stock_code == "600519.SH" for r in rows)
    assert total >= 1


def test_list_pagination_ordering():
    repo = DataQualityDiscrepancyRepository()
    page1, _ = repo.list_discrepancies(page=1, page_size=1)
    page2, _ = repo.list_discrepancies(page=2, page_size=1)
    # 按 created_at desc, id desc 排序：任意两页不应重复同一行
    assert not (page1 and page2 and page1[0].id == page2[0].id)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_data_quality_discrepancy_repo.py -q`
Expected: FAIL（`ModuleNotFoundError: src.repositories.data_quality_discrepancy_repo` 或表不存在）。

- [ ] **Step 3: 写 ORM 表 — 在 `src/storage.py` 的 `NotificationDeliveryRecord` 结束后插入**

在 `src/storage.py` 约 1023 行（`NotificationDeliveryRecord.__table_args__` 块结束之后、`class AlertCooldownRecord` 之前）插入：

```python
class DataQualityDiscrepancyRecord(Base):
    """Cross-source reconciliation discrepancy row.

    Captures a data-quality anomaly detected when the chosen primary source's
    value disagrees with an alternate source beyond the configured threshold
    (price diff / trade-date mismatch / missing field). Purely observational:
    never mutates the chosen value or the source-priority selection.
    """

    __tablename__ = 'data_quality_discrepancies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(16), nullable=False, index=True)
    stock_code = Column(String(32), nullable=False, index=True)
    issue_type = Column(String(32), nullable=False, index=True)
    primary_source = Column(String(32), default=None)
    secondary_source = Column(String(32), default=None)
    primary_price = Column(Float)
    secondary_price = Column(Float)
    price_diff_pct = Column(Float)
    primary_ts = Column(String(32))
    secondary_ts = Column(String(32))
    detail = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_data_quality_discrepancy_mkt_code_issue_time', 'market', 'stock_code', 'issue_type', 'created_at'),
    )
```

- [ ] **Step 4: 写仓储 — `src/repositories/data_quality_discrepancy_repo.py`**

镜像 `src/repositories/notification_delivery_repo.py`：

```python
# -*- coding: utf-8 -*-
"""Data quality discrepancy repository.

DB access for cross-source reconciliation findings (``data_quality_discrepancies``).
Mirrors ``NotificationDeliveryRepository``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, DataQualityDiscrepancyRecord


class DataQualityDiscrepancyRepository:
    """DB access layer for cross-source data-quality discrepancy rows."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def record_discrepancy(self, fields: Dict[str, Any]) -> DataQualityDiscrepancyRecord:
        with self.db.get_session() as session:
            row = DataQualityDiscrepancyRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_discrepancies(
        self,
        *,
        market: Optional[str] = None,
        stock_code: Optional[str] = None,
        issue_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[DataQualityDiscrepancyRecord], int]:
        conditions = []
        if market:
            conditions.append(DataQualityDiscrepancyRecord.market == market)
        if stock_code:
            conditions.append(DataQualityDiscrepancyRecord.stock_code == stock_code)
        if issue_type:
            conditions.append(DataQualityDiscrepancyRecord.issue_type == issue_type)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DataQualityDiscrepancyRecord.id))
                .select_from(DataQualityDiscrepancyRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(DataQualityDiscrepancyRecord)
                .where(where_clause)
                .order_by(
                    desc(DataQualityDiscrepancyRecord.created_at),
                    desc(DataQualityDiscrepancyRecord.id),
                )
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run python -m pytest tests/test_data_quality_discrepancy_repo.py -q`
Expected: PASS。

- [ ] **Step 6: 语法校验**

Run: `python -m py_compile src/storage.py src/repositories/data_quality_discrepancy_repo.py && uv run python -m pytest tests/test_data_quality_discrepancy_repo.py -q`
Expected: 无错误，测试通过。

---

### Task 2: 配置项 + `.env.example` + CHANGELOG

**Files:**
- Modify: `src/config.py`（新增 3 个字段；在 `bias_threshold` 附近的阈值区 + env parse 函数体）
- Modify: `.env.example`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: 无（独立）。
- Produces（Task 3 依赖）：
  - `data_quality_reconciliation_enabled: bool = True`
  - `data_quality_price_diff_threshold_pct: float = 1.0`
  - `data_quality_date_mismatch_tolerance_seconds: int = 3600`
  - 对应 env：`DATA_QUALITY_RECONCILIATION_ENABLED` / `DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT` / `DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS`。

- [ ] **Step 1: 写失败测试** —（配置项通过 `src.config.get_config()` 读取，可简单断言默认值；先加一个最小测试占位，若仓库已有 config 测试模块则复用其 fixture）

```python
# tests/test_config_data_quality.py
# -*- coding: utf-8 -*-
from src.config import Config


def test_data_quality_defaults():
    cfg = Config()
    assert cfg.data_quality_reconciliation_enabled is True
    assert cfg.data_quality_price_diff_threshold_pct == 1.0
    assert cfg.data_quality_date_mismatch_tolerance_seconds == 3600
```

（若 `Config()` 需要数据库/大环境初始化而仓库里 config 测试走别的入口，按仓库既有约定调整；本测试只是为 Task 3 的取值提供签名约束。）

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_config_data_quality.py -q`
Expected: FAIL（`AttributeError`）。

- [ ] **Step 3: 新增字段 — `src/config.py`**

在 `bias_threshold: float = 5.0` 行（约 996 行）之后加入：

```python
    # === 跨源一致性对账配置 ===
    # 是否在选源成功后用次选源做跨源比对（价差/交易日/字段缺失）；关闭则零调用零比对
    data_quality_reconciliation_enabled: bool = True
    # 两源价差超过该百分比判定为价差异常（%）
    data_quality_price_diff_threshold_pct: float = 1.0
    # 两源行情时间相差超过该秒数判定为错日/时间错位（秒）
    data_quality_date_mismatch_tolerance_seconds: int = 3600
```

- [ ] **Step 4: 新增 env parse — `src/config.py` 的配置构建函数体**

在既有 `data_source_quarantine_threshold` / `data_source_quarantine_recovery_seconds` 的 env parse 附近（约 2193-2196 行）加入：

```python
            data_quality_reconciliation_enabled=parse_env_bool(
                os.getenv('DATA_QUALITY_RECONCILIATION_ENABLED', 'true'), default=True,
            ),
            data_quality_price_diff_threshold_pct=parse_env_float(
                os.getenv('DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT', '1.0'), default=1.0,
            ),
            data_quality_date_mismatch_tolerance_seconds=int(
                os.getenv('DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS', '3600')
            ),
```

（`parse_env_float` / `parse_env_bool` 均已存在于 `src/config.py`。若该字段块实际构造方式与示例略有出入，沿用同文件同风格拼装，保证字段名一致。）

- [ ] **Step 5: 运行确认通过**

Run: `uv run python -m pytest tests/test_config_data_quality.py -q`
Expected: PASS。

- [ ] **Step 6: 更新 `.env.example`**

在 `DATA_SOURCE_QUARANTINE_*` 附近追加 3 行：

```
# 跨源一致性对账：选源成功后用次选源比对价差/交易日/字段缺失，命中记录数据质量异常并按 system_error 路由告警
# DATA_QUALITY_RECONCILIATION_ENABLED=true
# 两源价差超过该百分比判定为价差异常（%），默认 1.0
# DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT=1.0
# 两源行情时间相差超过该秒数判定为错日/时间错位（秒），默认 3600
# DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS=3600
```

- [ ] **Step 7: 更新 `docs/CHANGELOG.md`**

在 `[Unreleased]` 段（现有扁平条目列表）追加**扁平**两行（不加 `###` 标题）：

```
- [新功能] 跨源一致性对账：选源成功后用次选源比对价差/交易日/字段缺失，命中记录数据质量异常并可按 system_error 路由告警
- [新功能] 数据质量异常列表 API（/api/v1/data-quality/discrepancies）与设置页数据质量视图
```

---

### Task 3: 对账核心 — `data_provider/base.py`（纯函数 + 记录方法 + 接入 3 条路径）

**Files:**
- Modify: `data_provider/base.py`（新增两个纯函数 + 一个记录方法 + 接入实时盘/日 K 成功返回点）
- Test: `tests/test_data_quality_reconciliation.py`
- Modify（告警复用）：`src/services/system_alert.py`（不改，只被调用）

**Interfaces:**
- Consumes: `src.storage.DataQualityDiscrepancyRecord`（Task 1）、`data_quality_*` 配置（Task 2）、
  `src.services.system_alert.send_system_alert(content, *, dedup_key, enabled)`、`data_provider.realtime_types.UnifiedRealtimeQuote`。
- Produces（Task 4/5 消费的落库语义）：
  - 纯函数 `detect_cross_source_issue(primary, cross, *, price_threshold_pct, date_tolerance_seconds)`
    → `Optional[Dict[str, Any]]`（无 IO，可单测）。
  - 纯函数 `detect_daily_cross_source_issue(primary_close, primary_date, cross_close, cross_date, *, price_threshold_pct, date_tolerance_seconds)` → `Optional[Dict[str, Any]]`。
  - 方法 `_reconcile_realtime_cross_source(self, primary, cross, *, market, stock_code)`（读配置、调 detector、命中→记录）。
  - 方法 `_reconcile_daily_cross_source(self, df, cross_df, *, market, stock_code, primary_source, secondary_source)`。
  - 方法 `_record_data_quality_discrepancy(self, *, market, stock_code, issue_type, primary_source, secondary_source, primary_price, secondary_price, price_diff_pct, primary_ts, secondary_ts, detail)`（落库 + 告警，全 try/except 包住）。
- 排错语义：所有新方法**不抛异常**；次选源为 `None` 或取数失败时静默跳过；不修改传入的 quote / df。

- [ ] **Step 1: 写失败测试** — `tests/test_data_quality_reconciliation.py`

```python
# -*- coding: utf-8 -*-
"""Tests for cross-source reconciliation pure detectors + record method."""
from unittest import mock

from data_provider.realtime_types import UnifiedRealtimeQuote
from data_provider.base import detect_cross_source_issue, detect_daily_cross_source_issue


def _quote(price, ts):
    return UnifiedRealtimeQuote(
        provider="tencent",
        code="600519",
        name="",
        price=price,
        change_pct=None,
        change_amount=None,
        volume=None,
        amount=None,
        open_price=price,
        high=price,
        low=price,
        pre_close=price,
        pe=None,
        pb=None,
        market_cap=None,
        provider_timestamp=ts,
        fetched_at=None,
        currency=None,
        market=None,
        is_stale=None,
        stale_seconds=None,
    )


def test_detector_ok_no_issue():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(10.05, "2026-09-05T10:00:00")
    assert detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600) is None


def test_detector_price_discrepancy():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")  # 10% 价差 > 1%。
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "price_discrepancy"


def test_detector_date_mismatch():
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(10.0, "2026-09-02T10:00:00")  # 相差数天。
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "date_mismatch"


def test_detector_missing_field():
    q1 = _quote(None, "2026-09-05T10:00:00")  # price 缺失。
    q2 = _quote(10.0, "2026-09-05T10:00:00")
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "field_missing"


def test_detector_negative_price_skips_price_compare():
    q1 = _quote(0.0, "2026-09-05T10:00:00")  # 无价。
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    res = detect_cross_source_issue(q1, q2, price_threshold_pct=1.0, date_tolerance_seconds=3600)
    assert res is not None and res["issue_type"] == "field_missing"


def test_daily_detector_mismatch():
    res = detect_daily_cross_source_issue(
        10.0, "2026-09-05", 10.0, "2026-09-02",
        price_threshold_pct=1.0, date_tolerance_seconds=3600,
    )
    assert res is not None and res["issue_type"] == "date_mismatch"


def test_daily_detector_ok():
    res = detect_daily_cross_source_issue(
        10.0, "2026-09-05", 10.0, "2026-09-05",
        price_threshold_pct=1.0, date_tolerance_seconds=3600,
    )
    assert res is None


def test_record_method_gated_by_config(mocker):
    # 用 base 的 DataProviderManager 实例，构造 quote 调用 _reconcile_realtime_cross_source，
    # mock repo + alert 以验证「门控 + 触发」行为，不联网。
    from data_provider.base import DataProviderManager
    mgr = DataProviderManager._create_default()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    with mock.patch.object(
        mgr, "_update_data_quality_alert", return_value=None
    ):
        mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")
    # 若 _update_data_quality_alert 已封装 repo+alert，则此处断言其被调用；见 Step 4 备注。
    # 若实现为直接在方法内用 repo+alert，则改为 mock send_system_alert 与 repo.record_discrepancy。
```

> 说明：Step 4 为实现提供 `_update_data_quality_alert` 这一集中封装（落库 + 告警都走它），以便测试只需 mock 它一处；若实现者选择在 `_reconcile_*` 方法内直接调 repo + `send_system_alert`，测试需分别 mock 两者（等价覆盖门控与触发语义）。以上任一实现均要求：`data_quality_reconciliation_enabled=False` 时不比对不落库不告警；`_update_data_quality_alert` 抛出异常不影响主流程。

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_data_quality_reconciliation.py -q`
Expected: FAIL（`detect_cross_source_issue` 不存在）。

- [ ] **Step 3: 实现纯函数 + 记录方法（`data_provider/base.py` 追加在 `_merge_quote_fields`（约 2064 行）附近）**

追加顶层纯函数与类方法。实时 quote 用 `UnifiedRealtimeQuote` 字段；日 K 用 `(close, date)` 二元组。

```python
# ---- 跨源一致性对账 ----

def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_cross_source_issue(primary, cross, *, price_threshold_pct, date_tolerance_seconds):
    """Return a discrepancy dict, or None if the two sources agree within tolerance.

    Pure: no IO, no DB, no alerting. ``primary``/``cross`` are UnifiedRealtimeQuote.
    Issue types: price_discrepancy | date_mismatch | field_missing.
    """
    if primary is None or cross is None:
        return None
    p_price = _safe_float(getattr(primary, "price", None))
    c_price = _safe_float(getattr(cross, "price", None))
    p_ts = getattr(primary, "provider_timestamp", None)
    c_ts = getattr(cross, "provider_timestamp", None)

    # field_missing: any of price/provider_timestamp missing on either side
    if p_price is None or c_price is None or p_ts is None or c_ts is None:
        return {
            "issue_type": "field_missing",
            "primary_price": p_price,
            "secondary_price": c_price,
            "price_diff_pct": None,
            "primary_ts": p_ts,
            "secondary_ts": c_ts,
            "detail": "关键字段缺失（price 或 provider_timestamp 至少一侧为空）",
        }

    # date/timestamp mismatch
    try:
        from datetime import datetime
        p_dt = datetime.fromisoformat(str(p_ts))
        c_dt = datetime.fromisoformat(str(c_ts))
        if abs((p_dt - c_dt).total_seconds()) > date_tolerance_seconds:
            return {
                "issue_type": "date_mismatch",
                "primary_price": p_price,
                "secondary_price": c_price,
                "price_diff_pct": None,
                "primary_ts": p_ts,
                "secondary_ts": c_ts,
                "detail": f"两端行情时间错位: {p_ts} vs {c_ts}",
            }
    except (TypeError, ValueError) as exc:
        # 时间戳解析失败，按缺字段对待（保守告警）
        return {
            "issue_type": "field_missing",
            "primary_price": p_price,
            "secondary_price": c_price,
            "price_diff_pct": None,
            "primary_ts": p_ts,
            "secondary_ts": c_ts,
            "detail": f"时间戳无法解析: {exc}",
        }

    # price discrepancy (skip if primary price <= 0 -> cannot ratio)
    if p_price > 0 and c_price > 0:
        diff_pct = abs(p_price - c_price) / p_price * 100.0
        if diff_pct > price_threshold_pct:
            return {
                "issue_type": "price_discrepancy",
                "primary_price": p_price,
                "secondary_price": c_price,
                "price_diff_pct": round(diff_pct, 4),
                "primary_ts": p_ts,
                "secondary_ts": c_ts,
                "detail": f"两端收盘/报价价差 {diff_pct:.2f}% 超过阈值 {price_threshold_pct}%",
            }
    return None


def detect_daily_cross_source_issue(primary_close, primary_date, cross_close, cross_date, *, price_threshold_pct, date_tolerance_seconds):
    """Return a discrepancy dict for daily-close reconciliation, or None.

    ``primary_date``/``cross_date`` are date strings (df.index[-1]); ``primary_close``/``cross_close`` floats.
    """
    p_close = _safe_float(primary_close)
    c_close = _safe_float(cross_close)
    if p_close is None or c_close is None or primary_date is None or cross_date is None:
        return {
            "issue_type": "field_missing",
            "primary_price": p_close,
            "secondary_price": c_close,
            "price_diff_pct": None,
            "primary_ts": str(primary_date),
            "secondary_ts": str(cross_date),
            "detail": "日K关键字段缺失（close 或 交易日至少一侧为空）",
        }
    try:
        from datetime import datetime
        p_dt = datetime.fromisoformat(str(primary_date))
        c_dt = datetime.fromisoformat(str(cross_date))
        if abs((p_dt - c_dt).total_seconds()) > date_tolerance_seconds:
            return {
                "issue_type": "date_mismatch",
                "primary_price": p_close,
                "secondary_price": c_close,
                "price_diff_pct": None,
                "primary_ts": str(primary_date),
                "secondary_ts": str(cross_date),
                "detail": f"日K交易日错位: {primary_date} vs {cross_date}",
            }
    except (TypeError, ValueError) as exc:
        return {
            "issue_type": "field_missing",
            "primary_price": p_close,
            "secondary_price": c_close,
            "price_diff_pct": None,
            "primary_ts": str(primary_date),
            "secondary_ts": str(cross_date),
            "detail": f"日K日期无法解析: {exc}",
        }
    if p_close > 0 and c_close > 0:
        diff_pct = abs(p_close - c_close) / p_close * 100.0
        if diff_pct > price_threshold_pct:
            return {
                "issue_type": "price_discrepancy",
                "primary_price": p_close,
                "secondary_price": c_close,
                "price_diff_pct": round(diff_pct, 4),
                "primary_ts": str(primary_date),
                "secondary_ts": str(cross_date),
                "detail": f"日K收盘价差 {diff_pct:.2f}% 超过阈值 {price_threshold_pct}%",
            }
    return None
```

- [ ] **Step 4: 实现记录方法（放在 `DataProviderManager` 类体内，紧邻 `_merge_quote_fields`）**

```python
    def _update_data_quality_alert(self, *, market, stock_code, issue_type,
                                   primary_source, secondary_source,
                                   primary_price, secondary_price, price_diff_pct,
                                   primary_ts, secondary_ts, detail):
        """Persist + alert a cross-source discrepancy. Never raises.

        Wrapped in try/except + warning: a receipt/alert failure must never
        propagate back to the fetch path or change its success semantics.
        """
        try:
            from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository
            DataQualityDiscrepancyRepository().record_discrepancy({
                "market": str(market)[:16],
                "stock_code": str(stock_code)[:32],
                "issue_type": str(issue_type)[:32],
                "primary_source": str(primary_source)[:32],
                "secondary_source": str(secondary_source)[:32],
                "primary_price": primary_price,
                "secondary_price": secondary_price,
                "price_diff_pct": price_diff_pct,
                "primary_ts": str(primary_ts) if primary_ts is not None else None,
                "secondary_ts": str(secondary_ts) if secondary_ts is not None else None,
                "detail": detail,
            })
        except Exception as exc:  # noqa: BLE001 - must never break the caller
            logger.warning("数据质量对账落库失败: %s", exc)
        try:
            from src.services.system_alert import send_system_alert
            send_system_alert(
                f"跨源数据质量异常 [{issue_type}] {market} {stock_code}: "
                f"{primary_source}({primary_price}) vs {secondary_source}({secondary_price}) {detail}",
                dedup_key=f"data-quality:{market}:{stock_code}:{issue_type}",
                enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("数据质量对账告警失败: %s", exc)

    def _reconcile_realtime_cross_source(self, primary, cross, *, market, stock_code):
        """Reconcile ``primary`` against ``cross`` (realtime quotes). Never raises."""
        try:
            from src.config import get_config
            cfg = get_config()
            if not getattr(cfg, "data_quality_reconciliation_enabled", True):
                return
            if cross is None:
                return
            issue = detect_cross_source_issue(
                primary, cross,
                price_threshold_pct=getattr(cfg, "data_quality_price_diff_threshold_pct", 1.0),
                date_tolerance_seconds=getattr(cfg, "data_quality_date_mismatch_tolerance_seconds", 3600),
            )
            if issue is None:
                return
            self._update_data_quality_alert(
                market=market, stock_code=stock_code,
                issue_type=issue["issue_type"],
                primary_source=getattr(primary, "provider", None) or "primary",
                secondary_source=getattr(cross, "provider", None) or "cross",
                primary_price=issue["primary_price"], secondary_price=issue["secondary_price"],
                price_diff_pct=issue["price_diff_pct"],
                primary_ts=issue["primary_ts"], secondary_ts=issue["secondary_ts"],
                detail=issue["detail"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("数据质量实时对账异常: %s", exc, exc_info=True)

    def _reconcile_daily_cross_source(self, df, cross_df, *, market, stock_code, primary_source, secondary_source):
        """Reconcile daily-close ``df`` against ``cross_df``. Never raises."""
        try:
            from src.config import get_config
            cfg = get_config()
            if not getattr(cfg, "data_quality_reconciliation_enabled", True):
                return
            if df is None or cross_df is None or df.empty or cross_df.empty:
                return
            primary_close = df.iloc[-1].get("close")
            primary_date = df.index[-1]
            cross_close = cross_df.iloc[-1].get("close")
            cross_date = cross_df.index[-1]
            issue = detect_daily_cross_source_issue(
                primary_close, primary_date, cross_close, cross_date,
                price_threshold_pct=getattr(cfg, "data_quality_price_diff_threshold_pct", 1.0),
                date_tolerance_seconds=getattr(cfg, "data_quality_date_mismatch_tolerance_seconds", 3600),
            )
            if issue is None:
                return
            self._update_data_quality_alert(
                market=market, stock_code=stock_code,
                issue_type=issue["issue_type"],
                primary_source=primary_source, secondary_source=secondary_source,
                primary_price=issue["primary_price"], secondary_price=issue["secondary_price"],
                price_diff_pct=issue["price_diff_pct"],
                primary_ts=issue["primary_ts"], secondary_ts=issue["secondary_ts"],
                detail=issue["detail"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("数据质量日K对账异常: %s", exc, exc_info=True)
```

- [ ] **Step 5: 接入 A 股实时盘（`base.py` A 股主循环 `else` 分支，约 1984 行）**

在 `base.py` 的 A 股循环里，`primary_quote is not None` 的 `else` 分支中，在 `if not self._quote_needs_supplement(primary_quote): break` 之前追加：

```python
                        # 跨源一致性对账：quote 已是下一源的成功值，零额外网络开销
                        if primary_quote is not None and quote is not None:
                            self._reconcile_realtime_cross_source(
                                primary_quote, quote, market="cn", stock_code=stock_code,
                            )
```

- [ ] **Step 6: 接入美股/港股实时盘（`base.py` 双源路由，约 1866 行）**

在 `base.py` 的 `if primary_quote is not None: return self._enrich_realtime_quote(...)` 之前追加（`secondary_src` 已在 `:-1847` 解析）：

```python
            # 跨源一致性对账：用次选源再取一次做交叉验校（受 data_quality_reconciliation_enabled 门控）
            if primary_quote is not None:
                cross = self._try_fetcher_quote(stock_code, secondary_src, **secondary_kw)
                self._reconcile_realtime_cross_source(
                    primary_quote, cross, market=("us" if is_us else "hk"), stock_code=stock_code,
                )
```

- [ ] **Step 7: 接入日 K 路径（`base.py` 成功返回点，约 1419 与 1499）**

在 `get_daily_data` 的两个成功分支 `return df, fetcher.name` **之前**追加（`fallback_to` 已在循环内解析；`market` 已在 scope）：

```python
                        # 跨源一致性对账：用 fallback_to 再取一次日K做交叉验校（受门控），失败静默跳过
                        if fallback_to and getattr(config, "data_quality_reconciliation_enabled", True):
                            try:
                                cross_df = self._call_fetcher_method(
                                    self._get_fetcher_by_name(fallback_to, capability="daily_data"),
                                    "get_daily_data",
                                    stock_code=stock_code, start_date=start_date, end_date=end_date, days=days,
                                )
                                self._reconcile_daily_cross_source(
                                    df, cross_df, market=market, stock_code=stock_code,
                                    primary_source=fetcher.name, secondary_source=fallback_to,
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("数据质量日K对账取数异常: %s", exc)
```

> 注意：上面两处成功分支（美股与通用循环）结构相同，需在**两处**都插入。其中美股分支的 `market`（行情标签）若为可读中文（如 `market_label`），请传入小写 token（`market` 变量）。若 `config` 未在日 K 函数内直接可得，用 `get_config()` 局部获取。

- [ ] **Step 8: 运行配置为 False 的对照测试**

在 `tests/test_data_quality_reconciliation.py` 追加：

```python
def test_gate_off_does_nothing(mocker):
    from data_provider.base import DataProviderManager, detect_cross_source_issue
    mgr = DataProviderManager._create_default()
    q1 = _quote(10.0, "2026-09-05T10:00:00")
    q2 = _quote(9.0, "2026-09-05T10:00:00")
    mock_alert = mocker.patch.object(mgr, "_update_data_quality_alert")
    mocker.patch.object(mgr, "_get_config_for_test", return_value=mocker.Mock(
        data_quality_reconciliation_enabled=False,
    ))
    mgr._reconcile_realtime_cross_source(q1, q2, market="cn", stock_code="600519")
    mock_alert.assert_not_called()
```

- [ ] **Step 9: 运行确认通过**

Run: `uv run python -m pytest tests/test_data_quality_reconciliation.py -q`
Expected: PASS；`python -m py_compile data_provider/base.py` 无错误。

- [ ] **Step 10: 静态校验**

Run: `python -m py_compile data_provider/base.py && uv run python -m pytest tests/test_data_quality_reconciliation.py -q`
Expected: 无错误。

---

### Task 4: 列表 API — schema + endpoint + 路由挂载

**Files:**
- Create: `api/v1/schemas/data_quality.py`
- Create: `api/v1/endpoints/data_quality.py`
- Modify: `api/v1/router.py`
- Test: `tests/test_data_quality_discrepancies_api.py`

**Interfaces:**
- Consumes: `DataQualityDiscrepancyRepository`（Task 1）、`api/v1/errors.api_error`、`api.v1.schemas.common.ErrorResponse`。
- Produces: `GET /api/v1/data-quality/discrepancies`，参数 `market?/stock_code?/issue_type?/page/page_size`。响应：
  `DataQualityDiscrepancyListResponse{items[], total, page, pageSize}`，item 字段（snake→camel）：id, market, stockCode, issueType, primarySource, secondarySource, primaryPrice, secondaryPrice, priceDiffPct, primaryTs, secondaryTs, detail, createdAt。

- [ ] **Step 1: 写失败测试** — `tests/test_data_quality_discrepancies_api.py`

```python
# -*- coding: utf-8 -*-
"""Tests for GET /api/v1/data-quality/discrepancies."""
from fastapi.testclient import TestClient

from src.storage import DatabaseManager, DataQualityDiscrepancyRecord
from server import app


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_list_discrepancies_ok():
    repo = __import__("src.repositories.data_quality_discrepancy_repo", fromlist=["DataQualityDiscrepancyRepository"]).DataQualityDiscrepancyRepository()
    repo.record_discrepancy({
        "market": "cn", "stock_code": "600519.SH", "issue_type": "price_discrepancy",
        "primary_source": "tencent", "secondary_source": "akshare_sina",
        "primary_price": 1500.0, "secondary_price": 1450.0, "price_diff_pct": 3.33,
        "primary_ts": "2026-09-05T10:00:00", "secondary_ts": "2026-09-05T10:00:00",
        "detail": "price mismatch",
    })
    resp = _client().get("/api/v1/data-quality/discrepancies?issue_type=price_discrepancy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["items"][0]["issueType"] == "price_discrepancy"
    assert body["items"][0]["primarySource"] == "tencent"


def test_list_discrepancies_paginates():
    resp = _client().get("/api/v1/data-quality/discrepancies?page=1&page_size=1")
    assert resp.status_code == 200
    assert resp.json()["page"] == 1
    assert len(resp.json()["items"]) <= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_data_quality_discrepancies_api.py -q`
Expected: FAIL（404）。

- [ ] **Step 3: 实现 schema — `api/v1/schemas/data_quality.py`**

镜像 `api/v1/schemas/notifications.py`：

```python
# -*- coding: utf-8 -*-
"""Schemas for cross-source data-quality discrepancies."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class DataQualityDiscrepancyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market: str
    stock_code: str
    issue_type: str
    primary_source: Optional[str] = None
    secondary_source: Optional[str] = None
    primary_price: Optional[float] = None
    secondary_price: Optional[float] = None
    price_diff_pct: Optional[float] = None
    primary_ts: Optional[str] = None
    secondary_ts: Optional[str] = None
    detail: Optional[str] = None
    created_at: datetime


class DataQualityDiscrepancyListResponse(BaseModel):
    items: List[DataQualityDiscrepancyItem]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 4: 实现 endpoint — `api/v1/endpoints/data_quality.py`**

镜像 `api/v1/endpoints/notification_deliveries.py`（不带 `Security`/`Depends`，鉴权走全局 `is_auth_enabled()`）：

```python
# -*- coding: utf-8 -*-
"""Cross-source data-quality discrepancies API."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.errors import api_error
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.data_quality import (
    DataQualityDiscrepancyItem,
    DataQualityDiscrepancyListResponse,
)
from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return api_error(500, "internal_error", message)


@router.get(
    "/discrepancies",
    response_model=DataQualityDiscrepancyListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List cross-source data-quality discrepancies",
)
def list_discrepancies(
    market: Optional[str] = Query(None, description="Optional market filter"),
    stock_code: Optional[str] = Query(None, description="Optional stock code filter"),
    issue_type: Optional[str] = Query(None, description="Optional issue type filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DataQualityDiscrepancyListResponse:
    try:
        rows, total = DataQualityDiscrepancyRepository().list_discrepancies(
            market=market,
            stock_code=stock_code,
            issue_type=issue_type,
            page=page,
            page_size=page_size,
        )
        return DataQualityDiscrepancyListResponse(
            items=[DataQualityDiscrepancyItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List data quality discrepancies failed", exc)
```

- [ ] **Step 5: 挂载路由 — `api/v1/router.py`**

在 import 区加入：

```python
from api.v1.endpoints import (
    data_quality,
)
```

在 `router.include_router(...)` 列表追加：

```python
router.include_router(data_quality.router, prefix="/data-quality", tags=["DataQuality"])
```

（若 import 区是逐行 `from api.v1.endpoints import something`，则改为在该区域内加一行 `from api.v1.endpoints.data_quality import router as data_quality_router`，并在 include 区用 `data_quality_router`。以仓库实际风格为准，保持接口路径与 tag 一致。）

- [ ] **Step 6: 运行确认通过**

Run: `uv run python -m pytest tests/test_data_quality_discrepancies_api.py -q`
Expected: PASS；`python -m py_compile api/v1/schemas/data_quality.py api/v1/endpoints/data_quality.py` 无错误。

---

### Task 5: Web 数据质量视图 — types + api + SettingsPage 卡片 + i18n

**Files:**
- Create: `apps/dsa-web/src/types/dataQuality.ts`
- Create: `apps/dsa-web/src/api/dataQuality.ts`
- Modify: `apps/dsa-web/src/pages/SettingsPage.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`

**Interfaces:**
- Consumes: `apps/dsa-web/src/api/index`（default apiClient）、`apps/dsa-web/src/api/utils.toCamelCase`、
  `SettingsPage` 现有 `SettingsSectionCard`/`Button`/`Select`/`EmptyState`/`ApiErrorAlert`/`Loading`/`Pagination` 等组件、
  `getParsedApiError`/`ParsedApiError`。C 子项目 `NotificationDeliveryCard`（-:987）作为卡片镜像模板。
- Produces: `dataQualityApi.getDiscrepancies(query)`；`DataQualityDiscrepancyItem`/`ListResponse`/`ListQuery` types；
  SettingsPage 区块 `data-testid="data-quality-card"`；i18n key `settings.dataQuality*`（zh + en）。

- [ ] **Step 1: 新建 types — `apps/dsa-web/src/types/dataQuality.ts`**

```ts
/** 跨源数据质量异常（camelCase，由 api/dataQuality 的 snake->camel mapper 构建）。 */
export interface DataQualityDiscrepancyItem {
  id: number;
  market: string;
  stockCode: string;
  issueType: string;
  primarySource: string | null;
  secondarySource: string | null;
  primaryPrice: number | null;
  secondaryPrice: number | null;
  priceDiffPct: number | null;
  primaryTs: string | null;
  secondaryTs: string | null;
  detail: string | null;
  createdAt: string;
}

export interface DataQualityDiscrepancyListResponse {
  items: DataQualityDiscrepancyItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface DataQualityDiscrepancyListQuery {
  market?: string;
  stockCode?: string;
  issueType?: string;
  page?: number;
  pageSize?: number;
}
```

- [ ] **Step 2: 新建 api — `apps/dsa-web/src/api/dataQuality.ts`**

镜像 `apps/dsa-web/src/api/notifications.ts`：

```ts
import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DataQualityDiscrepancyItem,
  DataQualityDiscrepancyListQuery,
  DataQualityDiscrepancyListResponse,
} from '../types/dataQuality';

function toDiscrepancyParams(query: DataQualityDiscrepancyListQuery = {}): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {};
  if (query.market) params.market = query.market;
  if (query.stockCode) params.stock_code = query.stockCode;
  if (query.issueType) params.issue_type = query.issueType;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  return params;
}

function mapDiscrepancyItem(data: Record<string, unknown>): DataQualityDiscrepancyItem {
  const item = toCamelCase<DataQualityDiscrepancyItem>(data);
  item.id = Number(item.id ?? 0);
  item.market = String(item.market ?? '');
  item.stockCode = String(item.stockCode ?? '');
  item.issueType = String(item.issueType ?? '');
  item.primarySource = (item.primarySource ?? null) as string | null;
  item.secondarySource = (item.secondarySource ?? null) as string | null;
  item.primaryPrice = item.primaryPrice == null ? null : Number(item.primaryPrice);
  item.secondaryPrice = item.secondaryPrice == null ? null : Number(item.secondaryPrice);
  item.priceDiffPct = item.priceDiffPct == null ? null : Number(item.priceDiffPct);
  item.primaryTs = (item.primaryTs ?? null) as string | null;
  item.secondaryTs = (item.secondaryTs ?? null) as string | null;
  item.detail = (item.detail ?? null) as string | null;
  item.createdAt = String(item.createdAt ?? '');
  return item;
}

function mapDiscrepancyListResponse(data: Record<string, unknown>): DataQualityDiscrepancyListResponse {
  const rawItems = Array.isArray(data.items) ? data.items : [];
  const items = rawItems.map((it) => mapDiscrepancyItem((it ?? {}) as Record<string, unknown>));
  return {
    items,
    total: Number(data.total ?? 0),
    page: Number(data.page ?? 1),
    pageSize: Number(data.page_size ?? data.pageSize ?? 20),
  };
}

export const dataQualityApi = {
  async getDiscrepancies(query: DataQualityDiscrepancyListQuery = {}): Promise<DataQualityDiscrepancyListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/data-quality/discrepancies', {
      params: toDiscrepancyParams(query),
    });
    return mapDiscrepancyListResponse(response.data);
  },
};
```

- [ ] **Step 3: 在 SettingsPage 新增卡片**

在 `NotificationDeliveryCard`（~987）附近新增 `DataQualityCard`（镜像其结构，字段改为市场/标的/异常类型/价差/时间；筛选为 market + issue_type；分页 + 刷新；`data-testid="data-quality-card"`）。核心组件形如：

```tsx
const DATA_QUALITY_PAGE_SIZE = 20;

type DataQualityIssueFilter = 'all' | 'price_discrepancy' | 'date_mismatch' | 'field_missing';

const DataQualityCard: React.FC<{ t: (k: UiTextKey) => string; language: UiLanguage; disabled: boolean }> = ({ t, language, disabled }) => {
  const [items, setItems] = useState<DataQualityDiscrepancyItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [marketFilter, setMarketFilter] = useState('all');
  const [issueFilter, setIssueFilter] = useState<DataQualityIssueFilter>('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);
  const pageRef = useRef(1);

  const load = useCallback(async (pageOverride?: number) => {
    const requestedPage = pageOverride ?? pageRef.current;
    pageRef.current = requestedPage;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const isLatestRequest = () => requestIdRef.current === requestId;
    setLoading(true);
    try {
      const response = await dataQualityApi.getDiscrepancies({
        market: marketFilter === 'all' ? undefined : marketFilter,
        issueType: issueFilter === 'all' ? undefined : issueFilter,
        page: requestedPage,
        pageSize: DATA_QUALITY_PAGE_SIZE,
      });
      if (!isLatestRequest()) return;
      setItems(response.items);
      setTotal(response.total);
      setPage(requestedPage);
      setError(null);
    } catch (err) {
      if (!isLatestRequest()) return;
      setError(getParsedApiError(err));
    } finally {
      if (isLatestRequest()) setLoading(false);
    }
  }, [marketFilter, issueFilter]);

  useEffect(() => {
    void load(1);
  }, [load]);

  const refresh = () => void load();
  const issueText = (issue: string): string => t(issueLabelKey(issue));
  const pct = (v: number | null): string => (v == null ? '—' : `${v.toFixed(2)}%`);

  return (
    <SettingsSectionCard
      title={t('settings.dataQualityTitle')}
      description={t('settings.dataQualityDescription')}
      actions={(
        <Button type="button" variant="secondary" size="sm" disabled={disabled || loading} isLoading={loading}
          loadingText={t('settings.dataQualityRefreshing')} onClick={refresh}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">{t('settings.dataQualityRefresh')}</span>
        </Button>
      )}
    >
      <div data-testid="data-quality-card" className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Select label={t('settings.dataQualityMarket')} value={marketFilter} disabled={disabled} className="w-44"
            options={[
              { value: 'all', label: t('settings.dataQualityMarketAll') },
              { value: 'cn', label: t('settings.dataQualityMarketCn') },
              { value: 'us', label: t('settings.dataQualityMarketUs') },
              { value: 'hk', label: t('settings.dataQualityMarketHk') },
            ]}
            onChange={(v) => setMarketFilter(v)} />
          <Select label={t('settings.dataQualityIssue')} value={issueFilter} disabled={disabled} className="w-44"
            options={[
              { value: 'all', label: t('settings.dataQualityIssueAll') },
              { value: 'price_discrepancy', label: t('settings.dataQualityIssuePrice') },
              { value: 'date_mismatch', label: t('settings.dataQualityIssueDate') },
              { value: 'field_missing', label: t('settings.dataQualityIssueField') },
            ]}
            onChange={(v) => setIssueFilter(v as DataQualityIssueFilter)} />
        </div>

        {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
        {loading ? <Loading label={t('settings.dataQualityLoading')} /> : null}
        {!loading && items.length === 0 ? (
          <EmptyState icon={<AlertTriangle className="h-6 w-6" />} title={t('settings.dataQualityEmptyTitle')}
            description={t('settings.dataQualityEmptyDescription')}
            className="flex flex-col items-center justify-center" />
        ) : null}
        {!loading && items.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColMarket')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColStock')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColIssue')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColSources')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColDiff')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColTime')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="px-3 py-3">{item.market}</td>
                      <td className="px-3 py-3">{item.stockCode}</td>
                      <td className="px-3 py-3">{issueText(item.issueType)}</td>
                      <td className="px-3 py-3">{item.primarySource} ↔ {item.secondarySource}</td>
                      <td className="px-3 py-3">{pct(item.priceDiffPct)}</td>
                      <td className="px-3 py-3">{item.createdAt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} total={total} pageSize={DATA_QUALITY_PAGE_SIZE}
              onPageChange={(p) => load(p)} />
          </>
        ) : null}
      </div>
    </SettingsSectionCard>
  );
};
```

（组件内用到的 `useState/useEffect/useRef/useCallback/RefreshCw/AlertTriangle/SettingsSectionCard/Button/Select/EmptyState/Loading/ApiErrorAlert/Pagination/getParsedApiError/MARKET/issueLabelKey` 等需与 SettingsPage 现有 import 一致；`issueLabelKey` 与 `dataQualityTitle/Description/...` i18n key 由本任务 Step 4 提供。）

- [ ] **Step 4: 在 SettingsPage 挂载 + 更新 i18n**

在 `SettingsPage.tsx` 中，仿照 `NotificationDeliveryCard` 的挂载位置（`activeCategory === 'notification'` 区块内），在同区域挂载：

```tsx
            {activeCategory === 'notification' ? (
              <DataQualityCard
                t={t}
                language={language}
                disabled={disabled}
              />
            ) : null}
```

（若「数据质量异常」更适合独立分类而非挂靠 `notification`，可按 SettingsPage 实际的分类渲染结构放在最贴近「系统设置 / 数据」的分类下；保持 `data-testid="data-quality-card"`。若需新增分类按钮，一并加入 `categories` 列表。）

在 `apps/dsa-web/src/i18n/uiText.ts` 的 `settings` 段（zh + en 各一份）加入以下 keys（文案见括号建议，翻译成对应语言）：

```
zh:
'settings.dataQualityTitle': '数据质量异常'
'settings.dataQualityDescription': '跨源一致性对账：选源成功后用次选源比对价差、交易日与字段缺失，出现异常的记录。'
'settings.dataQualityRefresh': '刷新'
'settings.dataQualityRefreshing': '刷新中'
'settings.dataQualityMarket': '市场'
'settings.dataQualityMarketAll': '全部市场'
'settings.dataQualityMarketCn': 'A股'
'settings.dataQualityMarketUs': '美股'
'settings.dataQualityMarketHk': '港股'
'settings.dataQualityIssue': '异常类型'
'settings.dataQualityIssueAll': '全部类型'
'settings.dataQualityIssuePrice': '价差异常'
'settings.dataQualityIssueDate': '交易日错位'
'settings.dataQualityIssueField': '字段缺失'
'settings.dataQualityLoading': '加载中'
'settings.dataQualityEmptyTitle': '暂无数据质量异常'
'settings.dataQualityEmptyDescription': '尚未检测到跨源不一致；如有异常将在此列出。'
'settings.dataQualityColMarket': '市场'
'settings.dataQualityColStock': '标的'
'settings.dataQualityColIssue': '异常类型'
'settings.dataQualityColSources': '源对比'
'settings.dataQualityColDiff': '价差'
'settings.dataQualityColTime': '时间'

en:
'settings.dataQualityTitle': 'Data quality anomalies'
'settings.dataQualityDescription': 'Cross-source reconciliation: compares price, trade date, and required fields against the alternate source after a source is chosen; anomalies are listed here.'
'settings.dataQualityRefresh': 'Refresh'
'settings.dataQualityRefreshing': 'Refreshing'
'settings.dataQualityMarket': 'Market'
'settings.dataQualityMarketAll': 'All markets'
'settings.dataQualityMarketCn': 'A-shares'
'settings.dataQualityMarketUs': 'US'
'settings.dataQualityMarketHk': 'HK'
'settings.dataQualityIssue': 'Issue type'
'settings.dataQualityIssueAll': 'All types'
'settings.dataQualityIssuePrice': 'Price diff'
'settings.dataQualityIssueDate': 'Date mismatch'
'settings.dataQualityIssueField': 'Field missing'
'settings.dataQualityLoading': 'Loading'
'settings.dataQualityEmptyTitle': 'No data-quality anomalies'
'settings.dataQualityEmptyDescription': 'No cross-source inconsistency detected yet; any anomaly will be listed here.'
'settings.dataQualityColMarket': 'Market'
'settings.dataQualityColStock': 'Symbol'
'settings.dataQualityColIssue': 'Issue type'
'settings.dataQualityColSources': 'Source diff'
'settings.dataQualityColDiff': 'Diff %'
'settings.dataQualityColTime': 'Time'
```

（`issueLabelKey` 在组件里把 issue token 映射到上述 key，例如 `price_discrepancy` → `settings.dataQualityIssuePrice`，`date_mismatch` → `settings.dataQualityIssueDate`，`field_missing` → `settings.dataQualityIssueField`，未知回退原 token。）

- [ ] **Step 5: 前端校验**

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint 0 错误、build 成功。若组件因 `item.createdAt` 或未用变量报 TS 错误，按 TS 类型收紧（如 `item.createdAt = String(item.createdAt ?? '')` 已在 mapper 处理）。

- [ ] **Step 6: 说明截图**

Web UI 变更按 `AGENTS.md` 需在合并 PR 描述附受影响页面截图；若暂不截图，交付说明中写清原因与替代可视证据。

---

### Task 6: 文档校对 — `docs/data-source-stability.md`

**Files:**
- Modify: `docs/data-source-stability.md`

**Interfaces:**
- Consumes: 无（纯文档）。

- [ ] **Step 1: 更新「未来项」段**

在 `docs/data-source-stability.md` 的「后续可做的产品化增强」区（约 `:183`），第一条「数据源 Doctor 页面」改为注明已完成与本子项目的关系，或补一条说明：

```markdown
- **跨源一致性对账（已落地）**：选源成功后用配置优先级链里的次选源比对价差、交易日与字段缺失，
  命中记录数据质量异常（`data_quality_discrepancies`）并按 `system_error` 路由告警。配置见
  `DATA_QUALITY_RECONCILIATION_ENABLED` / `DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT` /
  `DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS`；关闭即停用，不影响既有选源与熔断。
```

- [ ] **Step 2: 核对一致性**

确认文档中不再把「无跨源对账」列为缺口；若文档早前有「多源不校准」之类的表述，一并修正为「已提供跨源一致性对账」。

- [ ] **Step 3: 说明**

Docs only, tests not run；核对 `.env.example`、`src/config.py` 字段名、`data-source-stability.md` 与本次实现一致。

---

## 交叉任务扫描（写计划时已核对）

| 两任务 | 相互产物 | 已核对结论 |
| --- | --- | --- |
| Task 1 ↔ Task 3 | `DataQualityDiscrepancyRecord` 列名 vs `_update_data_quality_alert` 的 fields | 一致（market/stock_code/issue_type/primary_source/.../detail）。 |
| Task 2 ↔ Task 3 | 配置字段名 vs `_reconcile_*` 的 `getattr` 取值 | 一致（`data_quality_reconciliation_enabled`/`_price_diff_threshold_pct`/`_date_mismatch_tolerance_seconds`）。 |
| Task 3 ↔ Task 4 | 落库列 vs schema `model_validate(row)` | 一致（`from_attributes=True` 读 ORM 列）。 |
| Task 4 ↔ Task 5 | snake 响应 vs camelCase mapper | 一致（`toCamelCase` 负责转换，`page_size`→`pageSize`）。 |
| Task 3 内 两接入点 | A 股（零额外开销）与美/港/日 K（额外调用） | 均受 `data_quality_reconciliation_enabled` 门控 & try/except。 |
