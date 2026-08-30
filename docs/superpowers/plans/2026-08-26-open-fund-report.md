# 场外基金支持 — 子项目 A：单基金分析报告 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 输入场外基金代码（显式后缀如 `006229.FUND`）→ 产出单基金 LLM 分析报告，复用现有编排/LLM/通知/历史/渲染骨架，仅在领域层新增（数据源、识别、Schema、Prompt），不动股票链路。

**Architecture:** 场外基金作为独立"资产类别=fund"并行链路：`analyze_stock` 入口按代码后缀分流到 `_analyze_fund_stock`；数据走独立 `FundDataProvider`（akshare 净值/持仓接口，不进证券 DataFetcherManager failover 循环）；报告用独立 `FundReportSchema` 与基金 Prompt；前端 `ReportType` 加 `'fund'` + 新增 `FundReportView` 按 `meta.reportType` 分派。

**Tech Stack:** Python 3 / FastAPI / pydantic v2 / akshare(已依赖, >=1.12.0) / pandas / React + TS + vite

## Global Constraints

- 遵循仓库 `AGENTS.md`：**未经明确确认不执行 `git commit`**；commit message 用英文、不加 `Co-Authored-By`。计划内每个 commit 步骤在执行时都需先获用户确认（或获一次性授权）。
- 不写死密钥/账号/路径/模型名/端口；新增配置同步 `.env.example`。
- 股票链路保持零行为变化；不引入无关重构。
- 复用 akshare，**不新增第三方依赖**。
- 后端验证：`uv run python -m pytest -m "not network"` + `python -m py_compile <changed>`；前端：`cd apps/dsa-web && npm run lint && npm run build`。
- 报告格式/渲染变化时，PR 附受影响报告页面截图或说明原因。
- 所有新文件遵循既有目录边界：`src/services/`、`src/schemas/`、`data_provider/`、`apps/dsa-web/src/`。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `src/config.py` | 修改 | 新增 `fund_support` 布尔开关 |
| `.env.example` | 修改 | 增加 `FUND_SUPPORT` 注释 |
| `src/services/fund_data_provider.py` | 新增 | 场外基金净值/资料/持仓数据源（akshare）+ 代码识别 |
| `src/schemas/fund_report_schema.py` | 新增 | 基金分析报告 Pydantic 模型 |
| `src/analyzer.py` | 修改 | 新增基金 LLM Prompt 常量与分析入口分支 |
| `src/services/analysis_service.py` | 修改 | `analyze_stock` 入口按基金分流 + `_analyze_fund_stock` |
| `src/services/stock_list_parser.py` | 修改 | `ParseStatus` 加 `FUND`；`parse_analysis_target` 识别基金后缀 |
| `apps/dsa-web/src/types/analysis.ts` | 修改 | `ReportType` 加 `'fund'`；基金报告 payload 类型 |
| `apps/dsa-web/src/components/report/FundReportView.tsx` | 新增 | 基金报告展示组件 |
| `apps/dsa-web/src/components/report/ReportSummary.tsx` | 修改 | 按 `meta.reportType === 'fund'` 分派到 FundReportView |
| `apps/dsa-web/src/components/report/HomeReportRegion.tsx` | 修改 | 基金历史报告的按钮分派分支 |
| `tests/test_fund_identify.py` | 新增 | 基金代码识别/路由单测 |
| `tests/test_fund_data_provider.py` | 新增 | 净值/持仓解析单测 |
| `tests/test_fund_report.py` | 新增 | 基金分析编排单测（mock 数据源） |
| `docs/CHANGELOG.md` | 修改 | `[Unreleased]` 增加条目 |

---

## Task 1: 新增 FUND_SUPPORT 配置开关

**Files:**
- Modify: `src/config.py`（dataclass `Config` 字段区 + `_load_from_env`）
- Modify: `.env.example`

**Interfaces:**
- Produces: `get_config().fund_support: bool`（默认 `False`）

- [ ] **Step 1: 加字段与 env 解析**

在 `src/config.py` 的 `Config` dataclass 内，仿照 `enable_chip_distribution: bool = True`（L1210）附近新增：

```python
# 是否启用场外基金分析链路（默认关闭；开启后支持 .FUND 后缀的场外基金代码）
fund_support: bool = False
```

在 `_load_from_env()`（L1367）内，仿照现有 `parse_env_bool(os.getenv(...), default=...)` 用法新增：

```python
fund_support=parse_env_bool(os.getenv("FUND_SUPPORT"), default=False),
```

- [ ] **Step 2: 更新 .env.example**

在 `.env.example` 与现有布尔配置相邻处加注释行（格式与现有一致）：

```
# 启用场外基金分析链路（支持 .FUND 后缀代码；关闭时仅股票证券）  默认 false
FUND_SUPPORT=false
```

- [ ] **Step 3: 验证导入通过**

Run: `uv run python -c "from src.config import get_config; print(get_config().fund_support)"`
Expected: 输出 `False`（默认）

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add src/config.py .env.example
git commit -m "feat: add FUND_SUPPORT config switch for off-market fund analysis"
```

---

## Task 2: 基金代码识别（数据源侧）

**Files:**
- Create: `src/services/fund_data_provider.py`
- Test: `tests/test_fund_identify.py`

**Interfaces:**
- Produces: `is_fund_code(code: str) -> bool` — 仅当带显式 `.FUND`/`.OTC` 后缀返回 True；裸码一律 False（保持证券语义）。`strip_fund_suffix(code: str) -> str` — 去掉后缀返回 6 位数字。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fund_identify.py
from src.services.fund_data_provider import is_fund_code, strip_fund_suffix

def test_fund_suffix_recognized():
    assert is_fund_code("006229.FUND") is True
    assert is_fund_code("006229.OTC") is True

def test_bare_code_is_not_fund():
    assert is_fund_code("006229") is False      # 裸码保持证券语义
    assert is_fund_code("600519.SH") is False   # 显式证券后缀
    assert is_fund_code("00878.TW") is False    # ETF 后缀

def test_strip_fund_suffix():
    assert strip_fund_suffix("006229.FUND") == "006229"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_identify.py -v`
Expected: FAIL（`ModuleNotFoundError` / 函数不存在）

- [ ] **Step 3: 实现识别函数**

```python
# src/services/fund_data_provider.py
_FUND_SUFFIXES = ("FUND", "OTC")

def is_fund_code(code: str) -> bool:
    """显式后缀才判定为场外基金；裸码/证券后缀保持原语义，避免误判。
    场外基金 6 位代码与 A 股证券/ETF 存在天然歧义，不做纯数字推断。"""
    if not code:
        return False
    upper = code.strip().upper()
    for suffix in _FUND_SUFFIXES:
        if upper.endswith("." + suffix) or upper.endswith("_" + suffix):
            return True
    return False

def strip_fund_suffix(code: str) -> str:
    """返回去掉基金后缀后的 6 位数字代码。"""
    upper = code.strip().upper()
    for suffix in _FUND_SUFFIXES:
        if upper.endswith("." + suffix):
            return code.strip()[: -len("." + suffix)]
        if upper.endswith("_" + suffix):
            return code.strip()[: -len("_" + suffix)]
    return code.strip()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_identify.py -v`
Expected: PASS

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/services/fund_data_provider.py tests/test_fund_identify.py
git commit -m "feat: add off-market fund code identification"
```

---

## Task 3: 基金数据模型 + FundDataProvider（净值/资料/持仓）

**Files:**
- Modify: `src/services/fund_data_provider.py`
- Create: `src/schemas/fund_report_schema.py`（仅数据承载模型，报告模型见 Task 5）
- Test: `tests/test_fund_data_provider.py`

**Interfaces:**
- Consumes: `is_fund_code`（Task 2）
- Produces:
  - `FundNavRow`（pydantic）: `date, unit_nav, daily_growth` (Optional)
  - `FundProfileRow`（pydantic）: `code, name, fund_type, inception_date, manager, scale, fee` (Optional)
  - `FundHoldingRow`（pydantic）: `code, name, ratio, market_value, quarter` (Optional)
  - `FundDataProvider.get_nav_series(code) -> tuple[list[FundNavRow], dict]`（第二项为 data_quality 元数据）
  - `FundDataProvider.get_holdings(code, year) -> list[FundHoldingRow]`
  - `FundDataProvider.stats_from_nav(rows) -> dict`（计算 interval_return / max_drawdown / current_drawdown）

- [ ] **Step 1: 写数据承载模型**

在 `src/schemas/fund_report_schema.py` 顶部新增数据承载模型（与分析模型同文件，分节即可）：

```python
from datetime import date
from typing import List, Optional
from pydantic import BaseModel

class FundNavRow(BaseModel):
    date: date
    unit_nav: Optional[float] = None
    daily_growth: Optional[float] = None

class FundProfileRow(BaseModel):
    code: str
    name: Optional[str] = None
    fund_type: Optional[str] = None
    inception_date: Optional[str] = None
    manager: Optional[str] = None
    scale: Optional[str] = None
    fee: Optional[str] = None

class FundHoldingRow(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    ratio: Optional[float] = None
    market_value: Optional[float] = None
    quarter: Optional[str] = None
```

- [ ] **Step 2: 写解析/统计失败测试**

```python
# tests/test_fund_data_provider.py
from src.services.fund_data_provider import FundDataProvider
from src.schemas.fund_report_schema import FundNavRow

def _assert_close(a, b, tol=1e-4):
    assert abs(a - b) < tol

def test_stats_compute_mdd_and_return():
    rows = [
        FundNavRow(date=date(2024,1,1), unit_nav=1.0, daily_growth=0.0),
        FundNavRow(date=date(2024,1,2), unit_nav=1.1, daily_growth=0.1),
        FundNavRow(date=date(2024,1,3), unit_nav=0.99, daily_growth=-0.1),
        FundNavRow(date=date(2024,1,4), unit_nav=1.2, daily_growth=0.212),
    ]
    stats = FundDataProvider.stats_from_nav(rows)
    _assert_close(stats["interval_return"], 0.2)      # 1.2/1.0 - 1
    _assert_close(stats["max_drawdown"], 0.10)        # 1.1 -> 0.99
    _assert_close(stats["current_drawdown"], 0.0)     # 1.2 为历史新高

def test_parse_nav_dataframe_column_map():
    import pandas as pd
    df = pd.DataFrame({
        "净值日期": ["2024-01-01", "2024-01-02"],
        "单位净值": [1.0, 1.1],
        "日增长率": [0.0, 0.1],
    })
    parsed = FundDataProvider._normalize_nav_df(df)
    assert len(parsed) == 2
    assert parsed[0].unit_nav == 1.0
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/test_fund_data_provider.py -v`
Expected: FAIL（函数/方法不存在）

- [ ] **Step 4: 实现数据 provider 与统计**

```python
# src/services/fund_data_provider.py 追加
import pandas as pd
from datetime import datetime

class FundDataProvider:
    """场外基金数据源：复用 akshare 净值/持仓接口。不进证券 failover 循环。"""

    @staticmethod
    def _normalize_nav_df(df: pd.DataFrame):
        from src.schemas.fund_report_schema import FundNavRow
        rows = []
        for _, r in df.iterrows():
            try:
                d = datetime.strptime(str(r["净值日期"]), "%Y-%m-%d").date()
            except Exception:
                continue
            rows.append(FundNavRow(
                date=d,
                unit_nav=float(r.get("单位净值") or r.get("单位净值估算") or 0) or None,
                daily_growth=float(r.get("日增长率", 0) or 0),
            ))
        rows.sort(key=lambda x: x.date)
        return rows

    def get_nav_series(self, code: str):
        """返回 (rows, data_quality)。data_quality 含 provider/as_of/missing_fields。"""
        import akshare as ak
        code6 = strip_fund_suffix(code)
        df = ak.fund_open_fund_info_em(symbol=code6, indicator="单位净值走势", period="成立来")
        rows = self._normalize_nav_df(df)
        q = {"provider": "akshare", "as_of": df.iloc[-1]["净值日期"] if len(df) else None,
             "missing_fields": []}
        return rows, q

    def get_holdings(self, code: str, year: str = "2025"):
        import akshare as ak
        code6 = strip_fund_suffix(code)
        df = ak.fund_portfolio_hold_em(symbol=code6, date=year)
        from src.schemas.fund_report_schema import FundHoldingRow
        rows = []
        for _, r in df.iterrows():
            rows.append(FundHoldingRow(
                code=str(r.get("股票代码") or ""),
                name=str(r.get("股票名称") or ""),
                ratio=float(r.get("占净值比例", 0) or 0),
                market_value=float(r.get("持仓市值", 0) or 0),
                quarter=str(r.get("季度") or ""),
            ))
        return rows

    @staticmethod
    def stats_from_nav(rows):
        if len(rows) < 2:
            return {"interval_return": None, "max_drawdown": None,
                    "current_drawdown": None, "period_days": len(rows)}
        first = rows[0].unit_nav
        last = rows[-1].unit_nav
        if not first or not last:
            return {"interval_return": None, "max_drawdown": None,
                    "current_drawdown": None, "period_days": len(rows)}
        interval_return = last / first - 1
        peak = -1e18
        max_dd = 0.0
        cur_peak = rows[-1].unit_nav
        cur_dd = 0.0
        for r in rows:
            if r.unit_nav is None:
                continue
            peak = max(peak, r.unit_nav)
            dd = (peak - r.unit_nav) / peak if peak else 0.0
            max_dd = max(max_dd, dd)
            cur_peak = max(cur_peak, r.unit_nav)
            if r.unit_nav:
                cur_dd = (cur_peak - r.unit_nav) / cur_peak
        return {"interval_return": interval_return, "max_drawdown": max_dd,
                "current_drawdown": cur_dd, "period_days": len(rows)}
```

- [ ] **Step 5: 运行确认通过（离线部分）**

Run: `pytest tests/test_fund_data_provider.py -v`
Expected: PASS（get_nav_series 等网络方法不在单测内，仅覆盖 `_normalize_nav_df` 与 `stats_from_nav`）

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add src/services/fund_data_provider.py src/schemas/fund_report_schema.py tests/test_fund_data_provider.py
git commit -m "feat: add fund data provider with nav and holdings parsing"
```

---

## Task 4: 报告 Schema / 解析层识别基金类型

**Files:**
- Modify: `src/services/stock_list_parser.py`
- Test: `tests/test_fund_identify.py`（追加）

**Interfaces:**
- Consumes: `is_fund_code`（Task 2）
- Produces: `ParseStatus.FUND = "fund"`；`parse_analysis_target("006229.FUND").asset_type == "fund"`

- [ ] **Step 1: 追加失败测试**

```python
# tests/test_fund_identify.py 追加
from src.services.stock_list_parser import parse_analysis_target, ParseStatus

def test_fund_suffix_parsed_as_fund():
    t = parse_analysis_target("006229.FUND")
    assert t.asset_type == "fund"

def test_bare_code_stays_stock():
    t = parse_analysis_target("006229")
    assert t.asset_type == "stock"     # 裸码仍按个股契约，不被误判为基金
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_identify.py -v`
Expected: FAIL（`ParseStatus.FUND` 不存在 / asset_type 非 fund）

- [ ] **Step 3: 加 ParseStatus + 识别分支**

在 `src/services/stock_list_parser.py` 的 `ParseStatus`（L77）新增成员：

```python
    FUND = "fund"
```

在 `parse_analysis_target`（L470）入口最前，于现有三条契约之前加基金后缀分支：

```python
    from src.services.fund_data_provider import is_fund_code
    if is_fund_code(raw_input):
        return AnalysisTarget(
            raw_input=raw_input, asset_type=ParseStatus.FUND,
            canonical_id=strip_fund_suffix(raw_input), display_code=raw_input,
            exchange="UNKNOWN", unsupported_reason=None,
            normalized_prefix="", normalized_code=strip_fund_suffix(raw_input),
            matched_index=None,
        )
```

（如需 import `strip_fund_suffix`，一并 import。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_identify.py -v`
Expected: PASS

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/services/stock_list_parser.py tests/test_fund_identify.py
git commit -m "feat: recognize off-market fund asset type in analysis target parser"
```

---

## Task 5: 基金分析报告 Schema + LLM Prompt

**Files:**
- Modify: `src/schemas/fund_report_schema.py`（追加分析模型）
- Modify: `src/analyzer.py`（新增基金 `SYSTEM_PROMPT` 常量 + `_get_fund_system_prompt`）
- Test: `tests/test_fund_report.py`

**Interfaces:**
- Consumes: `FundNavRow/FundProfileRow/FundHoldingRow`（Task 3）
- Produces: `FundReportSchema`（pydantic，`extra="allow"`）；`analyzer` 内 `FUND_SYSTEM_PROMPT` 常量与 `_get_fund_system_prompt(report_language) -> str`

- [ ] **Step 1: 加分析模型**

追加到 `src/schemas/fund_report_schema.py`：

```python
from pydantic import Field, ConfigDict

class FundReportSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    fund_name: Optional[str] = None
    fund_type: Optional[str] = None
    manager: Optional[str] = None
    scale: Optional[str] = None
    inception_date: Optional[str] = None
    interval_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    current_drawdown: Optional[float] = None
    holdings_concentration: Optional[str] = None
    top_holdings: Optional[List[FundHoldingRow]] = None
    analysis_summary: Optional[str] = None
    operation_advice: Optional[str] = None
    risk_warning: Optional[str] = None
    sentiment_score: Optional[int] = Field(default=None, ge=0, le=100)
```

- [ ] **Step 2: 加基金 Prompt 常量**

在 `src/analyzer.py` 中新增（与 `SYSTEM_PROMPT` 同风格，但剔除 MA/筹码/涨跌停/龙虎榜，替换为净值/回撤/持仓语义）：

```python
FUND_SYSTEM_PROMPT_ZH = """你是一位场外基金投资分析师。请基于给定基金净值、回撤、持仓、资料，
输出结构化基金分析 JSON（字段见契约）。基金只有每日净值，无盘中行情；
不得使用股票概念（涨跌停、龙虎榜、北向资金、技术均线位、成交量、筹码分布）。
重点评估：净值走势与区间收益、回撤水平、持仓集中度与重仓股行业、基金经理、规模/费率，
并给出申赎倾向建议（基金是申赎不是买卖）。
输出 JSON 必须包含以下字段：fund_name,fund_type,manager,scale,inception_date,
interval_return,max_drawdown,current_drawdown,holdings_concentration,top_holdings,
analysis_summary,operation_advice,risk_warning,sentiment_score。"""
```

提供取用函数（按语言返回，zh 默认）：

```python
def _get_fund_system_prompt(report_language: str) -> str:
    return FUND_SYSTEM_PROMPT_ZH  # 当前仅中文；en/ko 需扩展
```

- [ ] **Step 3: 写模型校验失败测试**

```python
# tests/test_fund_report.py
from src.schemas.fund_report_schema import FundReportSchema

def test_fund_report_schema_accepts_valid():
    r = FundReportSchema(fund_name="中欧医疗创新股票C", interval_return=0.2,
                         sentiment_score=65)
    assert r.fund_name == "中欧医疗创新股票C"

def test_fund_report_schema_extra_allow():
    r = FundReportSchema(**{"unknown_key": 1})
    assert "unknown_key" in r.model_dump()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_report.py -v`
Expected: PASS（Schema 字段存在且 extra="allow" 生效）

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/schemas/fund_report_schema.py src/analyzer.py tests/test_fund_report.py
git commit -m "feat: add fund report schema and fund analysis prompt"
```

---

## Task 6: 基金分析编排 `_analyze_fund_stock`

**Files:**
- Modify: `src/services/analysis_service.py`
- Test: `tests/test_fund_report.py`（追加）

**Interfaces:**
- Consumes: `is_fund_code`（Task 2）、`FundDataProvider`（Task 3）、`FundReportSchema`（Task 5）、`_get_fund_system_prompt`（Task 5）
- Produces: `AnalysisService._analyze_fund_stock(stock_code, query_id, report_language, trace_id) -> dict`；`_build_fund_response(...)` 返回与现有 `_build_analysis_response` 结构对齐的 dict

- [ ] **Step 1: 入口分流 + 编排实现**

在 `src/services/analysis_service.py` 的 `analyze_stock`（L50）最前加分流：

```python
        from src.services.fund_data_provider import is_fund_code
        if is_fund_code(stock_code):
            return self._analyze_fund_stock(
                stock_code, query_id=query_id, report_language=report_language,
                trace_id=trace_id)
```

新增 `_analyze_fund_stock`：

```python
    def _analyze_fund_stock(self, stock_code, query_id=None,
                            report_language=None, trace_id=None):
        """场外基金分析：净值/持仓 -> LLM -> 基金报告结构（复用骨架，领域层基金专属）。"""
        from src.services.fund_data_provider import FundDataProvider, strip_fund_suffix
        from src.schemas.fund_report_schema import FundReportSchema
        from src.analyzer import _get_fund_system_prompt
        import json

        provider = FundDataProvider()
        nav_rows, nav_quality = provider.get_nav_series(stock_code)
        stats = provider.stats_from_nav(nav_rows)
        try:
            holdings = provider.get_holdings(stock_code)
        except Exception:
            holdings = []
        fund_context = {
            "is_fund": True, "fund_code": strip_fund_suffix(stock_code),
            "nav_quality": nav_quality, "holding_count": len(holdings),
            "holding_preview": [h.model_dump() for h in holdings[:10]],
            **stats,
        }
        # 复用 LLM 分析骨架：以基金 prompt 调用，解析为基金报告
        decision = self._run_fund_llm(fund_context, report_language)
        report_schema = FundReportSchema(**transaction_data)  # 见 Step 2 实际实现
        return self._build_fund_response(stock_code, report_schema,
                                         query_id, report_language)
```

> 注：`_run_fund_llm` 为复用 `GeminiAnalyzer` 的浅封装（见 Step 2）；`transaction_data` 示意为 LLM 返回的 dict，实际实现里用字段名 `llm_data`。上面片段为流程框架，具体以 Step 2 的实现为准。

- [ ] **Step 2: 实现 `_run_fund_llm` 与 `_build_fund_response`**

在 `analysis_service.py` 内：

```python
    def _run_fund_llm(self, context, report_language):
        """复用 GeminiAnalyzer 的模型调用，喂基金 prompt 与基金上下文。"""
        from src.analyzer import GeminiAnalyzer, _get_fund_system_prompt
        analyzer = GeminiAnalyzer()
        system_prompt = _get_fund_system_prompt(report_language)
        # 组装基金上下文包（复用现有 context pack 结构，减少重复）
        from src.services.analysis_context_builder import build_analysis_context_pack
        pack = build_fund_analysis_context(context)
        return analyzer.run_fund_analysis(system_prompt, pack, report_language)

    def _build_fund_response(self, stock_code, schema, query_id, report_language):
        return {
            "query_id": query_id, "stock_code": stock_code,
            "report": {
                "meta": {"query_id": query_id, "stock_code": stock_code,
                         "report_type": "fund", "report_language": report_language},
                "fund": schema.model_dump(),
            },
        }
```

> 说明：`GeminiAnalyzer.run_fund_analysis` 与 `build_fund_analysis_context` 为新增助手（Task 5 预算内），负责把基金上下文 -> JSON 契约给 LLM -> 解析回 `FundReportSchema`。实现时可复用 `GeminiAnalyzer` 的 `_call_llm`/JSON 解析逻辑；若 `analyzer.analyze` 已足够通用，也可直接扩展其 `_get_analysis_system_prompt` 增加基金分支、复用 `analyze`。实现遵循"复用已有 LLM 调用骨架，仅替换 prompt 与上下文"原则，不复制整套 analyzer。

- [ ] **Step 3: 写编排失败测试（mock 数据源）**

```python
# tests/test_fund_report.py 追加
from unittest import mock
from src.services.analysis_service import AnalysisService

def test_analyze_fund_dispatches():
    with mock.patch("src.services.analysis_service.is_fund_code", return_value=True), \
         mock.patch.object(AnalysisService, "_analyze_fund_stock", return_value={"report": {"meta": {"report_type": "fund"}}}) as m:
        svc = AnalysisService()
        out = svc.analyze_stock("006229.FUND")
        m.assert_called_once()
        assert out["report"]["meta"]["report_type"] == "fund"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_report.py -v`
Expected: PASS（路由 mock 通过；网络数据路径标记 `network` 或不进单测）

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/services/analysis_service.py src/analyzer.py tests/test_fund_report.py
git commit -m "feat: add off-market fund analysis orchestration"
```

---

## Task 7: API 收口（校验放行基金代码）

**Files:**
- Modify: `api/v1/endpoints/analysis.py`（校验 `_is_obviously_invalid_analysis_input` 等处）
- No test required（行为不变，仅放行新代码形态）

**Interfaces:**
- Consumes: `is_fund_code`（Task 2）
- Produces: 允许 `006229.FUND` 通过输入校验并进入 `analyze_stock`

- [ ] **Step 1: 放行基金后缀**

在 `api/v1/endpoints/analysis.py` 的输入校验函数（`_is_obviously_invalid_analysis_input`）中，追加"基金后缀代码不判为非法"分支：

```python
        from src.services.fund_data_provider import is_fund_code
        if is_fund_code(raw_value):
            return False
```

放在现有校验末尾、返回"明显非法"之前，保证 `.FUND` 代码绕过裸码/正则拦截。

- [ ] **Step 2: 手动验证 API 可解析（可选，需运行后端）**

Run: `uv run python -c "from api.v1.endpoints.analysis import _is_obviously_invalid_analysis_input as f; print(f('006229.FUND'))"`
Expected: `False`（即"非明显非法"，可进入分析）

- [ ] **Step 3: Commit（需用户确认）**

```bash
git add api/v1/endpoints/analysis.py
git commit -m "feat: allow off-market fund code through analysis input validation"
```

---

## Task 8: 前端报告展示（ReportType + FundReportView + 分派）

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts`
- Create: `apps/dsa-web/src/components/report/FundReportView.tsx`
- Modify: `apps/dsa-web/src/components/report/ReportSummary.tsx`
- Modify: `apps/dsa-web/src/components/report/HomeReportRegion.tsx`
- Modify: `apps/dsa-web/src/components/report/index.ts`

**Interfaces:**
- Consumes: 后端 `report.meta.report_type === "fund"` 与 `report.fund` 载荷
- Produces: `FundReportView` 组件；`ReportSummary` 按 `meta.reportType === 'fund'` 渲染

- [ ] **Step 1: 类型加 fund**

`apps/dsa-web/src/types/analysis.ts`（L9）：

```ts
export type ReportType = StockReportType | 'market_review' | 'fund';
```

并新增基金 payload 类型（对齐后端 `report.fund`）：

```ts
export interface FundReportPayload {
  fundName?: string; fundType?: string; manager?: string; scale?: string;
  inceptionDate?: string; intervalReturn?: number | null; maxDrawdown?: number | null;
  currentDrawdown?: number | null; holdingsConcentration?: string;
  topHoldings?: Array<{ code?: string; name?: string; ratio?: number; quarter?: string }>;
  analysisSummary?: string; operationAdvice?: string; riskWarning?: string;
  sentimentScore?: number; [k: string]: unknown;
}
```

- [ ] **Step 2: 新建 FundReportView**

`apps/dsa-web/src/components/report/FundReportView.tsx`（镜像 `MarketReviewReportView` 的结构与 props）：

```tsx
export interface FundReportViewProps {
  report?: AnalysisReport; recordId?: string; onOpenRunFlow?: (id: string) => void;
}
export function FundReportView({ report, recordId, onOpenRunFlow }: FundReportViewProps) {
  const fund = (report?.details as unknown as { fund?: FundReportPayload })?.fund
    ?? (report as unknown as { fund?: FundReportPayload })?.fund;
  if (!fund) return null;
  return (
    <div className="space-y-5 pb-8 animate-fade-in">
      <Section title="基金概览">{fund.fundName} / {fund.fundType} / 经理 {fund.manager} / 规模 {fund.scale}</Section>
      <Section title="净值与回撤">区间收益 {fund.intervalReturn} · 最大回撤 {fund.maxDrawdown} · 当前回撤 {fund.currentDrawdown}</Section>
      <Section title="重仓股">{fmtHoldings(fund.topHoldings)}</Section>
      <Section title="分析与建议">{fund.analysisSummary}<br/>{fund.operationAdvice}</Section>
      <Section title="风险提示">{fund.riskWarning}</Section>
    </div>
  );
}
```
> 实际实现按仓库组件风格（复用 `ReportMarkdownBody`/空字段 `—`/null 时不渲染等既有模式）补齐，不改路由。

- [ ] **Step 3: ReportSummary 分派**

`apps/dsa-web/src/components/report/ReportSummary.tsx` 在 `if (meta.reportType === 'market_review')` 旁追加：

```tsx
if (meta.reportType === 'fund') {
  return <FundReportView report={report} recordId={recordId} .../>;
}
```

- [ ] **Step 4: HomeReportRegion 基金历史分支**

`apps/dsa-web/src/components/report/HomeReportRegion.tsx` 仿 `isMarketReviewHistoryReport`（L108）加：

```tsx
const isFundHistoryReport = selectedReport?.meta.reportType === 'fund';
```
并在按钮分派处（L184-221）让基金历史报告也走"重新分析/ask AI"而非"rerun market review"。

- [ ] **Step 5: index 导出**

`apps/dsa-web/src/components/report/index.ts` 导出 `FundReportView`。

- [ ] **Step 6: 前端校验**

Run: `cd apps/dsa-web && npm run lint && npm run build`
Expected: 通过

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/types/analysis.ts apps/dsa-web/src/components/report/
git commit -m "feat: render off-market fund report view in web frontend"
```

---

## Task 9: 端到端验证与文档

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/market-support.md`（新增场外基金边界段落）

- [ ] **Step 1: 端到端手工验证（需要网络，标记 network）**

Run: `uv run python -m pytest -m "not network"`（离线回归，确认股票链路零回归）
Run: 后端启动后 `curl -s -X POST .../api/v1/analysis/analyze -d '{"stock_code":"006229.FUND"}'`（观察 report 载荷，网络路径，手工）——若网络受限，记录未验证原因。

- [ ] **Step 2: py_compile 全部变更**

Run: `uv run python -m py_compile src/services/fund_data_provider.py src/services/stock_list_parser.py src/services/analysis_service.py src/analyzer.py src/config.py src/schemas/fund_report_schema.py`
Expected: 无错误

- [ ] **Step 3: 更新文档**

`docs/CHANGELOG.md` `[Unreleased]` 增加扁平行：
```
- [新功能] 支持场外基金单基分析报告（.FUND 后缀，配置 FUND_SUPPORT 开启）
```
`docs/market-support.md` 增加场外基金边界段（说明显式后缀、不承诺实时净值/裸码自动纠偏）。

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add docs/CHANGELOG.md docs/market-support.md
git commit -m "docs: document off-market fund analysis support and boundaries"
```

---

## 自审记录

- **Spec 覆盖**：识别(Task2/4)、数据源(Task3)、Schema(Task5)、Prompt(Task5)、编排(Task6)、API(Task7)、前端(Task8)、配置(Task1)、测试——全部落点。
- **未实现项（后续子项目 B/C/D）**：本计划仅覆盖子项目 A 报告链路；持仓估值/告警/池巡检为后续 spec，不在本计划。
- **类型一致性**：`is_fund_code/strip_fund_suffix/FundDataProvider/FundReportSchema/FundReportView` 等跨 Task 命名统一。
- **注意**：Task 6 的 `run_fund_analysis`/`build_fund_analysis_context` 为新增助手，落在 `src/analyzer.py`/`analysis_context_builder` 复用处；实现时以"复用 LLM 骨架、仅替换 prompt 与上下文"为原则，不复制整套 analyzer。若 `_build_fund_response` 需与现有 `AnalysisResultResponse.report` 兼容，则后端历史/API 响应层（`api/v1/schemas/history.py`）按需追加 fund 载荷字段以支持前端渲染——此为 Task 8 的前置，若发现缺失应在 Task 8 一并处理。
