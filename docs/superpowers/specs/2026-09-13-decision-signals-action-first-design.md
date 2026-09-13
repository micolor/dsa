# 决策信号页「行动优先」重构 — 设计文档

> 目标：把 `/decision-signals`（侧边栏「AI 建议」）从「AI 说过什么」的档案馆，改造为
> 「我现在该做什么」的工作台。范围限于 Web 前端重组 + 一处 Skill 标签映射，
> 不动后端、不动 API 契约、不动数据库。

## 1. 背景与现状

### 1.1 页面现状

`apps/dsa-web/src/pages/DecisionSignalsPage.tsx`（2016 行）当前只有两个区块：

- **全部信号**：7 项高级筛选 → 信号列表（2 列卡片网格，`PAGE_SIZE = 20`，默认
  `status=active`）→ 「信号表现统计」卡（:1540）→ 「Skill 表现」卡（:1629）。
- **单股追踪**：当前股票上下文 → 最新 active 信号 → 历史时间线。

`DecisionSignalCard`（`components/decision-signals/DecisionSignalDisplay.tsx:243`）
本身信息完整：动作徽章、状态、profile、score/confidence/horizon、价格计划
（入场区间/止损/目标价）、reason/catalyst/watch/conditions/risk/invalidation、
plan_quality、market_phase、expires_at。**问题不在单卡信息量，而在于所有卡片
平铺直叙、没有优先级**。

### 1.2 实测数据（`data/stock_analysis.db`）

| 项 | 实际值 |
| --- | --- |
| `decision_signals` 总数 | 8 |
| action 分布 | `watch`×4、`buy`×2、`avoid`×1、`hold`×1 |
| `plan_quality` 分布 | `complete`×6、`partial`×2、`minimal`×1 |
| `portfolio_positions` | 1 个账户、4 条持仓 |
| `decision_signal_feedback` | 0 条（功能从未被使用） |
| `skill_opinion_samples` | 每 skill 13 条左右，距 30 条门槛很远 |

结论：**当前页面的复杂度面向「数千条信号」的规模，而实际只有 8 条。**
7 项筛选、分页、两张统计卡在这个数据量下全是噪音，且「信号表现统计」「Skill 表现」
两张卡长期只能显示空态或 `-`（Skill 表现需每个 `skill × horizon` bucket 累积
`evaluated >= 30`，见 `src/services/skill_opinion_performance_service.py:23`）。

### 1.3 已经接好但没用的能力

以下字段/参数在 Web 类型层与 API 客户端层**已存在**，页面从未使用：

- `holdingOnly` — `types/decisionSignals.ts:107`、`api/decisionSignals.ts:294`
  （后端 `holding_only` 见 `api/v1/endpoints/decision_signals.py:175`）。
- `planQuality` / `entryLow` / `stopLoss` / `targetPrice` — 类型已有，卡片已渲染，
  但未参与任何排序或分档。

### 1.4 已建立的仓库契约

`docs/decision-signals.md`：

> Web 展示必须把这些 wire value 映射为当前 UI 语言的用户可读标签；
> API 响应继续保留原始枚举值。

`components/decision-signals/DecisionSignalDisplay.tsx` 侧的同源先例见
`utils/decisionSignalLabels.ts:56`——Skill 样本充分性（`sufficient` /
`observational`）此前也是前端直接渲染原值，后补了 `SKILL_SAMPLE_STATUS_LABEL_KEYS`。

## 2. 目标与非目标

**目标**

1. 页面首屏回答「现在有什么需要我动手」。
2. 让持仓成为一等公民：勾一下就能只看自己持仓的信号。
3. 把无数据支撑的统计面板从首屏降级。
4. Skill 列显示中文名。

**非目标**

- 不新增后端 endpoint、不改 API 请求/响应契约、不改数据库 schema。
- 不引入新的后端配置项（`.env.example` / config registry 均不动）。
- 不改写信号生成、生命周期、去重、reassess、guardrail 语义。
- 不新增「持仓徽章」——那需要额外拉取持仓接口，为单个标签不值得。
- 不重构「全部信号」与「单股追踪」两块的既有能力（筛选、分页、去重、
  activeFilterChips、详情抽屉、reassess 入口、时间线）——**一行不动**。

## 3. 分档规则（核心）

把 `active` 信号按「要不要我动手」分三档。**动作与计划完整度是「与」的关系**：

| 档位 | 判定条件 | 语义 |
| --- | --- | --- |
| `actionable` | `action ∈ {buy, add, reduce, sell}` **且** `plan_quality = complete` | 有明确动作 + 有入场/止损/目标价，可照此执行 |
| `needs_review` | `action ∈ {buy, add, reduce, sell}` **且** `plan_quality ∈ {partial, minimal, unknown}` | 有动作意图但计划不全，需自行补判断 |
| `watch` | `action ∈ {hold, watch, avoid, alert}` | 不需动手，只需知晓 |

设计理由：一个 `buy` 信号若没有止损位，不应进入「可以动手」档——那等于鼓励盲买。
`alert` 归入观察档：它是提醒而非动作。

**未知值归属**（必须显式实现，不得留空）：`action` 或 `plan_quality` 出现上述集合
之外的取值时（后端枚举扩展、历史脏数据、字段缺失），一律归入 `watch` 档。
理由：分档的方向是「告诉用户该动手」，宁可不提示也不能误报。该降级不得静默——
`needs_review` 与 `watch` 都可见，只是不在首屏最上方。

不存在「其他」兜底桶：三档覆盖全部输入，未知值进 `watch`。

以 §1.2 实测数据代入：`actionable` 1 条（`buy`/`complete`）、
`needs_review` 1 条（`buy`/`minimal`）、`watch` 6 条。

**数据来源**：分档区单独发一次请求
`{ status: 'active', page_size: 100 }`（叠加 `holdingOnly` 时一并带上），前端分档。

选择独立请求而非复用列表当前页的原因：待处理区要的是**全貌**，不能受 `PAGE_SIZE = 20`
分页影响——否则「可以动手」的信号可能落在第 3 页，首屏永远看不到。
该模式与 `dedupeLatest` 一致（`DEDUPE_PAGE_SIZE = 100`，:67），连提示文案模式
（`decisionSignals.dedupeNote`）都可复用。

**已知限制**：超过 100 条 `active` 信号时，分档只覆盖最近 100 条。必须用可见文案
明说（复用 `dedupeNote` 的写法：`仅在最近 {fetched} 条 active 信号内分档。`），
不得静默截断。

## 4. 页面结构

从上到下：

1. **待处理**（新增）
   - 标题行：「待处理」+ 「只看我的持仓」开关 + 刷新入口
   - 「可以动手」分组（默认展开）
   - 「需要确认」分组（默认展开）
   - 「观察」分组（默认折叠，标题带条数）
   - 三个分组各自空态；全空时显示整区空态
   - 底部：`仅在最近 {fetched} 条 active 信号内分档。`
2. **全部信号**（原样保留，一行不动）
3. **单股追踪**（原样保留）
4. **统计**（新增折叠容器，默认折叠）——内含「信号表现统计」与「Skill 表现」两卡

### 4.1 「只看我的持仓」开关

- 页面级 state `holdingOnly`，**放在「待处理」标题行**，同时驱动「待处理」请求
  与「全部信号」列表请求。
- 开关始终可见，因此不存在「隐藏筛选状态」的问题——无需并入
  `activeFilterChips`。
- 复用已接好的 `holdingOnly` 请求参数，不新增后端能力。

### 4.2 筛选表单折叠

- 7 项筛选默认折叠，点击「高级筛选」展开。
- **当存在活跃筛选时自动展开**。判定必须与 `DEFAULT_LIST_FILTERS`（:158）
  **逐字段比较是否相异**。
- **不得复用 `buildActiveFilterChips`（:392）作为判定依据**。该函数用的是真值判断
  （`if (filters.status)`），而 `DEFAULT_LIST_FILTERS.status` 本身就是 `'active'`
  （非空真值），因此**默认状态下 `activeFilterChips` 恒非空**——照搬会导致筛选表单
  永远自动展开，折叠功能形同虚设。判定需单独实现，把 `status` 的默认值
  `'active'` 视为「无筛选」。
- 上述 `activeFilterChips` 恒非空是既有行为（chips 行默认就渲染一个「状态」chip），
  **不在本次范围内修改**，避免夹带无关改动；仅需在实现时绕开它。
- 折叠状态只影响展示，不影响 `getInitialFilters()` 的 URL 还原逻辑。

### 4.3 统计折叠

- 「信号表现统计」与「Skill 表现」两卡整体包进折叠容器，默认折叠。
- 折叠**不卸载**卡片组件会带来的请求问题需要处理：采用「展开时才挂载/请求」的
  懒加载，避免首屏白白发出两个必然空态或长 `-` 的请求。

> 注：这一条是对现有行为的**行为变更**，需在 PR 描述中写明。

## 5. Skill 中文名映射

### 5.1 实现位置

`apps/dsa-web/src/utils/decisionSignalLabels.ts` 新增：

- `SKILL_LABEL_KEYS: Record<string, UiTextKey>`，key 为 skill id，value 为
  `decisionSignals.skill.<id>`。
- `getSkillLabel(skillId, t)`。

`apps/dsa-web/src/i18n/uiText.ts` 新增对应 zh + en 文案。

### 5.2 必须偏离 `translatedKnownValue`

`translatedKnownValue`（:62）对未知值返回 `'-'`。对写死的枚举这是正确行为，
对 skill **是错的**：skill 是开放集合（`AGENT_SKILLS` 支持自定义策略），
返回 `'-'` 会让整列变成横杠，比显示原始英文 id 更糟。

因此 `getSkillLabel` 对未命中映射的 id **必须回退为原始 id 本身**，且该行为要有
测试覆盖。

### 5.3 覆盖范围

镜像后端 `src/report_language.py:245` 的 `_STRATEGY_SKILL_TRANSLATIONS`，
共 15 个策略：

| skill id | zh | en |
| --- | --- | --- |
| `bull_trend` | 默认多头趋势 | Bull Trend |
| `hot_theme` | 热点题材 | Hot Theme |
| `volume_breakout` | 放量突破 | Volume Breakout |
| `ma_golden_cross` | 均线金叉 | MA Golden Cross |
| `growth_quality` | 成长质量 | Growth Quality |
| `bottom_volume` | 底部放量 | Bottom Volume |
| `box_oscillation` | 箱体震荡 | Box Oscillation |
| `chan_theory` | 缠论结构 | Chan Theory |
| `dragon_head` | 龙头战法 | Dragon Head |
| `emotion_cycle` | 情绪周期 | Emotion Cycle |
| `event_driven` | 事件驱动 | Event Driven |
| `expectation_repricing` | 预期重估 | Expectation Repricing |
| `one_yang_three_yin` | 一阳三阴 | One Yang Three Yin |
| `shrink_pullback` | 缩量回踩 | Shrink Pullback |
| `wave_theory` | 波浪理论 | Wave Theory |

### 5.4 防止双份维护漂移

这份映射与后端 `_STRATEGY_SKILL_TRANSLATIONS` 构成事实上的两份副本。为防止后端
新增策略而前端漏补中文名，新增 Python 测试：

`tests/test_skill_label_parity.py`

- 读取 `src/report_language.py` 的 `_STRATEGY_SKILL_TRANSLATIONS` 全部 key。
- 读取 `apps/dsa-web/src/i18n/uiText.ts`，断言每个 key 在 **zh 与 en 两个语言块**
  中都存在 `decisionSignals.skill.<id>`。
- 缺失即失败。

这是本设计引入的唯一跨语言约束，PR 描述需明确说明其存在与理由。

## 6. 配置项

无。不新增 env、config registry 项或 `.env.example` 内容。

## 7. 验证矩阵

**Web**（`apps/dsa-web/`）：

```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

新增/更新测试：

- 分档纯函数单测：三分档边界，重点覆盖
  「`buy` + `partial` 不得进入 `actionable`」、「`alert` 归入 `watch`」、
  「未知 `action`/`plan_quality` 的归属」。
- `DecisionSignalsPage` 渲染测试：三档分组渲染、观察档默认折叠、
  `holdingOnly` 开关驱动请求参数、筛选表单折叠与「有活跃筛选时自动展开」、
  统计区默认不请求。
- `getSkillLabel` 单测：命中 zh/en、未知 id 回退原始 id（**不得返回 `'-'`**）。
- UI 治理测试 `tests/ui_governance.test.ts` 需通过：本次不在 `span`/`div` 上使用
  原生 `title` 属性。

**Python**：

```bash
python -m pytest tests/test_skill_label_parity.py
./scripts/ci_gate.sh
```

**证据**：按 `AGENTS.md`，报告渲染 / Web UI 改动必须在 PR 描述附受影响页面截图，
本次需附**改造前后对比**。

## 8. 文档同步

- `docs/CHANGELOG.md` 的 `[Unreleased]` 追加**扁平格式**条目（不得新增
  `### 类目标题`），类型取 `改进` / `修复` / `测试`。
- `docs/decision-signals.md` 的「Web 展示」章节同步：待处理分档规则、
  `holdingOnly` 开关、统计区降级、Skill 中文名映射与未知 id 回退语义。
- `README.md` **不更新**（首页级信息，无变化）。

## 9. 风险与回滚

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 「可以动手」长期为空 | 分档依赖 `plan_quality = complete`；若长期大部分信号为 `minimal/unknown`，该档会变成新的空态 | 实测当前 8 条中 6 条为 `complete`；空态文案需明确解释「为什么空」而不是只写「暂无」 |
| 100 条上限静默截断 | 超过 100 条 active 时待处理区不完整 | 复用 `dedupeNote` 形式的可见文案，明确写出覆盖范围 |
| 统计区懒加载改变既有行为 | 原先挂载即请求，改为展开才请求 | 在 PR 描述中显式声明为行为变更；不影响「刷新」按钮语义 |
| 前后端两份 skill 映射漂移 | 前端映射是事实上的第二份副本 | `tests/test_skill_label_parity.py` 强制同步 |
| 分档纯函数被后续改动悄悄改语义 | 分档是页面核心语义 | 纯函数 + 边界单测独立成文件 |

**回滚方式**：改动全部集中在
`apps/dsa-web/src/pages/DecisionSignalsPage.tsx`、
`apps/dsa-web/src/components/decision-signals/`、
`apps/dsa-web/src/utils/decisionSignalLabels.ts`、
`apps/dsa-web/src/i18n/uiText.ts` 与新增测试文件，无后端与数据库变更。
回滚即 revert 对应前端提交；防漂移测试 `tests/test_skill_label_parity.py`
需与前端映射同进同退，不可单独保留。

## 10. 交付结构

按 `AGENTS.md` §9：改了什么 / 为什么这么改 / 验证情况 / 未验证项 / 风险点 / 回滚方式。
