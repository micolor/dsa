# 场外基金支持 — 子项目 A：单基金分析报告 设计文档

- 日期：2026-08-26
- 状态：已实现（本文档已按落地情况校正）
- 范围：子项目 A `单基金分析报告`（子项目 B/C/D 仅在文末预留接口，不在此实现）

> **关于「已按落地情况校正」**：本文档最初作为待实现设计提交，其中若干决策在实现阶段被
> 取代。为遵守 `AGENTS.md` §5.6「文档与代码不一致时以实际代码为准」，正文已改写为
> **as-built** 描述，原决策与取代原因集中记录在文末「与原稿的差异」，不再散落于正文。

## 背景与目标

现有系统（A 股/港股/美股/日股/韩股/台股）全部按**证券**建模：有实时价/K 线，估值 = 价格 × 数量，
分析走技术面 + 基本面，报告 Schema 的字段（MA、成交量、筹码、涨跌停、资金流、龙虎榜、板块）均为股票概念。

场外基金（公募开放式基金）**没有盘中行情，只有每日净值**，语义与证券彻底不同。因此：

1. 不把基金硬塞进证券市场；在现有证券链路之外增加一条**资产类别=fund** 的并行领域链路。
2. **骨架复用**：任务编排、LLM 分析调用、通知推送、报告历史保存、前端报告渲染、run_flow 诊断复用现有实现。
3. **领域层新增**：数据源适配、分析模型、报告 Schema、LLM Prompt 均为基金专属，不污染股票链路。
4. 股票链路零改动，避免回归。

本 spec 只覆盖**子项目 A：输入场外基金代码 → 产出单基金分析报告**，作为后续
组合持仓估值（B）、基金提醒预警（C）、基金池巡检复盘（D）的共同底座。

## 现状与可复用 / 需新增（复用矩阵）

| 层 | 现有实现 | 场外基金处置 |
| --- | --- | --- |
| 编排骨架 | `src/core/pipeline.py` 任务调度 | 复用（`analyze_stock` 内基金分支） |
| LLM 分析调用 | `src/analyzer.py` `_call_litellm` 骨架 | 复用（`run_fund_analysis` 换 prompt 与校验契约） |
| 通知推送 | `src/notification.py` 通道 | 复用 |
| 报告历史保存 | `src/repositories/` 分析历史 | 复用（`report_type="fund"`） |
| 报告渲染 / 前端 | `apps/dsa-web` 报告卡片框架 | 复用（`FundMetricsCard`，不新增独立视图） |
| 数据馈送 | `data_provider/` `get_daily_data`（K线） | 替换为基金净值序列（`FundFetcher`） |
| 分析模型 | 技术面 + 基本面 | 替换为净值/回撤/持仓 |
| 报告 Schema | `src/schemas/report_schema.py` 股票模型 | 新增 `fund_report_schema.py`（仅 LLM 解读字段） |
| LLM Prompt | 股票概念 Prompt | 新增 `GeminiAnalyzer.FUND_SYSTEM_PROMPT` |

## 设计

### 1. 资产识别

裸 6 位代码天然歧义（例：`006229` 既可能是深市证券，也是场外基金 `中欧医疗创新股票C`）。
为不破坏现有证券语义，采用**显式前缀**识别：

- `fund:<6位代码>`（如 `fund:006229`）→ 识别为基金，走基金链路。
- 大小写不敏感：任务队列会经 `normalize_stock_code` 大写化前缀，`FUND:006229` 同样识别为基金。
- **不带前缀的裸码按既有证券语义处理**（保持现有行为，不做无提示改变），不做启发式自动纠偏。
- 识别唯一收口在 `data_provider/fund_fetcher.is_fund_code()` / `strip_fund_prefix()`，
  `src/core/pipeline.py` 的基金分支是唯一判别点。

### 2. 数据源（复用东财公开接口，零新增依赖）

实现落在 `data_provider/fund_fetcher.py`，不新增平行 provider 抽象层：

- 净值序列：东财 `lsjz` 接口，解析为 `NavRecord(date / unit_nav / acc_nav / change_pct)`。
- **顺序契约**：东财按净值日期倒序返回，且排法历史上不保证稳定，因此 `_fetch_nav`
  在数据源边界统一归一化为**正序（旧 → 新）**，下游一律按「列表末尾是最新一条」
  取数（`compute_metrics` 的区间收益与回撤、`build_fund_report` 的 `latest_nav`、
  LLM prompt 的「最新净值」）。顺序不在这一层摆正，卡片会把窗口内最旧的一条当
  最新净值展示、区间收益也会算在时间倒序的路径上——`tests/test_fund_fetcher.py`
  有四例守这个边界。默认窗口取 `TRADING_DAYS + 1`，否则「近 1 年」恒为空值。
- 资产配置（`zcpz`）与十大重仓（`jjcc`）：解析为 `FundAssetAllocation` / `FundHolding`。
- `compute_metrics(history)`：由净值序列确定性演进区间收益、最大回撤、年化波动率、夏普比率。
- fail-open：单一接口失败不阻断体检主流程，对应区块为空并在报告中体现。

### 3. 数据模型

`FundProfile`：代码、名称、基金类型、净值序列、持仓、资产配置、区间收益、最大回撤、年化波动率、夏普比率。

**不含**基金经理、规模、成立日期、费率——当前数据源不提供，因此 LLM 契约也不向模型索取这些字段
（索取等于请模型凭记忆编造，见 §6）。

### 4. 分析维度

事实层（确定性，不经过 LLM）：

1. 净值走势与区间收益率（近 1 月 / 3 月 / 6 月 / 1 年）。
2. 最大回撤。
3. 年化波动率与夏普比率。
4. 持仓集中度与前十大重仓股。
5. 资产配置（股票 / 债券 / 现金占比 + 净资产）。
6. 净值体检结论与风险等级（`_risk_grade`，客户端镜像同一阈值）。

解读层（LLM 增强，可选）：

7. **LLM 综合解读 + 申赎建议**（基金为申赎，非买卖语义）。

两层字段不重叠：LLM 不复述事实层数字，避免同一张卡片上出现两个可能互相矛盾的数据源。

### 5. 报告 Schema

- `src/schemas/fund_report_schema.py` `FundReportSchema`：**仅**承载 LLM 解读字段
  （`holdings_concentration` / `analysis_summary` / `operation_advice` / `risk_warning` /
  `sentiment_score`），`extra="ignore"` 收口模型多吐的键。
- 事实层载荷由 `build_fund_report()` 确定性构造，直接进 `AnalysisResult.dashboard`。
- 渲染在前端按 `report_type=fund` 分派到 `FundMetricsCard`。

### 6. LLM Prompt

- `GeminiAnalyzer.FUND_SYSTEM_PROMPT`（中文基线）+ `_get_fund_system_prompt(report_language)`
  追加 `Output Language` 段落，与股票 `_get_analysis_system_prompt` 同构。
- **明确排除**涨跌停、北向资金、龙虎榜、融资融券、技术位、成交量、筹码分布等股票概念，
  避免套用股票语义导致幻觉；操作语义统一为**申赎**，不出现买卖点/止损/目标价。
- 字段清单共用 `FUND_REPORT_FIELDS`，prompt 与 schema 不可能各自漂移（有测试守卫）。
- 输出语言段落**不得复用股票版**——股票版要求 `decision_type` 保持 `buy|hold|sell`，
  基金没有买卖档。

### 7. 错误处理 / 降级

- 净值接口失败 → 降级并标注；单接口失败不阻断整条分析链路（与现有 fail-open 约定一致）。
- **LLM 增强层失败一律降级**：未配置模型、所有模型失败、返回内容违反契约、返回全空字段，
  都返回 `None` 并记 warning，确定性报告照常产出。基金报告不因 LLM 挂掉而消失。

### 8. 配置

**不新增配置项。** 增强层的开关就是「是否配置了 LLM」：未配置时 `run_fund_analysis`
自然失败并降级，无需 `FUND_SUPPORT` 之类的独立开关，避免叠加互斥开关。

### 9. 前端（apps/dsa-web）

- 代码输入：`fund:<code>` 手动输入；不做自动补全。
- 报告视图：`FundMetricsCard` 单一渲染器，按 `dashboard` 字段渲染净值指标、资产配置、
  重仓股表，以及可选的 `dashboard.llm` 解读区块（无增强块时整段不渲染）。

### 10. 测试

- 基金识别单测（前缀识别、大小写、裸码不误判）。
- 净值 / 持仓解析单测。
- 确定性报告与载荷映射单测。
- LLM 契约与 prompt 单测（字段集一致、股票概念被排除、语言选择）。
- pipeline 端到端（数据源与 LLM 桩掉）覆盖增强层挂载与降级路径。

### 11. 边界（不承诺）

- 不承诺实时净值（净值本身为每日收盘更新）。
- 不承诺基金经理 / 规模 / 费率 / 同类排行（当前数据源不提供）。
- 不承诺裸码"自动纠偏为基金"（需显式前缀），避免破坏证券语义。

## 为子项目 B / C / D 预留的接口（本 spec 不实现）

- **B 组合持仓估值**：Portfolio 增加 `asset_type=fund` 持仓口径，按申赎金额 / 净值估值（与股票市值口径分离）。复用 `NavRecord` / `FundProfile`。
- **C 基金提醒预警**：告警规则增加基金指标（净值涨跌幅度 / 回撤阈值 / 份额变化），复用通知链路。复用 `FundProfile`。
- **D 基金池巡检复盘**：批量拉净值 → 汇总 → 基金大盘复盘，独立于 `MARKET_REVIEW_REGION` 体系。复用 `FundFetcher` / 分析模型。

## 风险与回滚

- **风险**：东财接口字段变化 → 解析层做列映射归一化 + 缺列时降级，避免强依赖列名。
- **风险**：裸码歧义 → 显式前缀优先，默认不改变证券语义，用户可追踪。
- **回滚**：移除 `fund:` 前缀识别、`data_provider/fund_fetcher.py`、`src/services/fund_analysis.py`、
  `FundReportSchema`、`FundMetricsCard` 及 pipeline 基金分支，并回退文档中的能力声明。

## 与原稿的差异

原稿为待实现设计，以下决策在实现阶段被取代；此处记录原决策与取代原因，避免后来者按原稿实现：

| 项 | 原稿 | 实际落地 | 取代原因 |
| --- | --- | --- | --- |
| 资产识别 | `006229.FUND` / `006229.OTC` 后缀 | `fund:006229` 前缀 | 前后端与任务队列已统一按前缀流转；`is_fund_code` 是大写归一化后仍稳定的唯一判别点，后缀在 `normalize_stock_code` 链路上更易被误判为股票代码。 |
| 数据源 | 新增 `FundDataProvider`（akshare） | `data_provider/fund_fetcher.py` 的 `FundFetcher`（东财接口） | 不新增平行 provider 抽象；东财接口能一次拿到净值 + 资产配置 + 重仓，akshare 需多接口拼装。 |
| 配置 | `.env` 新增 `FUND_SUPPORT`（默认 false） | 无新增配置项 | 基金链路自带显式前缀入口，不会误触发；增强层开关即「是否配置 LLM」。叠加开关违反「避免叠加开关和互斥模式」。 |
| 报告渲染 | 新增基金 ReportView 分派 | 扩展现有 `FundMetricsCard` 单一渲染器 | 复用既有卡片框架，不维护两套渲染路径。 |
| LLM 契约 | 基金字段模型含经理 / 规模 / 成立日 / 最大回撤 / 重仓等 | 仅 5 个解读字段 | 前四项无数据源，索取即诱导幻觉；后三项事实层已有，复述会产生第二个数据源。 |

## 交付要求（仓库约束）

- 改动现有证券链路时保持零行为变化；新增文件遵循现有目录边界（`src/`、`data_provider/`、`api/`、`apps/dsa-web/`）。
- 用户可见能力变化同步 `docs/CHANGELOG.md` 与 `docs/market-support.md`。
- 不写死密钥 / 账号 / 模型名 / 端口；新配置优先"不配置也可运行"。
- 报告格式 / 渲染变化时 PR 附受影响页面截图或说明。
