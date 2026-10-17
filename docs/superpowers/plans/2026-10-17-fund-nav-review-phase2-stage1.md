# 基金净值复盘二期 · 阶段 1（真复权口径与回填）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为基金净值复盘增加分红再投资复权口径、累计净值/单位净值回退链、存量回填与组合页双口径展示。

**Architecture:** 在 `data_provider/fund_fetcher.py` 增加东财 F10 分红送配解析；新增纯函数模块 `src/services/fund_nav_restore.py` 计算复权收益/回撤与回退链；在现有 `FundNavReviewService` 的评估与回填流程中接入；数据库只追加可空列；API 与 Web 只追加字段和切换控件，旧单位口径语义不变。

**Tech Stack:** Python 3 / SQLAlchemy / FastAPI / pytest；React + TypeScript + vitest；数据源为东财 F10 HTML。

**Spec:** `docs/superpowers/specs/2026-10-17-fund-nav-review-phase2-design.md`

## Global Constraints

- 未经明确确认不执行 `git commit`、`git tag`、`git push`；计划中的 commit 步骤需先获得用户确认。
- commit message 使用英文，不添加 `Co-Authored-By`。
- 不写死密钥、账号、路径、模型名、端口；新配置必须有默认值并同步 `.env.example` 与文档。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 使用扁平 `- [类型] 描述`，禁止新增 `###` 类标题。
- 旧字段 `nav_return_pct` / `max_drawdown_pct` / `eval_status` 语义不变；新增列全部可空。
- Web 不使用原生 `title`；中英 i18n key 必须同时补齐。
- Python 验证优先 `uv run python -m pytest`；全量门槛 `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`。
- Web 命令必须在 `apps/dsa-web` 内执行。

---

### Task 1: F10 分红送配解析

**Files:**
- Modify: `data_provider/fund_fetcher.py`
- Test: `tests/test_fund_fetcher.py`

**Interfaces:**
- Produces:
  - `FundDividendEvent(date: str, cash_per_share: float)`
  - `FundSplitEvent(date: str, ratio: float)`
  - `FundDistributionEvents(dividends: List[FundDividendEvent], splits: List[FundSplitEvent], skipped_rows: int = 0)`
  - `parse_fhsp(html_text: str) -> FundDistributionEvents`
  - `FundFetcher.get_distribution_events(code: str) -> FundDistributionEvents`

- [ ] **Step 1: 写失败测试**

在 `tests/test_fund_fetcher.py` 追加：

```python
FHSP_HTML = """
<table class='w782 comm cfxq'><thead><tr><th>年份</th><th>权益登记日</th><th>除息日</th><th>每10份分红</th><th>分红发放日</th></tr></thead>
<tbody>
<tr><td>2021年</td><td>2021-08-10</td><td>2021-08-10</td><td>每10份派现金1.2739元</td><td>2021-08-12</td></tr>
<tr><td>坏行</td><td></td><td></td><td>暂无</td><td></td></tr>
</tbody></table>
<table class='w782 comm fhxq'><thead><tr><th>年份</th><th>拆分折算日</th><th>拆分类型</th><th>拆分折算比例</th></tr></thead>
<tbody><tr><td>2020年</td><td>2020-08-26</td><td>份额折算</td><td>1:1.4982</td></tr></tbody></table>
"""


def test_parse_fhsp_extracts_dividends_and_splits():
    events = parse_fhsp(FHSP_HTML)
    assert [(e.date, e.cash_per_share) for e in events.dividends] == [("2021-08-10", 0.12739)]
    assert [(e.date, e.ratio) for e in events.splits] == [("2020-08-26", 1.4982)]
    assert events.skipped_rows == 1


def test_parse_fhsp_empty_tables_return_empty_events():
    html = (
        "<table class='w782 comm cfxq'><tbody><tr><td colspan='5'>暂无分红信息!</td></tr></tbody></table>"
        "<table class='w782 comm fhxq'><tbody><tr><td colspan='4'>暂无拆分信息!</td></tr></tbody></table>"
    )
    events = parse_fhsp(html)
    assert events.dividends == []
    assert events.splits == []
    assert events.skipped_rows == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_fetcher.py -k fhsp -v`
Expected: FAIL（`parse_fhsp` 未定义）

- [ ] **Step 3: 实现解析**

在 `data_provider/fund_fetcher.py` 增加：

```python
FUND_FHSP_URL = "https://fundf10.eastmoney.com/fhsp_{code}.html"
_DIVIDEND_AMOUNT_RE = re.compile(r"每10份.*?([\d.]+)元")
_SPLIT_RATIO_RE = re.compile(r"^\s*1\s*:\s*([\d.]+)\s*$")


@dataclass
class FundDividendEvent:
    date: str
    cash_per_share: float


@dataclass
class FundSplitEvent:
    date: str
    ratio: float


@dataclass
class FundDistributionEvents:
    dividends: List[FundDividendEvent] = field(default_factory=list)
    splits: List[FundSplitEvent] = field(default_factory=list)
    skipped_rows: int = 0


def _html_table_rows(html_text: str, table_class: str) -> List[str]:
    match = re.search(
        rf"<table[^>]*class=['\"][^'\"]*{table_class}[^'\"]*['\"][^>]*>(.*?)</table>",
        html_text,
        re.S,
    )
    if not match:
        return []
    return re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S)


def _html_cells(row_html: str) -> List[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    ]


def parse_fhsp(html_text: str) -> FundDistributionEvents:
    events = FundDistributionEvents()
    for row in _html_table_rows(html_text, "cfxq"):
        cells = _html_cells(row)
        if len(cells) < 4:
            continue
        event_date = cells[2] or cells[1]
        amount = _DIVIDEND_AMOUNT_RE.search(cells[3])
        try:
            if not event_date or amount is None:
                raise ValueError("invalid dividend row")
            cash = float(amount.group(1)) / 10.0
            if cash <= 0:
                raise ValueError("non-positive dividend")
            events.dividends.append(FundDividendEvent(event_date, round(cash, 8)))
        except (TypeError, ValueError):
            events.skipped_rows += 1
    for row in _html_table_rows(html_text, "fhxq"):
        cells = _html_cells(row)
        if len(cells) < 4:
            continue
        ratio_match = _SPLIT_RATIO_RE.match(cells[3] or "")
        try:
            if not cells[1] or ratio_match is None:
                raise ValueError("invalid split row")
            ratio = float(ratio_match.group(1))
            if ratio <= 0:
                raise ValueError("non-positive split ratio")
            events.splits.append(FundSplitEvent(cells[1], ratio))
        except (TypeError, ValueError):
            events.skipped_rows += 1
    events.dividends.sort(key=lambda item: item.date)
    events.splits.sort(key=lambda item: item.date)
    return events


class FundFetcher:
    def get_distribution_events(self, code: str) -> FundDistributionEvents:
        base = strip_fund_prefix(code)
        response = requests.get(
            FUND_FHSP_URL.format(code=base), headers=_HEADERS, timeout=20
        )
        response.raise_for_status()
        return parse_fhsp(response.text)
```

注意：`requests` 已导入；`html` 模块已导入；`field` 已导入。`get_distribution_events` 的 HTTP/解析异常向上抛，调用方负责降级。

- [ ] **Step 4: 运行通过并回归**

Run: `uv run python -m pytest tests/test_fund_fetcher.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add data_provider/fund_fetcher.py tests/test_fund_fetcher.py
git commit -m "feat(funds): parse Eastmoney dividend and split events"
```

---

### Task 2: 复权计算纯函数

**Files:**
- Create: `src/services/fund_nav_restore.py`
- Test: `tests/test_fund_nav_restore.py`

**Interfaces:**
- Consumes: `NavRecord`, `FundDistributionEvents`（Task 1）。
- Produces:
  - 常量 `RESTORED_BASIS_DIVIDEND_REINVESTED = "dividend_reinvested"`、`RESTORED_BASIS_ACC = "acc_nav"`、`RESTORED_BASIS_UNIT = "unit_nav"`
  - `compute_restored_metrics(navs, anchor_idx, target_idx, events, *, events_error=None) -> dict`
  - 返回键：`restored_return_pct`、`restored_max_drawdown_pct`、`restored_basis`、`restored_reason`、`restored_events`、`anchor_acc_nav`、`eval_acc_nav`、`acc_nav_return_pct`、`acc_max_drawdown_pct`。

- [ ] **Step 1: 写失败测试**

```python
from data_provider.fund_fetcher import (
    FundDividendEvent, FundDistributionEvents, FundSplitEvent, NavRecord,
)
from src.services.fund_nav_restore import compute_restored_metrics


def _nav(day, unit, acc):
    return NavRecord(date=day, unit_nav=unit, acc_nav=acc, change_pct=0.0)


def test_dividend_reinvested_return_beats_unit_return():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-03", 0.90, 1.10), _nav("2025-01-06", 0.99, 1.10)]
    events = FundDistributionEvents(dividends=[FundDividendEvent("2025-01-03", 0.10)])
    result = compute_restored_metrics(navs, 0, 2, events)
    assert result["restored_basis"] == "dividend_reinvested"
    assert result["restored_return_pct"] == 10.0  # 0.99 * (1 + 0.10/0.90) / 1.00 - 1


def test_split_multiplies_shares():
    navs = [_nav("2025-01-02", 2.00, 2.00), _nav("2025-01-03", 1.00, 2.00), _nav("2025-01-06", 1.10, 2.20)]
    events = FundDistributionEvents(splits=[FundSplitEvent("2025-01-03", 2.0)])
    result = compute_restored_metrics(navs, 0, 2, events)
    assert result["restored_return_pct"] == 10.0


def test_invalid_distribution_event_falls_back_to_acc_nav():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-06", 1.10, 1.21)]
    events = FundDistributionEvents(dividends=[FundDividendEvent("2025-01-03", 0.0)])
    result = compute_restored_metrics(navs, 0, 1, events)
    assert result["restored_basis"] == "acc_nav"
    assert result["restored_reason"] == "invalid_distribution_event"
    assert result["restored_return_pct"] == 10.0


def test_distribution_fetch_error_falls_back_to_acc_then_unit():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-06", 1.10, 1.21)]
    result = compute_restored_metrics(navs, 0, 1, None, events_error="distribution_fetch_error")
    assert result["restored_basis"] == "acc_nav"
    navs_no_acc = [_nav("2025-01-02", 1.00, 0.0), _nav("2025-01-06", 1.10, 0.0)]
    result = compute_restored_metrics(navs_no_acc, 0, 1, None, events_error="distribution_fetch_error")
    assert result["restored_basis"] == "unit_nav"
    assert result["restored_reason"] == "acc_nav_unavailable"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_restore.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""Dividend-reinvested restoration for fund NAV reviews."""

from __future__ import annotations

import bisect
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from data_provider.fund_fetcher import FundDistributionEvents, NavRecord

RESTORED_BASIS_DIVIDEND_REINVESTED = "dividend_reinvested"
RESTORED_BASIS_ACC = "acc_nav"
RESTORED_BASIS_UNIT = "unit_nav"


def _date_key(value: str) -> str:
    return (value or "")[:10]


def _acc_metrics(navs: Sequence[NavRecord], anchor_idx: int, target_idx: int) -> Dict[str, Any]:
    window = navs[anchor_idx:target_idx + 1]
    values = [float(record.acc_nav or 0) for record in window]
    anchor_acc = values[0] if values else 0.0
    eval_acc = values[-1] if values else 0.0
    if anchor_acc <= 0 or eval_acc <= 0 or any(value <= 0 for value in values):
        return {
            "anchor_acc_nav": anchor_acc if anchor_acc > 0 else None,
            "eval_acc_nav": eval_acc if eval_acc > 0 else None,
            "acc_nav_return_pct": None,
            "acc_max_drawdown_pct": None,
        }
    peak = anchor_acc
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {
        "anchor_acc_nav": anchor_acc,
        "eval_acc_nav": eval_acc,
        "acc_nav_return_pct": round((eval_acc / anchor_acc - 1.0) * 100.0, 4),
        "acc_max_drawdown_pct": round(drawdown * 100.0, 4),
    }


def _restored_series(
    navs: Sequence[NavRecord], anchor_idx: int, target_idx: int, events: FundDistributionEvents,
) -> Optional[List[float]]:
    by_date = {_date_key(record.date): float(record.unit_nav or 0) for record in navs[anchor_idx:target_idx + 1]}
    dates = sorted(by_date)
    if not dates:
        return None
    anchor_key = dates[0]
    eval_key = dates[-1]
    event_rows = [
        ("dividend", event.date, event.cash_per_share)
        for event in events.dividends
    ] + [
        ("split", event.date, event.ratio)
        for event in events.splits
    ]
    event_rows = sorted(
        (item for item in event_rows if anchor_key < _date_key(item[1]) <= eval_key),
        key=lambda item: _date_key(item[1]),
    )
    values: List[float] = []
    factor = 1.0
    event_index = 0
    for day in dates:
        while event_index < len(event_rows) and _date_key(event_rows[event_index][1]) <= day:
            kind, _, amount = event_rows[event_index]
            event_index += 1
            if float(amount) <= 0:
                return None
            if kind == "split":
                factor *= float(amount)
                continue
            nav_at_event = by_date.get(day, 0.0)
            if nav_at_event <= 0:
                return None
            factor *= 1.0 + float(amount) / nav_at_event
        values.append(by_date[day] * factor)
    return values


def compute_restored_metrics(
    navs: Sequence[NavRecord],
    anchor_idx: int,
    target_idx: int,
    events: Optional[FundDistributionEvents],
    *,
    events_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the best available restored metrics for one review row."""
    acc = _acc_metrics(navs, anchor_idx, target_idx)
    result: Dict[str, Any] = {
        **acc,
        "restored_return_pct": None,
        "restored_max_drawdown_pct": None,
        "restored_basis": None,
        "restored_reason": None,
        "restored_events": 0,
    }
    adjusted = _restored_series(navs, anchor_idx, target_idx, events) if events is not None else None
    if adjusted:
        anchor_value = adjusted[0]
        eval_value = adjusted[-1]
        peak = anchor_value
        drawdown = 0.0
        for value in adjusted:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1.0)
        result.update({
            "restored_return_pct": round((eval_value / anchor_value - 1.0) * 100.0, 4),
            "restored_max_drawdown_pct": round(drawdown * 100.0, 4),
            "restored_basis": RESTORED_BASIS_DIVIDEND_REINVESTED,
            "restored_events": len([
                event for event in (events.dividends + events.splits)
                if _date_key(navs[anchor_idx].date) < _date_key(event.date) <= _date_key(navs[target_idx].date)
            ]),
        })
        return result
    reason = (
        (events_error or "distribution_fetch_error")
        if events is None
        else "invalid_distribution_event"
    )
    if acc["acc_nav_return_pct"] is not None:
        result.update({
            "restored_return_pct": acc["acc_nav_return_pct"],
            "restored_max_drawdown_pct": acc["acc_max_drawdown_pct"],
            "restored_basis": RESTORED_BASIS_ACC,
            "restored_reason": reason,
        })
        return result
    unit_values = [float(record.unit_nav or 0) for record in navs[anchor_idx:target_idx + 1]]
    if unit_values and unit_values[0] > 0 and unit_values[-1] > 0:
        peak = unit_values[0]
        drawdown = 0.0
        for value in unit_values:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1.0)
        result.update({
            "restored_return_pct": round((unit_values[-1] / unit_values[0] - 1.0) * 100.0, 4),
            "restored_max_drawdown_pct": round(drawdown * 100.0, 4),
            "restored_basis": RESTORED_BASIS_UNIT,
            "restored_reason": reason or "acc_nav_unavailable",
        })
    return result
```

实现注意：无事件时 `restored_basis` 允许直接为 `acc_nav`（原因为空），这仍满足「最佳可用口径」；不要在无事件时伪造 `dividend_reinvested`。

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_restore.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/services/fund_nav_restore.py tests/test_fund_nav_restore.py
git commit -m "feat(funds): add dividend-reinvested NAV metric calculation"
```

---

### Task 3: 数据模型、迁移与仓储

**Files:**
- Modify: `src/storage.py`（`FundNavReviewRecord`、`_init_db`、新增 `_ensure_fund_nav_review_restore_columns`）
- Modify: `src/repositories/fund_nav_review_repo.py`（`update_review` 白名单、新增查询）
- Test: `tests/test_fund_nav_review_store.py`

**Interfaces:**
- Produces:
  - 列：`anchor_acc_nav`、`eval_acc_nav`、`acc_nav_return_pct`、`acc_max_drawdown_pct`、`restored_return_pct`、`restored_max_drawdown_pct`、`restored_basis`、`restored_reason`、`restored_events`、`restored_checked_at`
  - `FundNavReviewRepository.list_restore_backlog(engine_version, fund_code=None, anchor_from=None, anchor_to=None, force=False, limit=400) -> List[FundNavReviewRecord]`

- [ ] **Step 1: 写失败测试**

```python
def test_restore_columns_exist_and_backlog_query(tmp_path):
    manager = DatabaseManager(db_path=str(tmp_path / "restore.db"))
    manager.init_db()
    columns = {c["name"] for c in inspect(manager.engine).get_columns("fund_nav_reviews")}
    assert {"restored_return_pct", "restored_basis", "restored_checked_at"} <= columns
    repo = FundNavReviewRepository(manager)
    repo.insert_reviews([{
        "fund_code": "003095", "fund_name": "测试基金", "horizon": "1m",
        "engine_version": "fund-nav-v1", "anchor_date": date(2025, 1, 2),
        "anchor_nav": 1.0, "due_date": date(2025, 2, 6), "eval_status": "evaluated",
        "anchor_source": "pipeline", "attempts": 1,
    }])
    assert len(repo.list_restore_backlog(engine_version="fund-nav-v1")) == 1
```

（按现有测试文件的 fixture 风格调整 `DatabaseManager` 构造方式。）

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_review_store.py -k restore -v`
Expected: FAIL

- [ ] **Step 3: 实现列与迁移**

在 `FundNavReviewRecord` 的 `evaluated_at` 后追加可空列，并在 `__table_args__` 增加：

```python
restored_return_pct = Column(Float)
restored_max_drawdown_pct = Column(Float)
restored_basis = Column(String(24), index=True)
restored_reason = Column(String(48))
restored_events = Column(Integer)
restored_checked_at = Column(DateTime, index=True)
anchor_acc_nav = Column(Float)
eval_acc_nav = Column(Float)
acc_nav_return_pct = Column(Float)
acc_max_drawdown_pct = Column(Float)

Index(
    'ix_fund_nav_review_restore_backlog',
    'engine_version', 'eval_status', 'restored_checked_at',
)
```

在 `_init_db` 中 `_ensure_decision_signal_outcome_benchmark_columns()` 之后调用 `self._ensure_fund_nav_review_restore_columns()`。

迁移方法复制现有 SQLite 幂等模式：

```python
def _ensure_fund_nav_review_restore_columns(self) -> None:
    if not self._is_sqlite_engine:
        return
    try:
        existing = {
            column["name"]
            for column in inspect(self._engine).get_columns(
                FundNavReviewRecord.__tablename__
            )
        }
    except Exception as exc:
        logger.warning("[fund nav review] failed to inspect restore columns: %s", exc)
        return
    column_sql = {
        "anchor_acc_nav": "FLOAT",
        "eval_acc_nav": "FLOAT",
        "acc_nav_return_pct": "FLOAT",
        "acc_max_drawdown_pct": "FLOAT",
        "restored_return_pct": "FLOAT",
        "restored_max_drawdown_pct": "FLOAT",
        "restored_basis": "VARCHAR(24)",
        "restored_reason": "VARCHAR(48)",
        "restored_events": "INTEGER",
        "restored_checked_at": "DATETIME",
    }
    max_retries = self._sqlite_write_retry_max
    for column, column_type in column_sql.items():
        if column in existing:
            continue
        for attempt in range(max_retries + 1):
            try:
                with self._engine.begin() as connection:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {FundNavReviewRecord.__tablename__} "
                        f"ADD COLUMN {column} {column_type}"
                    )
                existing.add(column)
                break
            except OperationalError as exc:
                if self._is_sqlite_duplicate_column_error(exc, column):
                    existing.add(column)
                    break
                if self._is_sqlite_locked_error(exc) and attempt < max_retries:
                    time.sleep(self._sqlite_write_retry_base_delay * (2 ** attempt))
                    continue
                raise
    with self._engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_fund_nav_review_restore_backlog "
            "ON fund_nav_reviews (engine_version, eval_status, restored_checked_at)"
        )
```

仓储 `update_review` 白名单追加全部新列；新增：

```python
def list_restore_backlog(
    self, *, engine_version: str, fund_code: Optional[str] = None,
    anchor_from: Optional[date] = None, anchor_to: Optional[date] = None,
    force: bool = False, limit: int = 400,
) -> List[FundNavReviewRecord]:
    conditions = [
        FundNavReviewRecord.engine_version == engine_version,
        FundNavReviewRecord.eval_status == 'evaluated',
    ]
    if not force:
        conditions.append(FundNavReviewRecord.restored_checked_at.is_(None))
    if fund_code:
        conditions.append(FundNavReviewRecord.fund_code == fund_code)
    if anchor_from:
        conditions.append(FundNavReviewRecord.anchor_date >= anchor_from)
    if anchor_to:
        conditions.append(FundNavReviewRecord.anchor_date <= anchor_to)
    with self.db.get_session() as session:
        rows = session.execute(
            select(FundNavReviewRecord)
            .where(and_(*conditions))
            .order_by(FundNavReviewRecord.anchor_date, FundNavReviewRecord.id)
            .limit(max(1, min(int(limit), 400)))
        ).scalars().all()
        return list(rows)
```

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_review_store.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/storage.py src/repositories/fund_nav_review_repo.py tests/test_fund_nav_review_store.py
git commit -m "feat(funds): add restored NAV review columns and backlog query"
```

---

### Task 4: 评估链路接入复权与回退

**Files:**
- Modify: `src/services/fund_nav_review_service.py`
- Test: `tests/test_fund_nav_review_service.py`

**Interfaces:**
- Consumes: `compute_restored_metrics`（Task 2）、`get_distribution_events`（Task 1）、新列（Task 3）。
- Produces: `run_reviews` 的每行结果包含 restored 字段；`_evaluate_row` 写入新列；`record_anchor` 写入 `anchor_acc_nav`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_fund_nav_review_service.py` 的 FakeFetcher 增加 `get_distribution_events`，并新增：

```python
def test_evaluate_row_writes_dividend_reinvested_metrics(service, fake_fetcher):
    # 锚点 1.00，除息日净值 0.90、每份分红 0.10，评估日 0.99
    fake_fetcher.navs = [...]
    fake_fetcher.events = FundDistributionEvents(
        dividends=[FundDividendEvent("2025-01-03", 0.10)]
    )
    result = service.run_reviews(as_of=date(2025, 1, 6))
    item = result["items"][0]
    assert item["restored_basis"] == "dividend_reinvested"
    assert item["restored_return_pct"] == 10.0
    assert item["nav_return_pct"] == -1.0  # 单位口径不变


def test_event_fetch_failure_does_not_block_evaluation(service, fake_fetcher):
    fake_fetcher.get_distribution_events = lambda code: (_ for _ in ()).throw(RuntimeError("boom"))
    result = service.run_reviews(as_of=date(2025, 1, 6))
    item = result["items"][0]
    assert item["eval_status"] == "evaluated"
    assert item["restored_basis"] in {"acc_nav", "unit_nav"}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_review_service.py -k restored -v`
Expected: FAIL

- [ ] **Step 3: 实现**

- `record_anchor`：读取 `latest.acc_nav`，>0 时写入 `_build_anchor_rows` 的 `anchor_acc_nav`。
- `run_reviews`：在按基金循环内，拉取净值区间后尝试 `events = self.fetcher.get_distribution_events(code)`，失败置 `events=None, events_error="distribution_fetch_error"`，只 warning；把 `events/events_error` 传给 `_evaluate_row`。
- `_evaluate_row` 在写完单位指标后：

```python
restored = compute_restored_metrics(
    navs, anchor_idx, target_idx, events, events_error=events_error,
)
fields.update({
    "anchor_acc_nav": restored["anchor_acc_nav"],
    "eval_acc_nav": restored["eval_acc_nav"],
    "acc_nav_return_pct": restored["acc_nav_return_pct"],
    "acc_max_drawdown_pct": restored["acc_max_drawdown_pct"],
    "restored_return_pct": restored["restored_return_pct"],
    "restored_max_drawdown_pct": restored["restored_max_drawdown_pct"],
    "restored_basis": restored["restored_basis"],
    "restored_reason": restored["restored_reason"],
    "restored_events": restored["restored_events"],
    "restored_checked_at": now,
})
```

- `_to_item` 与返回 item 追加同名字段。
- `_evaluate_row` 新增关键字参数 `events=None, events_error=None`，保持默认值以便旧调用。

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_review_service.py -q`
Expected: PASS，且原有用例全部通过

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/services/fund_nav_review_service.py tests/test_fund_nav_review_service.py
git commit -m "feat(funds): evaluate restored NAV metrics with fallback chain"
```

---

### Task 5: 存量回填服务与 API

**Files:**
- Modify: `src/services/fund_nav_review_service.py`
- Modify: `api/v1/schemas/fund_nav_reviews.py`
- Modify: `api/v1/endpoints/fund_nav_reviews.py`
- Test: `tests/test_fund_nav_restore_backfill.py`（新建）、`tests/test_fund_nav_review_api.py`

**Interfaces:**
- Produces:
  - `FundNavReviewService.backfill_restored_metrics(*, fund_code=None, anchor_from=None, anchor_to=None, limit=200, dry_run=True, force=False) -> dict`
  - `POST /api/v1/fund-nav-reviews/backfill/restore-metrics`
  - 请求/响应 schema `FundNavRestoreBackfillRequest`、`FundNavRestoreBackfillResponse`

- [ ] **Step 1: 写失败测试**

```python
def test_backfill_restore_metrics_dry_run_does_not_write(service, fake_fetcher, evaluated_row):
    result = service.backfill_restored_metrics(dry_run=True)
    assert result["dry_run"] is True
    assert result["scanned"] == 1
    assert evaluated_row.restored_basis is None


def test_backfill_restore_metrics_updates_idempotently(service, fake_fetcher, evaluated_row):
    first = service.backfill_restored_metrics(dry_run=False)
    second = service.backfill_restored_metrics(dry_run=False)
    assert first["updated"] == 1
    assert second["scanned"] == 0


def test_backfill_marks_window_mismatch(service, fake_fetcher, evaluated_row):
    evaluated_row.eval_date = date(2025, 1, 7)  # 与目标索引不一致
    result = service.backfill_restored_metrics(dry_run=False)
    assert result["skipped"] == 1
    assert evaluated_row.restored_basis is None
```

API 测试：鉴权、`dry_run` 默认 true、`limit > 400` 返回 422、非法 `fund_code` 返回 400。

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_restore_backfill.py -v`
Expected: FAIL

- [ ] **Step 3: 实现服务**

```python
def backfill_restored_metrics(
    self, *, fund_code: Optional[str] = None,
    anchor_from: Optional[date] = None, anchor_to: Optional[date] = None,
    limit: int = 200, dry_run: bool = True, force: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or utc_naive_now()
    normalized = _normalize_fund_code(fund_code) if fund_code else None
    if fund_code and not normalized:
        raise ValueError("fund_code must be a 6-digit fund code")
    rows = self.repo.list_restore_backlog(
        engine_version=FUND_NAV_REVIEW_ENGINE_VERSION,
        fund_code=normalized, anchor_from=anchor_from, anchor_to=anchor_to,
        force=force, limit=min(max(int(limit), 1), MAX_ROWS_PER_RUN),
    )
    counts = {"scanned": len(rows), "updated": 0, "unavailable": 0,
              "skipped": 0, "funds": 0}
    for code, fund_rows in self._group_rows_by_fund(rows):
        counts["funds"] += 1
        navs = self.fetcher.get_nav_range(
            code, min(row.anchor_date for row in fund_rows),
            max(row.eval_date or row.anchor_date for row in fund_rows),
        )
        try:
            events = self.fetcher.get_distribution_events(code)
            events_error = None
        except Exception as exc:
            logger.warning("[%s] 基金分红送配抓取失败: %s", code, exc)
            events, events_error = None, "distribution_fetch_error"
        for row in fund_rows:
            dates = [(record.date or "")[:10] for record in navs]
            anchor_idx = bisect.bisect_left(dates, row.anchor_date.isoformat())
            if anchor_idx >= len(navs) or dates[anchor_idx] != row.anchor_date.isoformat():
                counts["skipped"] += 1
                continue
            target_idx = anchor_idx + FUND_NAV_REVIEW_HORIZONS[row.horizon]
            if target_idx >= len(navs) or _parse_date(navs[target_idx].date) != row.eval_date:
                counts["skipped"] += 1
                continue
            restored = compute_restored_metrics(
                navs, anchor_idx, target_idx, events, events_error=events_error
            )
            if dry_run:
                counts["updated"] += 1 if restored["restored_basis"] else 0
                counts["unavailable"] += 0 if restored["restored_basis"] else 1
                continue
            self.repo.update_review(review_id=row.id, fields={
                "anchor_acc_nav": restored["anchor_acc_nav"],
                "eval_acc_nav": restored["eval_acc_nav"],
                "acc_nav_return_pct": restored["acc_nav_return_pct"],
                "acc_max_drawdown_pct": restored["acc_max_drawdown_pct"],
                "restored_return_pct": restored["restored_return_pct"],
                "restored_max_drawdown_pct": restored["restored_max_drawdown_pct"],
                "restored_basis": restored["restored_basis"],
                "restored_reason": restored["restored_reason"],
                "restored_events": restored["restored_events"],
                "restored_checked_at": now,
            })
            counts["updated"] += 1
    return {**counts, "dry_run": dry_run,
            "engine_version": FUND_NAV_REVIEW_ENGINE_VERSION}
```

实现要求：
- 净值抓取失败向上抛并由 API 记 500；不得在纯读取失败时写入任何行。
- `dry_run` 不落库、不改 `restored_checked_at`。
- 已降级行只有 `force=true` 才重算。

API schema：

```python
class FundNavRestoreBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fund_code: Optional[str] = Field(None, max_length=16)
    anchor_from: Optional[date] = None
    anchor_to: Optional[date] = None
    limit: int = Field(200, ge=1, le=400)
    dry_run: bool = True
    force: bool = False


class FundNavRestoreBackfillResponse(BaseModel):
    scanned: int
    updated: int
    unavailable: int
    skipped: int
    funds: int
    dry_run: bool
    engine_version: str
```

端点路径 `/backfill/restore-metrics`，响应模型、鉴权、`api_error` 复用现有写法。

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_restore_backfill.py tests/test_fund_nav_review_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/services/fund_nav_review_service.py api/v1/schemas/fund_nav_reviews.py api/v1/endpoints/fund_nav_reviews.py tests/test_fund_nav_restore_backfill.py tests/test_fund_nav_review_api.py
git commit -m "feat(funds): backfill restored NAV metrics"
```

---

### Task 6: 可配置的每日自动回填

**Files:**
- Modify: `src/config.py`
- Modify: `src/services/runtime_scheduler.py`
- Modify: `.env.example`
- Test: `tests/test_runtime_scheduler_fund_review_tasks.py`

**Interfaces:**
- Produces: `Config.fund_nav_review_auto_restore_enabled: bool = False`；任务名 `fund_nav_review_restore_backfill`。

- [ ] **Step 1: 写失败测试**

```python
def test_restore_backfill_task_disabled_by_default():
    scheduler = RuntimeScheduler({"fund_nav_review_auto_restore_enabled": False})
    names = [task["name"] for task in scheduler._current_background_tasks(config)]
    assert "fund_nav_review_restore_backfill" not in names


def test_restore_backfill_task_registered_when_enabled(monkeypatch):
    called = {}
    def fake_backfill(self, **kwargs):
        called.update(kwargs)
        return {"updated": 0}
    monkeypatch.setattr(
        "src.services.fund_nav_review_service.FundNavReviewService.backfill_restored_metrics",
        fake_backfill,
    )
    tasks = scheduler._current_background_tasks(config)
    task = next(t for t in tasks if t["name"] == "fund_nav_review_restore_backfill")
    task["task"]()
    assert called["dry_run"] is False
```

（按现有 scheduler 测试的构造方式调整。）

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_runtime_scheduler_fund_review_tasks.py -k restore -v`
Expected: FAIL

- [ ] **Step 3: 实现**

- `Config` 增加字段 `fund_nav_review_auto_restore_enabled: bool = False`。
- `Config.from_env` / 现有解析处：

```python
fund_nav_review_auto_restore_enabled=(
    os.getenv('FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED', 'false').lower() == 'true'
),
```

- `runtime_scheduler._current_background_tasks` 追加 `self._current_fund_nav_restore_background_tasks(config)`。
- 新方法复制现有 fund eval 任务模式，任务名 `fund_nav_review_restore_backfill`，`interval_seconds = 86400`，`run_immediately` 沿用注册缓存；任务体：

```python
def fund_nav_restore_task() -> None:
    try:
        from src.services.fund_nav_review_service import FundNavReviewService
        FundNavReviewService().backfill_restored_metrics(
            dry_run=False, limit=MAX_ROWS_PER_RUN,
        )
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("fund nav review restore backfill failed: %s", exc)
```

- `.env.example` 在 `FUND_NAV_REVIEW_AUTO_EVAL_ENABLED` 后追加：

```text
# 是否启用基金复权指标每日自动回填（默认关闭；开启后会联网）
# FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED=false
```

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_runtime_scheduler_fund_review_tasks.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/config.py src/services/runtime_scheduler.py .env.example tests/test_runtime_scheduler_fund_review_tasks.py
git commit -m "feat(funds): add optional daily restore-metrics backfill"
```

---

### Task 7: 统计与 API 字段

**Files:**
- Modify: `src/services/fund_nav_review_service.py`（`_aggregate`、`_aggregate_bucket`、`_to_item`）
- Modify: `api/v1/schemas/fund_nav_reviews.py`
- Test: `tests/test_fund_nav_review_api.py`、`tests/test_fund_nav_review_service.py`

**Interfaces:**
- Produces: item 与 stats 的新增字段（见 spec 8.1），`restored_bases` 计数。

- [ ] **Step 1: 写失败测试**

```python
def test_stats_expose_restored_metrics_alongside_unit_metrics(client, seeded_reviews):
    body = client.get("/api/v1/fund-nav-reviews/stats").json()
    assert "avg_restored_return_pct" in body
    assert "avg_nav_return_pct" in body
    assert set(body["restored_bases"]) <= {"dividend_reinvested", "acc_nav", "unit_nav"}
    bucket = body["breakdowns"]["horizon"][0]
    assert "restored_positive_rate_pct" in bucket
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_review_api.py -k restored -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`_aggregate` 追加：

```python
restored = [row.restored_return_pct for row in evaluated if row.restored_return_pct is not None]
return {
    **existing_payload,
    "restored_evaluated": len(restored),
    "restored_positive_rate_pct": (
        round(sum(1 for value in restored if value > 0) / len(restored) * 100.0, 4)
        if restored else None
    ),
    "avg_restored_return_pct": _mean(restored),
    "avg_restored_max_drawdown_pct": _mean(
        row.restored_max_drawdown_pct for row in evaluated
        if row.restored_max_drawdown_pct is not None
    ),
    "restored_bases": dict(Counter(
        row.restored_basis for row in evaluated if row.restored_basis
    )),
}
```

`FundNavReviewItem` 追加 spec 中的 9 个字段；`FundNavReviewStatsBucket` 与 `FundNavReviewStatsResponse` 追加上述 5 个字段（`restored_bases: Dict[str,int]`）。`_to_item` 同步返回。

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_review_api.py tests/test_fund_nav_review_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/services/fund_nav_review_service.py api/v1/schemas/fund_nav_reviews.py tests/test_fund_nav_review_api.py tests/test_fund_nav_review_service.py
git commit -m "feat(funds): expose restored metrics in review list and stats"
```

---

### Task 8: Web 双口径切换

**Files:**
- Modify: `apps/dsa-web/src/types/fundNavReviews.ts`
- Modify: `apps/dsa-web/src/components/portfolio/FundNavReviewPanel.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`
- Test: `apps/dsa-web/src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx`

**Interfaces:**
- Consumes: API 新字段（Task 7）。
- Produces: 面板口径状态 `basis: 'restored' | 'unit'`，默认 `restored`。

- [ ] **Step 1: 写失败测试**

```tsx
it('defaults to restored basis and switches to unit values', async () => {
  renderPanel();
  fireEvent.click(screen.getByRole('button', { name: /展开/ }));
  expect(await screen.findByText('+10.00%')).toBeInTheDocument(); // restored
  fireEvent.click(screen.getByRole('button', { name: '单位' }));
  expect(screen.getByText('-1.00%')).toBeInTheDocument(); // unit
});

it('shows the actual basis badge per row', async () => {
  renderPanel();
  fireEvent.click(screen.getByRole('button', { name: /展开/ }));
  expect(await screen.findByText('累计')).toBeInTheDocument();
});
```

（复用现有 mock 数据与 `renderPanel` helper，按实际实现调整。）

- [ ] **Step 2: 运行确认失败**

Run（在 `apps/dsa-web`）: `npx vitest run src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

- 类型追加：

```ts
restored_return_pct?: number | null;
restored_max_drawdown_pct?: number | null;
restored_basis?: 'dividend_reinvested' | 'acc_nav' | 'unit_nav' | null;
restored_reason?: string | null;
restored_events?: number | null;
anchor_acc_nav?: number | null;
eval_acc_nav?: number | null;
acc_nav_return_pct?: number | null;
acc_max_drawdown_pct?: number | null;
```

- 面板增加 `const [basis, setBasis] = useState<'restored' | 'unit'>('restored');`。
- 统计卡取值：`basis === 'restored'` 用 `avg_restored_return_pct` / `avg_restored_max_drawdown_pct` / `restored_positive_rate_pct` / `restored_evaluated`；否则用旧字段。
- 表格收益/回撤：restored 用 `restored_*`，unit 用 `nav_return_pct` / `max_drawdown_pct`。
- 每行徽标：`dividend_reinvested → t('fundReviews.basis.dividendReinvested')`，`acc_nav → t('fundReviews.basis.accNav')`，`unit_nav → t('fundReviews.basis.unitNav')`。
- 口径切换按钮组带 `aria-pressed`。
- i18n zh/en 增加：

```ts
'fundReviews.basis.label': '口径',
'fundReviews.basis.restored': '复权',
'fundReviews.basis.unit': '单位',
'fundReviews.basis.dividendReinvested': '复权',
'fundReviews.basis.accNav': '累计',
'fundReviews.basis.unitNav': '单位',
'fundReviews.basis.note': '复权按除息日净值再投资；累计净值按分红现金加回；均不可用时回退单位净值。',
```

英文对应翻译同步。

- [ ] **Step 4: 运行通过**

Run: `npx vitest run src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx tests/ui_governance.test.ts`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add apps/dsa-web/src/types/fundNavReviews.ts apps/dsa-web/src/components/portfolio/FundNavReviewPanel.tsx apps/dsa-web/src/i18n/uiText.ts apps/dsa-web/src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx
git commit -m "feat(web): switch fund review panel between restored and unit bases"
```

---

### Task 9: 文档、API 契约与全量验证

**Files:**
- Modify: `docs/fund-nav-review.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/architecture/api_spec.json`（重新生成）
- Modify: `docs/full-guide.md`、`docs/full-guide_EN.md`（新配置）
- Modify: `docs/INDEX.md`、`docs/INDEX_EN.md`（描述补充）

- [ ] **Step 1: 更新文档**

`docs/fund-nav-review.md` 增加「复权口径与回退链」「restore-metrics 回填」「`FUND_NAV_REVIEW_AUTO_RESTORE_ENABLED`」章节；明确累计净值非再投资、货币基金等边界。

`docs/CHANGELOG.md` `[Unreleased]` 追加一行：

```markdown
- [改进] 基金净值复盘支持分红再投资复权口径，按「复权 → 累计净值 → 单位净值」回退并标注实际口径；新增存量复权指标回填接口与默认关闭的每日自动回填
```

- [ ] **Step 2: 重新生成 API 契约并核对**

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from server import create_app
spec = create_app().openapi()
Path("docs/architecture/api_spec.json").write_text(
    json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY
git diff --stat docs/architecture/api_spec.json
```

Expected: `api_spec.json` 包含 `/api/v1/fund-nav-reviews/backfill/restore-metrics` 与新字段。

- [ ] **Step 3: 后端全量验证**

```bash
uv run python -m pytest tests/test_fund_fetcher.py tests/test_fund_nav_restore.py tests/test_fund_nav_review_store.py tests/test_fund_nav_review_service.py tests/test_fund_nav_restore_backfill.py tests/test_fund_nav_review_api.py tests/test_runtime_scheduler_fund_review_tasks.py tests/test_runtime_scheduler_service.py -q
PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh
```

Expected: 全绿；若出现已知基线失败，先按仓库记忆排除环境因素再归因。

- [ ] **Step 4: Web 全量验证**

```bash
cd apps/dsa-web
npx tsc -b
npm run lint
npx vitest run
npm run build
```

Expected: tsc 0 error、lint 0 error、vitest 全绿、build 成功。

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add docs .env.example
git commit -m "docs: document fund review restored basis and backfill"
```
