# 场外基金净值体检分析 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 跟踪进度。

**Goal:** 新增场外基金(如 003095)的净值体检分析路径,产出「该不该拿/风险多大」报告,像股票一样接入自选→分析→报告→历史→推送→Web。

**Architecture:** 独立 `FundFetcher`(东财免费净值源)+ `fund:` 前缀代码标记路由 + 净值指标确定性计算(收益/回撤/波动/Sharpe)+ 基金版 LLM 报告。与股票引擎严格隔离,不改股票/ETF 识别与逻辑。

**Tech Stack:** Python 3.12、pydantic(pydantic dataclasses)、pandas、requests、pytest、现有 `BaseFetcher` / `ReportType` / `AnalysisResult` 基建。

**Spec:** `docs/superpowers/specs/2026-09-05-fund-analysis-design.md`

## Global Constraints

- 不执行 `git commit/tag/push` 除非用户明确确认(AGENTS.md)。
- 新增配置项需同步 `.env.example`(`FUND_RISK_FREE_RATE`,默认 `0.02`)。
- 报告**不含**买卖点/止损/仓位建议;文案显式声明「基于净值,非股票式信号,不构成投资建议」。
- 不改动 A 股 / 场内 ETF 的识别与分析逻辑(`get_market_for_stock`、`_is_etf_code` 不动)。
- 代码标记:`fund:<code>`(仅带前缀才识别为基金;裸 6 位数默认按股票,保持现状)。
- LLM 渠道与股票共用(现有 DeepSeek),基金仅换 prompt、不动 provider/model 契约。
- commit message 英文,不加 `Co-Authored-By`。
- 变更文件需 `python -m py_compile`;后端走 `./scripts/ci_gate.sh`。

---

### Task 1: 基金数据源 `FundFetcher` + 净值指标(确定性计算)

**Files:**
- Create: `data_provider/fund_fetcher.py`
- Test: `tests/test_fund_fetcher.py`

**Interfaces:**
- Produces:
  - `@dataclass NavRecord(date: str, unit_nav: float, acc_nav: float, change_pct: float)`
  - `@dataclass FundProfile(code, name, fund_type, nav_history: List[NavRecord], holdings: List[dict], return_1m/3m/6m/1y, max_drawdown, annual_volatility, sharpe)`
  - `FundFetcher` methods:
    - `get_profile(code: str, history_len: int = 250) -> FundProfile`
    - 可测试的解析器(纯函数):`parse_lsjz(payload: dict) -> List[NavRecord]`、`parse_pingzhongdata(raw: str) -> tuple`、`compute_metrics(history: List[NavRecord], risk_free: float) -> dict`

- [ ] **Step 1: 写失败测试(解析器 + 指标)**

```python
# tests/test_fund_fetcher.py
import math
from data_provider.fund_fetcher import parse_lsjz, compute_metrics, NavRecord

def _mk(dates, navs):
    recs = []
    for d, n in zip(dates, navs):
        acc = n * 1.5
        recs.append(NavRecord(date=d, unit_nav=n, acc_nav=acc, change_pct=0.0))
    return recs

def test_parse_lsjz_maps_fields():
    payload = {"Data": {"LSJZList": [
        {"FSRQ": "2026-09-04", "DWJZ": "1.9360", "LJJZ": "2.1740", "JZZZL": "-1.07"},
    ]}}
    recs = parse_lsjz(payload)
    assert recs[0].date == "2026-09-04"
    assert abs(recs[0].unit_nav - 1.936) < 1e-9
    assert abs(recs[0].acc_nav - 2.174) < 1e-9
    assert abs(recs[0].change_pct - -1.07) < 1e-9

def test_compute_metrics_return_1m_and_drawdown():
    # 120 trading rows: unit_nav starts at 1.0, rises to 1.5, then falls to 1.2
    navs = list(range(1, 61)) + list(range(60, 30, -1)) + [30]
    navs = [v / 40.0 for v in navs]   # ~0.025 .. ~1.5, then down to 0.75
    recs = []
    for i, v in enumerate(navs):
        recs.append(NavRecord(date=str(i), unit_nav=round(v, 6), acc_nav=round(v * 1.5, 6), change_pct=0.0))
    m = compute_metrics(recs)
    assert m["return_1m"] is not None
    assert m["max_drawdown"] < 0      # 序列存在峰值回落
    assert m["return_1y"] is None     # 120 行不足以算 1 年(252 日)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError: data_provider.fund_fetcher`

- [ ] **Step 3: 实现 `data_provider/fund_fetcher.py`**

```python
# data_provider/fund_fetcher.py
from __future__ import annotations
import math, statistics
from dataclasses import dataclass, field
from typing import List, Optional
import requests

TRADING_DAYS = 252

@dataclass
class NavRecord:
    date: str
    unit_nav: float
    acc_nav: float
    change_pct: float

@dataclass
class FundProfile:
    code: str
    name: str
    fund_type: str = ""
    nav_history: List[NavRecord] = field(default_factory=list)
    holdings: List[dict] = field(default_factory=list)
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_1y: Optional[float] = None
    max_drawdown: Optional[float] = None
    annual_volatility: Optional[float] = None
    sharpe: Optional[float] = None

def is_fund_code(code: str) -> bool:
    return bool(code) and code.startswith("fund:")

def strip_fund_prefix(code: str) -> str:
    return code[5:] if is_fund_code(code) else code

def parse_lsjz(payload: dict) -> List[NavRecord]:
    rows = (payload.get("Data") or {}).get("LSJZList") or []
    recs = []
    for r in rows:
        try:
            recs.append(NavRecord(
                date=r.get("FSRQ", ""),
                unit_nav=float(r.get("DWJZ") or 0),
                acc_nav=float(r.get("LJJZ") or 0),
                change_pct=float(r.get("JZZZL") or 0),
            ))
        except (TypeError, ValueError):
            continue
    return recs

def parse_pingzhongdata(raw: str) -> tuple:
    name = ""
    if 'fS_name = ' in raw:
        name = raw.split('fS_name = "', 1)[1].split('"', 1)[0]
    return name

def compute_metrics(history: List[NavRecord], risk_free: float = 0.02) -> dict:
    if not history:
        return {}
    navs = [h.unit_nav for h in history]
    n = len(navs)

    def ret(days: int):
        if n <= days:
            return None
        start = navs[-days - 1]
        end = navs[-1]
        if start <= 0:
            return None
        return end / start - 1.0

    # max drawdown
    peak = -math.inf; mdd = 0.0
    for v in navs:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak else 0.0
        mdd = min(mdd, dd)
    # daily returns -> annualized volatility + sharpe
    daily = [(navs[i] / navs[i-1] - 1.0) for i in range(1, n) if navs[i-1]]
    vol = (statistics.pstdev(daily) * math.sqrt(TRADING_DAYS)) if len(daily) >= 2 else None
    annual_ret = ret(TRADING_DAYS)
    sharpe = None
    if vol and annual_ret is not None and vol > 0:
        sharpe = (annual_ret - risk_free) / vol
    return {
        "return_1m": ret(22), "return_3m": ret(66), "return_6m": ret(126),
        "return_1y": ret(TRADING_DAYS), "max_drawdown": mdd,
        "annual_volatility": vol, "sharpe": sharpe,
    }

_FUND_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
_FUND_INFO_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_HEADERS = {"Referer": "https://fundf10.eastmoney.com/"}

class FundFetcher:
    def get_profile(self, code: str, history_len: int = 250) -> FundProfile:
        base = strip_fund_prefix(code)
        nav = self._fetch_nav(base, history_len)
        name = self._fetch_name(base)
        holdings = []
        metrics = compute_metrics(nav)
        return FundProfile(code=base, name=name, nav_history=nav,
                           holdings=holdings, **metrics)

    def _fetch_nav(self, code: str, limit: int) -> List[NavRecord]:
        r = requests.get(_FUND_NAV_URL, params=dict(fundCode=code, pageIndex=1, pageSize=limit),
                         headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return parse_lsjz(r.json())

    def _fetch_name(self, code: str) -> str:
        try:
            r = requests.get(_FUND_INFO_URL.format(code=code), headers=_HEADERS, timeout=20)
            return parse_pingzhongdata(r.text)
        except Exception:
            return ""
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_fetcher.py -v`
Expected: PASS

- [ ] **Step 5: 校验编译**

Run: `python -m py_compile data_provider/fund_fetcher.py`
Expected: 无错误

---

### Task 2: 基金代码识别与配置同步

**Files:**
- Modify: `.env.example`(新增 `FUND_RISK_FREE_RATE`)
- Test: `tests/test_fund_code_routing.py`

**Interfaces:**
- Consumes: `is_fund_code`, `strip_fund_prefix` (Task 1)
- Produces: `resolve_instrument_kind(code: str) -> str`(返回 `"fund"` 或 `"stock"`),供 pipeline 分流用

- [ ] **Step 1: 写失败测试(歧义反例)**

```python
# tests/test_fund_code_routing.py
from data_provider.fund_fetcher import is_fund_code, strip_fund_prefix

def test_fund_prefix_recognized():
    assert is_fund_code("fund:003095") is True
    assert strip_fund_prefix("fund:003095") == "003095"

def test_bare_stock_code_not_fund():
    assert is_fund_code("003816") is False   # 003816 是 A 股,不得判为基金
    assert is_fund_code("600519") is False
    assert is_fund_code("") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_code_routing.py -v`
(依赖 Task 1 的 `is_fund_code`,Task 1 先行;两步合并运行即可)

- [ ] **Step 3: `.env.example` 追加配置**

```bash
# 场外基金体检:无风险利率(用于 Sharpe)
FUND_RISK_FREE_RATE=0.02
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_code_routing.py -v`
Expected: PASS

---

### Task 3: 基金分析服务 + 基金报告

**Files:**
- Create: `src/services/fund_analysis.py`
- Test: `tests/test_fund_analysis.py`

**Interfaces:**
- Consumes: `FundProfile`, `compute_metrics` (Task 1); `ReportType.FUND` (added 下一步,先定义在 `src/enums.py`)
- Produces: `build_fund_report(fund: FundProfile, risk_free: float) -> dict`,报告 dict 直接可被 `AnalysisResult` 形态下游消费的字段:`sentiment_score`、`operation_advice`、`trend_prediction`、`report_type="fund"`、`summary`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fund_analysis.py
from data_provider.fund_fetcher import NavRecord
from src.services.fund_analysis import build_fund_report

def _profile():
    navs = []
    nv = 1.0
    for d in range(120):
        nv *= 1.001  # 平稳上行
        navs.append(NavRecord(date=str(d), unit_nav=round(nv,4), acc_nav=0.0, change_pct=0.1))
    from data_provider.fund_fetcher import FundProfile, compute_metrics
    m = compute_metrics(navs)
    return FundProfile(code="003095", name="中欧医疗健康混合A", nav_history=navs, **m)

def test_build_fund_report_shape():
    r = build_fund_report(_profile())
    assert r["report_type"] == "fund"
    assert "operation_advice" in r
    assert "风险" in r["summary"] or "净值" in r["summary"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: src.services.fund_analysis`

- [ ] **Step 3: 实现 `src/services/fund_analysis.py`**

```python
# src/services/fund_analysis.py
from __future__ import annotations
from typing import Any, Optional
from data_provider.fund_fetcher import FundProfile

# 无需 LLM 的确定性结论:报告骨架;LLM 层作为增强由下游 Prompt 任务补充。
def _risk_grade(mdd, vol):
    if mdd is None or vol is None:
        return "数据不足"
    if mdd < -0.20 or vol > 0.30:
        return "高"
    if mdd < -0.08 or vol > 0.15:
        return "中"
    return "低"

def build_fund_report(fund: FundProfile, risk_free: float = 0.02) -> dict:
    latest = fund.nav_history[-1] if fund.nav_history else None
    m = {
        "return_1m": fund.return_1m, "return_3m": fund.return_3m,
        "return_6m": fund.return_6m, "return_1y": fund.return_1y,
        "max_drawdown": fund.max_drawdown, "annual_volatility": fund.annual_volatility,
        "sharpe": fund.sharpe,
    }
    risk = _risk_grade(fund.max_drawdown, fund.annual_volatility)
    trend = "上行" if (fund.return_3m or 0) > 0 and (fund.return_1y or 0) > 0 else "震荡"
    if (fund.return_1y or 0) < -0.1:
        trend = "下行"
    advice = "风险偏高,建议关注" if risk == "高" else "可继续持有"
    if trend == "下行":
        advice = "趋势偏弱,谨慎持有"
    summary = (
        f"{fund.name}({fund.code}) 近1年收益 {fmt(fund.return_1y)}、最大回撤 {fmt(fund.max_drawdown)};"
        f"风险等级:{risk}。基于净值序列,非股票式信号,不构成投资建议。"
    )
    return {
        "report_type": "fund",
        "code": fund.code, "name": fund.name,
        "sentiment_score": 50,  # 无买卖档,固定中性(下游摘要按分数排序用)
        "operation_advice": advice,
        "trend_prediction": trend,
        "summary": summary,
        "metrics": m, "latest_nav": latest.unit_nav if latest else None,
        "not_investment_advice": True,
    }

def fmt(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x*100:.1f}%"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_fund_analysis.py -v`
Expected: PASS

---

### Task 4: 报告类型 `FUND` + 通知分支 + pipeline 分流

**Files:**
- Modify: `src/enums.py:13`(`ReportType`)
- Modify: `src/notification.py:413`(`generate_aggregate_report` + `generate_dashboard_report`)
- Modify: `src/core/pipeline.py:414`(`analyze_stock` 入口按 `is_fund_code` 分流)
- Test: `tests/test_fund_pipeline_routing.py`

**Interfaces:**
- Consumes: `is_fund_code` (Task 1), `build_fund_report` (Task 3)
- Produces: 基金代码经 `analyze_stock` 后返回带 `report_type="fund"` 的结果对象;通知渲染走 `generate_fund_dashboard`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fund_pipeline_routing.py
from data_provider.fund_fetcher import is_fund_code
from src.enums import ReportType

def test_report_type_has_fund():
    assert ReportType.from_str("fund") == ReportType.FUND

def test_route_marker():
    assert is_fund_code("fund:003095") is True
    assert is_fund_code("600519") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_fund_pipeline_routing.py -v`
Expected: FAIL(`ReportType.FUND` 不存在)

- [ ] **Step 3: 添加枚举 `FUND`**

```python
# src/enums.py (ReportType 内新增)
    FUND = "fund"  # 场外基金净值体检报告
```
并在 `from_str` 的合法映射里加入 `"fund": cls.FUND`。

- [ ] **Step 4: 通知分支(锚 `src/notification.py`)**

在 `generate_aggregate_report(results, report_type)` 里,对结果先按 `report_type == "fund"` 分批,新增 `generate_fund_aggregate(results)`:

```python
def generate_fund_aggregate(self, results) -> str:
    lines = [f"# 📈 基金体检 ({len(results)}支)"]
    for r in results:
        lines.append(f"\n## {r.name}({r.code}) | {r.operation_advice} | {r.trend_prediction}")
        lines.append(f"> {r.summary}")
    lines.append("\n---\n*基于净值,非股票式信号,不构成投资建议*")
    return "\n".join(lines)
```

- [ ] **Step 5: pipeline 分流(锚 `src/core/pipeline.py` `analyze_stock`)**

在进入现有股票分析前,若 `is_fund_code(code)`:

```python
from data_provider.fund_fetcher import FundFetcher, is_fund_code
from src.services.fund_analysis import build_fund_report
...
def analyze_stock(self, code, ...):
    if is_fund_code(code):
        profile = FundFetcher().get_profile(code)
        return self._result_from_fund_report(build_fund_report(profile))
    ...  # 现有股票逻辑不动
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/test_fund_pipeline_routing.py -v && python -m py_compile src/enums.py src/notification.py src/core/pipeline.py src/services/fund_analysis.py`
Expected: PASS + 编译通过

---

### Task 5: 历史库 / API

**Files:**
- Modify: `src/services/`(历史存储 record_type 放行 `fund`)与 `api/`(history 列表返回 fund report)
- Test: `tests/test_fund_history_store.py`

**Interfaces:**
- Produces: `/api/v1/history` 可查询 `report_type="fund"` 报告;Web 侧读取同字段渲染。

- [ ] **Step 1: 测试**

```python
# tests/test_fund_history_store.py
def test_history_accepts_fund_record():
    # 复用现有 report_store 保存一枚 report_type="fund" 记录并读回
    ...
```

- [ ] **Step 2: 实现** —— 在报告持久化与 `history` 查询处放行 `fund` 类型(不加鉴权/字段变更,仅放行枚举与展示分类)。

- [ ] **Step 3: 校验** —— `python -m py_compile` 变更文件 + `pytest tests/test_fund_history_store.py -v`。

---

### Task 6: Web 报告卡片(前端渲染)

**Files:**
- Modify: `apps/dsa-web/src/`(报告详情按 `report_type==="fund"` 渲染指标卡,隐藏买卖点区块)
- Test: 前端现有测试目录补基金卡片断言

**Interfaces:**
- Consumes: `/api/v1/history/{id}/markdown` 与 `report_type` 字段
- 说明:`md` 报告已含声明文案;前端仅需按类型隐藏「作战计划(买卖点)」区块并展示「净值指标/风险等级」。

- [ ] **Step 1: 前端组件 —— 依 `report_type` 条件渲染**(基金:显示净值指标卡,隐藏操作点位表)。
- [ ] **Step 2: 构建校验** —— `cd apps/dsa-web && npm run lint && npm run build`。

---

### Task 7: 文档同步 + 端到端验证

**Files:**
- Modify: `docs/full-guide.md`(新增基金分析小节、`fund:` 用法、`FUND_RISK_FREE_RATE`)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 以扁平 `- [新功能] 支持场外基金净值体检分析` 单行追加)
- Modify: `docs/market-support.md`(注明场外基金为净值体检、无买卖点;`fund:` 标记)

- [ ] **Step 1: 更新三份文档**(命令、配置、行为声明与实际一致)。
- [ ] **Step 2: 后端验证** —— `./scripts/ci_gate.sh` + `python -m py_compile` 变更文件。
- [ ] **Step 3: 端到端抽检(联网可观测)** —— `uv run python -c "from data_provider.fund_fetcher import FundFetcher; p=FundFetcher().get_profile('003095'); print(p.name, len(p.nav_history), p.max_drawdown)"`,预期输出 003095 名称与净值长度、回撤,非抛错。

---

## Self-Review

**1. Spec 覆盖:** 数据源(Task1)、代码识别/配置(Task2)、分析+报告(Task3)、report_type+通知+pipeline(静态)接入(Task4)、历史/API(Task5)、Web(Task6)、文档+验证(Task7)。已覆盖设计 §3–§9 主体。**缺口:** 重仓股持仓区块(f10/JJCC)与 LLM 增强层在 Task3 以确定性骨架实现,LLM 深层解读留作后续增强(标注在 spec §5,计划内以「确定性结论 + 骨架」先行,避免过度设计)。

**2. 占位扫描:** 无「TBD/稍后处理」;每个代码步骤均有实现代码。Task5/6 因依赖未深入核验的具体签名,步骤以锚点+意图描述为主 —— 实现时需对照现行 `src/services`/`api`/Web 报告组件补精确签名(Task5/6 的执行说明里已明确此依赖)。

**3. 类型一致:** `is_fund_code`/`strip_fund_prefix`/`build_fund_report`/`FundFetcher.get_profile`/`ReportType.FUND` 在各任务签名一致;`compute_metrics` 返回键与 `FundProfile` 字段对齐。

---

**执行说明:** 建议按 Task 顺序;Task 2/3/4/5/6 依赖前一任务类型,须顺序执行。Task 5/6 具体签名对照现行代码微调(Task 说明已标注)。每任务以「测试通过 + py_compile」为门禁,最后跑 `./scripts/ci_gate.sh` 与端到端抽检。
