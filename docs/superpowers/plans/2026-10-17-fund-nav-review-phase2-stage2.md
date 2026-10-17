# 基金净值复盘二期 · 阶段 2（区间走势与基金报告入口）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供区间净值查询 API、单位/累计/复权走势图，并在单只基金报告页展示复盘摘要。

**Architecture:** 复用阶段 1 的复权算法，把「事件 + 净值区间」暴露为只读 API；Web 用已有 `recharts` 画图并按需加载；基金报告分支挂载紧凑摘要组件，复用现有复盘 list 接口。

**Tech Stack:** FastAPI / SQLAlchemy / pytest；React + TypeScript + `recharts` + vitest。

**Spec:** `docs/superpowers/specs/2026-10-17-fund-nav-review-phase2-design.md`

**依赖:** 阶段 1 已完成（`restored_*` 字段、`compute_restored_metrics`、`get_distribution_events`）。

## Global Constraints

- 未经明确确认不执行 `git commit`、`git tag`、`git push`。
- commit message 使用英文，不添加 `Co-Authored-By`。
- 区间接口只读，不写台账、不触发评估、不联网以外的副作用。
- Web 不使用原生 `title`；中英 i18n key 必须同时补齐。
- 图表组件必须懒加载，避免进入首屏 bundle。
- 验证命令与阶段 1 相同；Web 命令在 `apps/dsa-web` 内执行。

---

### Task 1: 复权序列公开函数

**Files:**
- Modify: `src/services/fund_nav_restore.py`
- Test: `tests/test_fund_nav_restore.py`

**Interfaces:**
- Produces: `compute_adjusted_series(navs, start_idx, events) -> Optional[List[float]]`，与 `_restored_series` 同算法，因子在 `navs[start_idx]` 处为 1，事件区间为 `(start_date, last_date]`。

- [ ] **Step 1: 写失败测试**

```python
def test_compute_adjusted_series_applies_events_after_start():
    navs = [_nav("2025-01-02", 1.00, 1.10), _nav("2025-01-03", 0.90, 1.10), _nav("2025-01-06", 0.99, 1.10)]
    events = FundDistributionEvents(dividends=[FundDividendEvent("2025-01-03", 0.10)])
    series = compute_adjusted_series(navs, 0, events)
    assert series is not None
    assert series[0] == 1.00
    assert series[1] == 1.00  # 0.90 * (1 + 0.10/0.90)
    assert series[2] == 1.10
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_restore.py -k adjusted_series -v`
Expected: FAIL

- [ ] **Step 3: 实现**

把现有 `_restored_series` 重命名为公开的 `compute_adjusted_series(navs, anchor_idx, target_idx, events)` 并保留内部包装；或新增：

```python
def compute_adjusted_series(navs, start_idx, events):
    return _restored_series(navs, start_idx, len(navs) - 1, events)
```

阶段 1 的 `compute_restored_metrics` 改用公开函数，行为不变。

- [ ] **Step 4: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_restore.py -q`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add src/services/fund_nav_restore.py tests/test_fund_nav_restore.py
git commit -m "refactor(funds): expose adjusted NAV series helper"
```

---

### Task 2: 区间净值 API

**Files:**
- Modify: `src/services/fund_nav_review_service.py`
- Modify: `api/v1/schemas/fund_nav_reviews.py`
- Modify: `api/v1/endpoints/fund_nav_reviews.py`
- Test: `tests/test_fund_nav_review_api.py`

**Interfaces:**
- Produces:
  - `FundNavReviewService.get_nav_range(*, fund_code, start_date, end_date) -> dict`
  - `GET /api/v1/fund-nav-reviews/nav-range`
  - Schemas `FundNavRangeItem`、`FundNavRangeResponse`

- [ ] **Step 1: 写失败测试**

```python
def test_nav_range_returns_ordered_records_with_adjusted_nav(client, monkeypatch):
    class FakeFetcher:
        def get_nav_range(self, code, start, end):
            return [
                NavRecord("2025-01-02", 1.00, 1.10, 0.0),
                NavRecord("2025-01-03", 0.90, 1.10, -10.0),
                NavRecord("2025-01-06", 0.99, 1.10, 10.0),
            ]
        def get_distribution_events(self, code):
            return FundDistributionEvents(
                dividends=[FundDividendEvent("2025-01-03", 0.10)]
            )
    monkeypatch.setattr(
        "src.services.fund_nav_review_service.FundFetcher", lambda: FakeFetcher()
    )
    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={"fund_code": "fund:003095", "start_date": "2025-01-01", "end_date": "2025-01-31"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["adjustment_status"] == "available"
    assert [item["date"] for item in body["items"]] == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert body["items"][1]["adjusted_nav"] == 1.00


def test_nav_range_rejects_too_wide_window(client):
    response = client.get(
        "/api/v1/fund-nav-reviews/nav-range",
        params={"fund_code": "003095", "start_date": "2020-01-01", "end_date": "2026-01-01"},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest tests/test_fund_nav_review_api.py -k nav_range -v`
Expected: FAIL

- [ ] **Step 3: 实现服务**

```python
MAX_NAV_RANGE_DAYS = 1100
MAX_NAV_RANGE_RECORDS = 1000


def get_nav_range(self, *, fund_code: str, start_date: date, end_date: date) -> Dict[str, Any]:
    code = _normalize_fund_code(fund_code)
    if not code:
        raise ValueError("fund_code must be a 6-digit fund code")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if (end_date - start_date).days > MAX_NAV_RANGE_DAYS:
        raise ValueError(f"date range must not exceed {MAX_NAV_RANGE_DAYS} days")
    navs = self.fetcher.get_nav_range(code, start_date, end_date)
    navs = sorted(navs, key=lambda record: (record.date or "")[:10])
    truncated = len(navs) > MAX_NAV_RANGE_RECORDS
    navs = navs[:MAX_NAV_RANGE_RECORDS]
    try:
        events = self.fetcher.get_distribution_events(code)
        adjustment_status = "available"
        event_counts = {"dividends": len(events.dividends), "splits": len(events.splits)}
        adjusted = compute_adjusted_series(navs, 0, events) if navs else []
    except Exception as exc:
        logger.warning("[%s] 基金分红送配抓取失败: %s", code, exc)
        adjustment_status = "unavailable"
        event_counts = {"dividends": 0, "splits": 0}
        adjusted = None
    if navs and adjusted is None:
        adjustment_status = "unavailable"
    items = []
    for index, record in enumerate(navs):
        items.append({
            "date": record.date,
            "unit_nav": record.unit_nav,
            "acc_nav": record.acc_nav,
            "adjusted_nav": adjusted[index] if adjusted else None,
            "change_pct": record.change_pct,
        })
    return {
        "fund_code": code,
        "start_date": start_date,
        "end_date": end_date,
        "adjustment_status": adjustment_status,
        "truncated": truncated,
        "events": event_counts,
        "items": items,
    }
```

实现注意：
- `compute_adjusted_series` 返回 `None` 时 `adjusted_nav` 全为 null，`adjustment_status` 置 `unavailable`。
- 空区间返回 `items=[]`、`adjustment_status="available"`。
- 不写数据库。

- [ ] **Step 4: 实现 schema 与端点**

```python
class FundNavRangeItem(BaseModel):
    date: str
    unit_nav: float
    acc_nav: float
    adjusted_nav: Optional[float] = None
    change_pct: float


class FundNavRangeResponse(BaseModel):
    fund_code: str
    start_date: date
    end_date: date
    adjustment_status: Literal["available", "unavailable"]
    truncated: bool
    events: Dict[str, int]
    items: List[FundNavRangeItem] = Field(default_factory=list)
```

端点 `GET /nav-range`，参数 `fund_code: str = Query(..., max_length=16)`、`start_date: date`、`end_date: date`，鉴权与 `api_error` 复用现有写法；`ValueError → 400`，其他 → 500。

- [ ] **Step 5: 运行通过**

Run: `uv run python -m pytest tests/test_fund_nav_review_api.py -q`
Expected: PASS

- [ ] **Step 6: 提交（需用户确认）**

```bash
git add src/services/fund_nav_review_service.py api/v1/schemas/fund_nav_reviews.py api/v1/endpoints/fund_nav_reviews.py tests/test_fund_nav_review_api.py
git commit -m "feat(funds): add NAV range API with adjusted series"
```

---

### Task 3: Web API 客户端与类型

**Files:**
- Modify: `apps/dsa-web/src/types/fundNavReviews.ts`
- Modify: `apps/dsa-web/src/api/fundNavReviews.ts`
- Test: `apps/dsa-web/src/api/__tests__/fundNavReviews.test.ts`

**Interfaces:**
- Produces: `FundNavRangeItem`、`FundNavRangeResponse`、`fundNavReviewsApi.getNavRange(params)`。

- [ ] **Step 1: 写失败测试**

```ts
it('serializes nav range params and camelizes the response', async () => {
  get.mockResolvedValue({
    data: {
      fund_code: '003095',
      start_date: '2025-01-01',
      end_date: '2025-01-31',
      adjustment_status: 'available',
      truncated: false,
      events: { dividends: 1, splits: 0 },
      items: [{ date: '2025-01-02', unit_nav: 1, acc_nav: 1.1, adjusted_nav: 1, change_pct: 0 }],
    },
  });
  const result = await fundNavReviewsApi.getNavRange({
    fund_code: '003095',
    start_date: '2025-01-01',
    end_date: '2025-01-31',
  });
  expect(get).toHaveBeenCalledWith('/fund-nav-reviews/nav-range', {
    params: { fund_code: '003095', start_date: '2025-01-01', end_date: '2025-01-31' },
  });
  expect(result.adjustmentStatus).toBe('available');
  expect(result.items[0].adjustedNav).toBe(1);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/api/__tests__/fundNavReviews.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

类型：

```ts
export interface FundNavRangeItem {
  date: string;
  unitNav: number;
  accNav: number;
  adjustedNav: number | null;
  changePct: number;
}

export interface FundNavRangeResponse {
  fundCode: string;
  startDate: string;
  endDate: string;
  adjustmentStatus: 'available' | 'unavailable';
  truncated: boolean;
  events: { dividends: number; splits: number };
  items: FundNavRangeItem[];
}
```

API：

```ts
getNavRange: async (params: {
  fund_code: string;
  start_date: string;
  end_date: string;
}): Promise<FundNavRangeResponse> => {
  const response = await apiClient.get('/fund-nav-reviews/nav-range', { params });
  return toCamelCase<FundNavRangeResponse>(response.data);
},
```

- [ ] **Step 4: 运行通过**

Run: `npx vitest run src/api/__tests__/fundNavReviews.test.ts`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add apps/dsa-web/src/types/fundNavReviews.ts apps/dsa-web/src/api/fundNavReviews.ts apps/dsa-web/src/api/__tests__/fundNavReviews.test.ts
git commit -m "feat(web): add fund NAV range API client"
```

---

### Task 4: 净值走势图组件

**Files:**
- Create: `apps/dsa-web/src/components/portfolio/FundNavTrendChart.tsx`
- Test: `apps/dsa-web/src/components/portfolio/__tests__/FundNavTrendChart.test.tsx`

**Interfaces:**
- Consumes: `FundNavRangeResponse`（Task 3）。
- Produces: `<FundNavTrendChart fundCode startDate endDate height? />`，内部按需加载 recharts。

- [ ] **Step 1: 写失败测试**

```tsx
it('renders a chart with unit, acc and adjusted series', async () => {
  render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);
  expect(await screen.findByLabelText('净值走势')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复权' })).toHaveAttribute('aria-pressed', 'true');
});

it('shows an empty state when the range has no records', async () => {
  mockGetNavRange.mockResolvedValue({ ...emptyResponse });
  render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);
  expect(await screen.findByText(/暂无净值记录/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/portfolio/__tests__/FundNavTrendChart.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

组件结构：

```tsx
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { fundNavReviewsApi } from '../../api/fundNavReviews';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ApiErrorAlert, Loading } from '../common';

const RechartsLine = lazy(() => import('./FundNavTrendChartImpl'));

export function FundNavTrendChart(props: {
  fundCode: string;
  startDate: string;
  endDate: string;
  height?: number;
}) {
  const { t } = useUiLanguage();
  const [basis, setBasis] = useState<'adjusted' | 'unit' | 'acc'>('adjusted');
  const [data, setData] = useState<FundNavRangeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);
  // useEffect: increment requestId, call getNavRange, discard stale responses
  return (
    <section aria-label={t('fundReviews.trend.title')}>
      {/* 口径按钮组：复权 / 单位 / 累计，aria-pressed */}
      {/* loading / error+retry / empty / <Suspense fallback={<Loading/>}><RechartsLine .../></Suspense> */}
    </section>
  );
}
```

`FundNavTrendChartImpl.tsx` 只负责 recharts 渲染：`ResponsiveContainer` + `LineChart` + `XAxis dataKey="date"` + `YAxis domain={['auto','auto']}` + `Tooltip` + `Line type="monotone" dataKey={basis} dot={false} isAnimationActive={false}`。把 `items` 映射为 `{ date, unit: unitNav, acc: accNav, adjusted: adjustedNav }`，缺失值用 `connectNulls={false}`。

- [ ] **Step 4: i18n 与测试通过**

新增 key（zh/en）：`fundReviews.trend.title`、`fundReviews.trend.empty`、`fundReviews.trend.loadFailed`、`fundReviews.trend.adjusted/unit/acc`、`fundReviews.trend.retry`。

Run: `npx vitest run src/components/portfolio/__tests__/FundNavTrendChart.test.tsx tests/ui_governance.test.ts`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add apps/dsa-web/src/components/portfolio/FundNavTrendChart.tsx apps/dsa-web/src/components/portfolio/FundNavTrendChartImpl.tsx apps/dsa-web/src/components/portfolio/__tests__/FundNavTrendChart.test.tsx apps/dsa-web/src/i18n/uiText.ts
git commit -m "feat(web): add lazily loaded fund NAV trend chart"
```

---

### Task 5: 组合页面板接入走势图

**Files:**
- Modify: `apps/dsa-web/src/components/portfolio/FundNavReviewPanel.tsx`
- Test: `apps/dsa-web/src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx`

**Interfaces:**
- Consumes: `FundNavTrendChart`（Task 4）。
- Produces: 面板顶部「近一年走势」区域；默认区间为最近 365 天，切换基金时只展示当前选中基金（无选中时不渲染）。

- [ ] **Step 1: 写失败测试**

```tsx
it('mounts the trend chart only when a fund is selected', async () => {
  renderPanel();
  fireEvent.click(screen.getByRole('button', { name: /展开/ }));
  expect(screen.queryByLabelText('净值走势')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('基金代码'), { target: { value: '003095' } });
  expect(await screen.findByLabelText('净值走势')).toBeInTheDocument();
});
```

（若面板没有基金代码输入，则用台账第一行点击选中；按实际交互调整。）

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx -t trend`
Expected: FAIL

- [ ] **Step 3: 实现**

- 面板记录 `selectedFundCode`（点击台账行或过滤框变化时更新）。
- 渲染：

```tsx
{selectedFundCode && (
  <FundNavTrendChart
    fundCode={selectedFundCode}
    startDate={oneYearAgo()}
    endDate={today()}
  />
)}
```

- 日期用本地 `date` 格式化为 `YYYY-MM-DD`，不写死年份。
- 图表按需加载，不阻塞台账数据。

- [ ] **Step 4: 运行通过**

Run: `npx vitest run src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add apps/dsa-web/src/components/portfolio/FundNavReviewPanel.tsx apps/dsa-web/src/components/portfolio/__tests__/FundNavReviewPanel.test.tsx
git commit -m "feat(web): show NAV trend chart in fund review panel"
```

---

### Task 6: 基金报告页复盘摘要

**Files:**
- Create: `apps/dsa-web/src/components/report/FundNavReviewSummary.tsx`
- Modify: `apps/dsa-web/src/components/report/ReportSummary.tsx`
- Test: `apps/dsa-web/src/components/report/__tests__/FundNavReviewSummary.test.tsx`、`apps/dsa-web/src/components/report/__tests__/ReportSummary.test.tsx`

**Interfaces:**
- Consumes: `fundNavReviewsApi.list`（现有）。
- Produces: `<FundNavReviewSummary fundCode="fund:003095" />`，非 fund 代码不请求。

- [ ] **Step 1: 写失败测试**

```tsx
it('loads recent reviews for a fund code', async () => {
  render(<FundNavReviewSummary fundCode="fund:003095" />);
  expect(await screen.findByText('净值复盘')).toBeInTheDocument();
  expect(mockList).toHaveBeenCalledWith(expect.objectContaining({ fund_code: '003095', page_size: 5 }));
});

it('does not fetch for a stock code', () => {
  render(<FundNavReviewSummary fundCode="600519" />);
  expect(mockList).not.toHaveBeenCalled();
});
```

`ReportSummary.test.tsx` 增加：fund 报告渲染 `净值复盘` 区块，非 fund 报告不渲染。

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/report/__tests__/FundNavReviewSummary.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

组件：

- `isFund = /^fund:/i.test(fundCode)`；非 fund 返回 `null` 且不请求。
- 进入时 `list({ fund_code: stripPrefix(fundCode), page_size: 5, page: 1 })`，用 `requestIdRef` 丢弃过期响应。
- 渲染：标题「净值复盘」、最近 5 条（锚定日 / 周期 / 区间收益 / 回撤 / 实际口径徽标）、空态说明、错误重试。
- 收益优先 `restored_return_pct`，回撤优先 `restored_max_drawdown_pct`；缺失时显示 `-`。
- 不使用原生 `title`；i18n 复用 `fundReviews.*` 并补充 `fundReviews.report.title`、`fundReviews.report.empty`。

`ReportSummary` fund 分支在 `FundMetricsCard` 之后插入：

```tsx
<FundNavReviewSummary fundCode={meta.stockCode} />
```

- [ ] **Step 4: 运行通过**

Run: `npx vitest run src/components/report/__tests__/FundNavReviewSummary.test.tsx src/components/report/__tests__/ReportSummary.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add apps/dsa-web/src/components/report/FundNavReviewSummary.tsx apps/dsa-web/src/components/report/ReportSummary.tsx apps/dsa-web/src/components/report/__tests__/FundNavReviewSummary.test.tsx apps/dsa-web/src/components/report/__tests__/ReportSummary.test.tsx apps/dsa-web/src/i18n/uiText.ts
git commit -m "feat(web): add fund review summary to fund reports"
```

---

### Task 7: 文档、API 契约与全量验证

**Files:**
- Modify: `docs/fund-nav-review.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/architecture/api_spec.json`（重新生成）
- Modify: `docs/INDEX.md`、`docs/INDEX_EN.md`

- [ ] **Step 1: 更新文档**

- `docs/fund-nav-review.md` 增加「区间净值 API」章节：参数、1100 天限制、1000 条截断、`adjusted_nav` 口径、只读语义。
- 「Web 面板」章节补充走势图与基金报告摘要入口。
- `docs/CHANGELOG.md` `[Unreleased]` 追加：

```markdown
- [新功能] 基金净值复盘新增区间净值查询接口与单位/累计/复权走势图，并在单只基金报告页展示复盘摘要
```

- [ ] **Step 2: 重新生成 API 契约**

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from server import create_app
Path("docs/architecture/api_spec.json").write_text(
    json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
```

核对 `/api/v1/fund-nav-reviews/nav-range` 与 `FundNavRangeItem` 字段。

- [ ] **Step 3: 后端全量验证**

```bash
uv run python -m pytest tests/test_fund_nav_restore.py tests/test_fund_nav_review_service.py tests/test_fund_nav_review_api.py -q
PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh
```

- [ ] **Step 4: Web 全量验证**

```bash
cd apps/dsa-web
npx tsc -b
npm run lint
npx vitest run
npm run build
```

Expected: 全部通过；构建产物确认图表 chunk 未进入首屏入口。

- [ ] **Step 5: 提交（需用户确认）**

```bash
git add docs
git commit -m "docs: document fund NAV range API and trend chart"
```
