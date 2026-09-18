# 决策信号页「行动优先」重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/decision-signals` 从「AI 说过什么」的档案馆改造为「我现在该做什么」的工作台——首屏按可执行性分档、接入持仓过滤、把统计面板降级，并让 Skill 列显示中文名。

**Architecture:** 纯 Web 前端重组 + 一个跨语言一致性测试。新增一个纯函数模块做信号分档、一个展示组件渲染三档队列、给通用 `Collapsible` 补受控模式；页面把原有「全部信号」「单股追踪」两块原样保留，只在它们之上新增首屏队列、在它们之下新增折叠统计区。后端、API 契约、数据库均不动。

**Tech Stack:** React 19 + TypeScript + Vite + Tailwind、Vitest + @testing-library/react（`apps/dsa-web`）、pytest（仓库根）。

**Spec:** `docs/superpowers/specs/2026-09-13-decision-signals-action-first-design.md`

## Global Constraints

- 后端、API 契约、数据库、`.env.example`、config registry **一律不动**。本计划不新增任何后端配置项。
- 不新增后端 endpoint。复用既有 `GET /api/v1/decision-signals`（`holding_only` 参数已存在）与 `GET /skill-outcomes/stats`。
- `apps/dsa-web/tests/ui_governance.test.ts` 禁止在 `button`/`a`/`input`/`textarea`/`select`/`div`/`span` 上使用原生 `title` 属性 —— 新增元素一律不得带 `title=`。
- `apps/dsa-web/src/i18n/uiText.ts` 中 `UiTextKey = keyof typeof zh`（:1112），`en` 类型为 `Record<UiTextKey, string>`（:1114）。**新增 key 必须同时补 zh 与 en**，否则 `tsc -b` 失败。
- 未知 skill id 的标签**必须回退为原始 id 本身，禁止返回 `'-'`**（与既有 `translatedKnownValue` 行为不同，见 Task 1）。
- 分档中未知 `action` / `plan_quality` **一律归入 `watch`**，不设兜底桶。
- 分档区请求上限 `pageSize = 100`，超过时必须显示可见文案，不得静默截断。
- 遵循 `AGENTS.md`：注释与文档用中文，与文件语境一致；不写死路径/端口/密钥。
- 提交信息用英文，**不得添加 `Co-Authored-By`**。未经用户明确确认不执行 `git push` 或创建 PR。
- 每个 Task 结束后跑对应测试命令；Task 8 执行完整验证矩阵。

---

### Task 1: Skill 中文名标签（工具函数 + i18n + 表格接线）

**Files:**
- Modify: `apps/dsa-web/src/utils/decisionSignalLabels.ts`（在文件末尾追加）
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh 块约 :361-397 区域追加；en 块约 :1471-1507 区域追加）
- Modify: `apps/dsa-web/src/pages/DecisionSignalsPage.tsx:1689`
- Test: `apps/dsa-web/src/utils/__tests__/decisionSignalLabels.test.ts`

**Interfaces:**
- Consumes: 无（本任务是第一个）
- Produces:
  - `getSkillLabel(skillId: string | null | undefined, t: Translator): string` —— 命中映射时返回译文，未命中时**返回原始 id**，空值时返回 `'-'`。
  - `SKILL_LABEL_KEYS: Record<string, UiTextKey>` —— key 为 skill id，value 为 `decisionSignals.skill.<id>`。
  - i18n key 命名约定：`decisionSignals.skill.<skill_id>`。

- [ ] **Step 1: 写失败测试**

在 `apps/dsa-web/src/utils/__tests__/decisionSignalLabels.test.ts` 的 import 中追加 `getSkillLabel`，并在文件末尾追加一个新的 describe 块：

```ts
describe('getSkillLabel', () => {
  const skillLabels: Partial<Record<UiTextKey, string>> = {
    'decisionSignals.skill.bull_trend': '默认多头趋势',
    'decisionSignals.skill.box_oscillation': '箱体震荡',
  };
  const skillT = (key: UiTextKey): string => skillLabels[key] ?? '';

  it('maps known skill ids through explicit i18n keys', () => {
    expect(getSkillLabel('bull_trend', skillT)).toBe('默认多头趋势');
    expect(getSkillLabel('box_oscillation', skillT)).toBe('箱体震荡');
  });

  it('falls back to the raw skill id for unknown skills instead of a dash', () => {
    // skill 是开放集合（AGENT_SKILLS 可配置自定义策略），
    // 未知 id 必须回退原始值；返回 '-' 会让整列变成横杠，比显示英文 id 更糟。
    expect(getSkillLabel('my_custom_skill', skillT)).toBe('my_custom_skill');
  });

  it('renders a dash only for empty input', () => {
    expect(getSkillLabel(null, skillT)).toBe('-');
    expect(getSkillLabel(undefined, skillT)).toBe('-');
    expect(getSkillLabel('', skillT)).toBe('-');
    expect(getSkillLabel('   ', skillT)).toBe('-');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/decisionSignalLabels.test.ts`
Expected: FAIL —— `getSkillLabel is not a function` / 导入报错 `does not provide an export named 'getSkillLabel'`

- [ ] **Step 3: 在 `decisionSignalLabels.ts` 末尾实现**

追加以下内容（`SKILL_LABEL_KEYS` 与文件既有 `*_LABEL_KEYS` 同风格；`getSkillLabel` **故意不复用** `translatedKnownValue`）：

```ts
// 与后端 src/report_language.py 的 _STRATEGY_SKILL_TRANSLATIONS 对应；
// 由 tests/test_skill_label_parity.py 保证两边不漂移。
const SKILL_LABEL_KEYS: Record<string, UiTextKey> = {
  bull_trend: 'decisionSignals.skill.bull_trend',
  hot_theme: 'decisionSignals.skill.hot_theme',
  volume_breakout: 'decisionSignals.skill.volume_breakout',
  ma_golden_cross: 'decisionSignals.skill.ma_golden_cross',
  growth_quality: 'decisionSignals.skill.growth_quality',
  bottom_volume: 'decisionSignals.skill.bottom_volume',
  box_oscillation: 'decisionSignals.skill.box_oscillation',
  chan_theory: 'decisionSignals.skill.chan_theory',
  dragon_head: 'decisionSignals.skill.dragon_head',
  emotion_cycle: 'decisionSignals.skill.emotion_cycle',
  event_driven: 'decisionSignals.skill.event_driven',
  expectation_repricing: 'decisionSignals.skill.expectation_repricing',
  one_yang_three_yin: 'decisionSignals.skill.one_yang_three_yin',
  shrink_pullback: 'decisionSignals.skill.shrink_pullback',
  wave_theory: 'decisionSignals.skill.wave_theory',
};

/**
 * Skill 是开放集合（AGENT_SKILLS 支持自定义策略），因此未知 id 必须回退为原始
 * id —— 不能像 translatedKnownValue 那样返回 '-'，否则整列会变成横杠，
 * 比显示英文 id 更不可读。
 */
export function getSkillLabel(
  skillId: string | null | undefined,
  t: Translator,
): string {
  const normalized = typeof skillId === 'string' ? skillId.trim() : '';
  if (!normalized) return '-';
  const key = SKILL_LABEL_KEYS[normalized];
  if (!key) return normalized;
  return t(key) || normalized;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/decisionSignalLabels.test.ts`
Expected: PASS（原有 2 个用例 + 新增 3 个用例全绿）

- [ ] **Step 5: 补 i18n 文案**

在 `apps/dsa-web/src/i18n/uiText.ts` 的 **zh 对象**中，紧跟 `'decisionSignals.skillStatsSampleStatus.observational': '样本不足',` 之后追加：

```ts
  'decisionSignals.skill.bull_trend': '默认多头趋势',
  'decisionSignals.skill.hot_theme': '热点题材',
  'decisionSignals.skill.volume_breakout': '放量突破',
  'decisionSignals.skill.ma_golden_cross': '均线金叉',
  'decisionSignals.skill.growth_quality': '成长质量',
  'decisionSignals.skill.bottom_volume': '底部放量',
  'decisionSignals.skill.box_oscillation': '箱体震荡',
  'decisionSignals.skill.chan_theory': '缠论结构',
  'decisionSignals.skill.dragon_head': '龙头战法',
  'decisionSignals.skill.emotion_cycle': '情绪周期',
  'decisionSignals.skill.event_driven': '事件驱动',
  'decisionSignals.skill.expectation_repricing': '预期重估',
  'decisionSignals.skill.one_yang_three_yin': '一阳三阴',
  'decisionSignals.skill.shrink_pullback': '缩量回踩',
  'decisionSignals.skill.wave_theory': '波浪理论',
```

在 **en 对象**中，紧跟 `'decisionSignals.skillStatsSampleStatus.observational': 'Insufficient',` 之后追加：

```ts
  'decisionSignals.skill.bull_trend': 'Bull Trend',
  'decisionSignals.skill.hot_theme': 'Hot Theme',
  'decisionSignals.skill.volume_breakout': 'Volume Breakout',
  'decisionSignals.skill.ma_golden_cross': 'MA Golden Cross',
  'decisionSignals.skill.growth_quality': 'Growth Quality',
  'decisionSignals.skill.bottom_volume': 'Bottom Volume',
  'decisionSignals.skill.box_oscillation': 'Box Oscillation',
  'decisionSignals.skill.chan_theory': 'Chan Theory',
  'decisionSignals.skill.dragon_head': 'Dragon Head',
  'decisionSignals.skill.emotion_cycle': 'Emotion Cycle',
  'decisionSignals.skill.event_driven': 'Event Driven',
  'decisionSignals.skill.expectation_repricing': 'Expectation Repricing',
  'decisionSignals.skill.one_yang_three_yin': 'One Yang Three Yin',
  'decisionSignals.skill.shrink_pullback': 'Shrink Pullback',
  'decisionSignals.skill.wave_theory': 'Wave Theory',
```

- [ ] **Step 6: 把表格接线到 `getSkillLabel`**

修改 `apps/dsa-web/src/pages/DecisionSignalsPage.tsx`。该页**已经**从 `../utils/decisionSignalLabels`
导入（`:55-61`），因此**不要新增一行 import**（会产生同一模块的重复导入，触发 lint），
而是把 `getSkillLabel` 加进既有的那个花括号块里，保持字母序：

```ts
import {
  getDecisionSignalHorizonLabel,
  getDecisionSignalMarketLabel,
  getDecisionSignalMarketPhaseLabel,
  getDecisionSignalSourceTypeLabel,
  getSkillLabel,
  getSkillOpinionSampleStatusLabel,
} from '../utils/decisionSignalLabels';
```

然后修改 :1689，把裸 `skillId` 换成标签：

```tsx
                  <span className="min-w-0 truncate font-medium text-foreground">
                    {getSkillLabel(bucket.skillId, t)}
                    <span className="ml-2 text-secondary-text">{bucket.horizon}</span>
                  </span>
```

- [ ] **Step 7: 跑类型检查与相关测试**

Run: `cd apps/dsa-web && npx tsc -b && npx vitest run src/utils/__tests__/decisionSignalLabels.test.ts`
Expected: tsc 无错误（证明 en 块未漏 key），测试 PASS

- [ ] **Step 8: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/utils/decisionSignalLabels.ts \
        apps/dsa-web/src/utils/__tests__/decisionSignalLabels.test.ts \
        apps/dsa-web/src/i18n/uiText.ts \
        apps/dsa-web/src/pages/DecisionSignalsPage.tsx
git commit -m "feat: localize skill ids in decision signal performance table"
```

---

### Task 2: 跨语言 Skill 标签一致性测试

**Files:**
- Create: `tests/test_skill_label_parity.py`
- Test: 本文件自身

**Interfaces:**
- Consumes: Task 1 产出的 `decisionSignals.skill.<id>` i18n key，以及 Python 侧既有的 `_STRATEGY_SKILL_TRANSLATIONS`。
- Produces: 无（纯守护测试）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_skill_label_parity.py`：

```python
# -*- coding: utf-8 -*-
"""保证 Web 侧 skill 中文名映射与后端 _STRATEGY_SKILL_TRANSLATIONS 不漂移。

Skill 是开放集合，但内建策略集合由后端权威维护。Web 需要一份独立映射才能把
skill_id 渲染为用户可读标签（见 docs/decision-signals.md「Web 展示」章节：
API 保留原始枚举值，Web 负责映射）。本测试防止后端新增策略而前端漏补。
"""

from __future__ import annotations

import re
from pathlib import Path

from src import report_language


ROOT = Path(__file__).resolve().parents[1]
UI_TEXT_PATH = "apps/dsa-web/src/i18n/uiText.ts"

# zh 块与 en 块的分界：UiTextKey 由 zh 推导，en 声明为 Record<UiTextKey, string>。
_EN_BLOCK_MARKER = "const en: Record<UiTextKey, string> = {"


def _ui_text_source() -> str:
    return (ROOT / UI_TEXT_PATH).read_text(encoding="utf-8")


def test_every_backend_strategy_skill_has_a_web_label() -> None:
    source = _ui_text_source()
    marker_index = source.index(_EN_BLOCK_MARKER)
    zh_block = source[:marker_index]
    en_block = source[marker_index:]

    missing_zh: list[str] = []
    missing_en: list[str] = []

    for skill_id in report_language._STRATEGY_SKILL_TRANSLATIONS:
        # 用带引号的完整 key 匹配，避免 bull_trend 误命中 decisionSignals.skill.bull_trend_v2
        pattern = re.compile(rf"['\"]decisionSignals\.skill\.{re.escape(skill_id)}['\"]\s*:")
        if not pattern.search(zh_block):
            missing_zh.append(skill_id)
        if not pattern.search(en_block):
            missing_en.append(skill_id)

    assert missing_zh == [], (
        f"以下 skill 缺少中文名 i18n key decisionSignals.skill.<id>：{missing_zh}"
    )
    assert missing_en == [], (
        f"以下 skill 缺少英文名 i18n key decisionSignals.skill.<id>：{missing_en}"
    )


def test_web_skill_map_covers_backend_skill_translations() -> None:
    """反向检查：Web 的 SKILL_LABEL_KEYS 不得出现后端未知的策略。

    Web 映射允许多于后端（历史策略），但不得凭空发明后端不存在的 id，
    否则说明两边对「内建策略集合」的理解已经分叉。
    """
    source = (ROOT / "apps/dsa-web/src/utils/decisionSignalLabels.ts").read_text(
        encoding="utf-8"
    )
    block_start = source.index("const SKILL_LABEL_KEYS")
    block_end = source.index("};", block_start)
    block = source[block_start:block_end]

    web_ids = set(re.findall(r"^\s*([a-z0-9_]+):\s*'decisionSignals\.skill\.", block, re.M))
    backend_ids = set(report_language._STRATEGY_SKILL_TRANSLATIONS)

    assert web_ids, "未能从 decisionSignalLabels.ts 解析出任何 skill id，解析逻辑需要更新"
    assert web_ids == backend_ids, (
        "Web 与后端的 skill 集合不一致："
        f"仅 Web 有 {sorted(web_ids - backend_ids)}；"
        f"仅后端有 {sorted(backend_ids - web_ids)}"
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/anwen/Documents/开发/dsa && uv run python -m pytest tests/test_skill_label_parity.py -v`
Expected: 若 Task 1 尚未完成则 FAIL（`DecisionSignals.skill.*` 缺失）；Task 1 已完成则两个用例应 PASS。**先确认 `_STRATEGY_SKILL_TRANSLATIONS` 与 `SKILL_LABEL_KEYS` 的 id 集合实际一致**，不一致则按后端为准修正 Task 1 的映射。

- [ ] **Step 3: 修正到通过**

若 Step 2 报出集合差异，逐条核对 `src/report_language.py:245` 的 `_STRATEGY_SKILL_TRANSLATIONS` 与 `decisionSignalLabels.ts` 的 `SKILL_LABEL_KEYS`，以**后端为权威**补齐或删除前端条目，并同步 `uiText.ts` 的 zh / en。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/anwen/Documents/开发/dsa && uv run python -m pytest tests/test_skill_label_parity.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

> **执行期修订（Task 1 复核发现，controller 裁定，详见 `.superpowers/sdd/2026-09-13-decision-signals-action-first/progress.md`）**
>
> Step 1 追加第三个用例 `test_web_skill_label_text_matches_backend_text` 与其 helper
> `_parse_web_skill_labels`（完整代码见该任务 brief 文件）。原因：原两个用例只比对
> **key 是否存在**与 **id 集合**，无法发现**文案改字**——把 `wave_theory` 的中文写成
> 「海浪理论」时仍然全绿；而 Task 1 已在 `decisionSignalLabels.ts` 留下
> 「由 tests/test_skill_label_parity.py 保证两边不漂移」的注释，缺少该用例时是过强声明。
> `_STRATEGY_SKILL_TRANSLATIONS` 每条自带 `zh` / `en`（`ko` 无对应 Web 语言，不比对）。
> 因此 Expected 由 `2 passed` 改为 `3 passed`。原有用例与 id 集合核对逻辑一行未改。
>
> **本计划执行期裁定为「只做实现，不提交」：跳过下面的 `git commit`，不要执行 `git add` / `git commit`。**

```bash
cd /Users/anwen/Documents/开发/dsa
git add tests/test_skill_label_parity.py
git commit -m "test: guard web skill labels against backend skill translation drift"
```

---

### Task 3: 信号分档纯函数

**Files:**
- Create: `apps/dsa-web/src/utils/decisionSignalQueue.ts`
- Test: `apps/dsa-web/src/utils/__tests__/decisionSignalQueue.test.ts`

**Interfaces:**
- Consumes: `DecisionSignalItem`（`apps/dsa-web/src/types/decisionSignals.ts:20`，`action: DecisionAction`、`planQuality: DecisionSignalPlanQuality`）
- Produces:
  - `type DecisionSignalQueueGroup = 'actionable' | 'needs_review' | 'watch'`
  - `classifyDecisionSignalQueue(item: Pick<DecisionSignalItem, 'action' | 'planQuality'>): DecisionSignalQueueGroup`
  - `groupDecisionSignalsByQueue(items: readonly DecisionSignalItem[]): Record<DecisionSignalQueueGroup, DecisionSignalItem[]>`
  - `DECISION_SIGNAL_QUEUE_ORDER: readonly DecisionSignalQueueGroup[]`

- [ ] **Step 1: 写失败测试**

创建 `apps/dsa-web/src/utils/__tests__/decisionSignalQueue.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import {
  DECISION_SIGNAL_QUEUE_ORDER,
  classifyDecisionSignalQueue,
  groupDecisionSignalsByQueue,
} from '../decisionSignalQueue';
import type { DecisionSignalItem } from '../../types/decisionSignals';

const signal = (
  action: DecisionSignalItem['action'],
  planQuality: DecisionSignalItem['planQuality'],
  id = 1,
): DecisionSignalItem => ({ id, action, planQuality } as DecisionSignalItem);

describe('classifyDecisionSignalQueue', () => {
  it('puts position-changing actions with a complete plan into actionable', () => {
    expect(classifyDecisionSignalQueue(signal('buy', 'complete'))).toBe('actionable');
    expect(classifyDecisionSignalQueue(signal('add', 'complete'))).toBe('actionable');
    expect(classifyDecisionSignalQueue(signal('reduce', 'complete'))).toBe('actionable');
    expect(classifyDecisionSignalQueue(signal('sell', 'complete'))).toBe('actionable');
  });

  it('keeps position-changing actions with an incomplete plan out of actionable', () => {
    // 没有止损位的 buy 不该被推成「可以动手」，那等于鼓励盲买。
    expect(classifyDecisionSignalQueue(signal('buy', 'partial'))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('buy', 'minimal'))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('sell', 'unknown'))).toBe('needs_review');
  });

  it('puts non-position-changing actions into watch regardless of plan quality', () => {
    expect(classifyDecisionSignalQueue(signal('hold', 'complete'))).toBe('watch');
    expect(classifyDecisionSignalQueue(signal('watch', 'complete'))).toBe('watch');
    expect(classifyDecisionSignalQueue(signal('avoid', 'complete'))).toBe('watch');
    expect(classifyDecisionSignalQueue(signal('alert', 'complete'))).toBe('watch');
  });

  it('routes unknown action or plan quality values to watch', () => {
    // 后端枚举扩展或历史脏数据不得被误报成可执行。
    expect(classifyDecisionSignalQueue({ action: 'rebalance' as never, planQuality: 'complete' as never })).toBe('watch');
    expect(classifyDecisionSignalQueue({ action: 'buy' as never, planQuality: 'excellent' as never })).toBe('needs_review');
  });
});

describe('groupDecisionSignalsByQueue', () => {
  it('splits items into the three groups without dropping any', () => {
    const items = [
      signal('buy', 'complete', 1),
      signal('buy', 'minimal', 2),
      signal('watch', 'complete', 3),
      signal('sell', 'complete', 4),
    ];
    const grouped = groupDecisionSignalsByQueue(items);

    expect(grouped.actionable.map((item) => item.id)).toEqual([1, 4]);
    expect(grouped.needs_review.map((item) => item.id)).toEqual([2]);
    expect(grouped.watch.map((item) => item.id)).toEqual([3]);
    const total = DECISION_SIGNAL_QUEUE_ORDER.reduce((sum, group) => sum + grouped[group].length, 0);
    expect(total).toBe(items.length);
  });

  it('returns empty groups for an empty input', () => {
    const grouped = groupDecisionSignalsByQueue([]);
    expect(grouped).toEqual({ actionable: [], needs_review: [], watch: [] });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/decisionSignalQueue.test.ts`
Expected: FAIL —— 无法解析模块 `../decisionSignalQueue`

- [ ] **Step 3: 实现 `apps/dsa-web/src/utils/decisionSignalQueue.ts`**

```ts
import type { DecisionSignalItem } from '../types/decisionSignals';

/** 分档结果：会改变仓位的动作按计划完整度拆成两档，其余归入观察。 */
export type DecisionSignalQueueGroup = 'actionable' | 'needs_review' | 'watch';

/** 渲染顺序，也是「行动优先级」顺序。 */
export const DECISION_SIGNAL_QUEUE_ORDER: readonly DecisionSignalQueueGroup[] = [
  'actionable',
  'needs_review',
  'watch',
];

/** 会改变仓位的动作；其余（hold/watch/avoid/alert）不需要立即动手。 */
const POSITION_CHANGING_ACTIONS = new Set<string>(['buy', 'add', 'reduce', 'sell']);

const COMPLETE_PLAN_QUALITY = 'complete';

/**
 * 判定一条信号落在哪一档。
 *
 * 动作与计划完整度是「与」的关系：一个没有止损位的 buy 不应进入 actionable，
 * 那等于鼓励盲买。未知 action / plan_quality 一律归入 watch —— 分档的方向是
 * 「告诉用户该动手」，宁可不提示也不能误报。
 */
export function classifyDecisionSignalQueue(
  item: Pick<DecisionSignalItem, 'action' | 'planQuality'>,
): DecisionSignalQueueGroup {
  const action = typeof item.action === 'string' ? item.action.trim() : '';
  if (!POSITION_CHANGING_ACTIONS.has(action)) return 'watch';

  const planQuality = typeof item.planQuality === 'string' ? item.planQuality.trim() : '';
  return planQuality === COMPLETE_PLAN_QUALITY ? 'actionable' : 'needs_review';
}

/** 按三档分组；不做过滤，入参的每一条都会出现在某一档里。 */
export function groupDecisionSignalsByQueue(
  items: readonly DecisionSignalItem[],
): Record<DecisionSignalQueueGroup, DecisionSignalItem[]> {
  const grouped: Record<DecisionSignalQueueGroup, DecisionSignalItem[]> = {
    actionable: [],
    needs_review: [],
    watch: [],
  };
  for (const item of items) {
    grouped[classifyDecisionSignalQueue(item)].push(item);
  }
  return grouped;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/decisionSignalQueue.test.ts`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/utils/decisionSignalQueue.ts \
        apps/dsa-web/src/utils/__tests__/decisionSignalQueue.test.ts
git commit -m "feat: add decision signal actionability classifier"
```

---

### Task 4: `Collapsible` 支持受控模式

**Files:**
- Modify: `apps/dsa-web/src/components/common/Collapsible.tsx`
- Test: `apps/dsa-web/src/components/common/__tests__/Collapsible.test.tsx`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `Collapsible` 新增两个可选 prop：`open?: boolean`、`onOpenChange?: (open: boolean) => void`。两者均不传时保持原有非受控行为（内部 `useState`，默认取 `defaultOpen`）。

**背景：** 当前唯一消费者 `components/report/MarketReviewReportView.tsx:723` 只传 `defaultOpen` / `title` / `icon` / `className`，因此新增可选 prop 向后兼容。页面需要感知展开状态才能在展开时才发起统计请求，同时需要「未展开不挂载子内容」——`Collapsible` 现有实现用 `max-h-0 opacity-0` 隐藏，子节点仍然挂载。

- [ ] **Step 1: 写失败测试**

创建 `apps/dsa-web/src/components/common/__tests__/Collapsible.test.tsx`：

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Collapsible } from '../Collapsible';

describe('Collapsible', () => {
  it('keeps internal open state when uncontrolled', () => {
    render(
      <Collapsible title="面板">
        <p>内容</p>
      </Collapsible>,
    );
    const toggle = screen.getByRole('button', { name: '面板' });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('honours defaultOpen when uncontrolled', () => {
    render(
      <Collapsible title="面板" defaultOpen>
        <p>内容</p>
      </Collapsible>,
    );
    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('reports toggle intent without changing its own rendered state when controlled', () => {
    const onOpenChange = vi.fn();
    render(
      <Collapsible title="面板" open={false} onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );
    const toggle = screen.getByRole('button', { name: '面板' });

    fireEvent.click(toggle);

    expect(onOpenChange).toHaveBeenCalledWith(true);
    // 受控模式下由父组件决定是否展开；父组件未回传新值时保持收起。
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('renders expanded content when controlled open is true', () => {
    render(
      <Collapsible title="面板" open onOpenChange={() => undefined}>
        <p>内容</p>
      </Collapsible>,
    );
    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'true');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/components/common/__tests__/Collapsible.test.tsx`
Expected: FAIL —— 受控用例失败（点击后 `aria-expanded` 变成 `'true'`，而断言期望 `'false'`；`onOpenChange` 未被调用）

- [ ] **Step 3: 改造 `Collapsible.tsx`**

把 props 与内部状态改为受控/非受控双模式。替换 `Collapsible.tsx:4-38`（props 接口、
docstring、组件签名、内部 state、以及 `<button>` 的开标签），其余 JSX（:40-62）保持不动。

**`:35-37` 的 `aria-expanded` 及其上方两行注释是该文件既有的**（已由前一位维护者加上，
理由写在注释里），必须**原样保留**，不得当成新代码重新引入，也不得删除：

```tsx
interface CollapsibleProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  /** 传入即为受控模式；展开状态由父组件持有。 */
  open?: boolean;
  /** 受控模式下点击标题栏时回调期望的新状态。 */
  onOpenChange?: (open: boolean) => void;
  icon?: React.ReactNode;
  className?: string;
}

/**
 * Collapsible panel with animated expand and collapse behavior.
 *
 * 同时支持受控与非受控：不传 `open` 时用内部 state（原行为），传了 `open` 时
 * 完全由父组件决定展开状态，本组件只通过 `onOpenChange` 上报点击意图。
 * 父组件需要「未展开不挂载子内容」时用受控模式，非受控模式的子节点始终挂载。
 */
export const Collapsible: React.FC<CollapsibleProps> = ({
  title,
  children,
  defaultOpen = false,
  open,
  onOpenChange,
  icon,
  className = '',
}) => {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : uncontrolledOpen;

  const handleToggle = () => {
    if (!isControlled) {
      setUncontrolledOpen(!isOpen);
    }
    onOpenChange?.(!isOpen);
  };

  return (
    <div
      className={cn(
        'overflow-hidden rounded-2xl border border-subtle bg-card/70 shadow-soft-card transition-[background-color,box-shadow] duration-300',
        'hover:border-accent',
        className,
      )}
    >
      <button
        type="button"
        onClick={handleToggle}
        // ↓ 以下注释与 aria-expanded 是既有代码（原 :35-37），逐字保留
        // 展开态此前只由箭头 icon 的旋转表达，读屏用户拿不到状态；
        // 仓库其他折叠组件（RunFlowNodeDetails / RunFlowGraph / TaskPanel）都带该属性。
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-hover"
      >
```

组件其余部分（含 `cn('...', isOpen && 'rotate-180')` 与内容区 `isOpen ? ... : ...`）只需把原先引用的 `isOpen` 继续使用——原来是 `const [isOpen, setIsOpen] = useState(defaultOpen)`，现在 `isOpen` 由上面推导，**其余 JSX 一行都不用改**。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd apps/dsa-web && npx vitest run src/components/common/__tests__/Collapsible.test.tsx`
Expected: 4 passed

- [ ] **Step 5: 确认既有消费者未被破坏**

Run: `cd apps/dsa-web && npx vitest run src/components/report/__tests__ 2>/dev/null || npx vitest run --reporter=dot src/components`
Expected: 全绿。若 `MarketReviewReportView` 相关用例失败，说明非受控路径被改坏，回 Step 3 修正。

- [ ] **Step 6: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/components/common/Collapsible.tsx \
        apps/dsa-web/src/components/common/__tests__/Collapsible.test.tsx
git commit -m "feat: add controlled mode to Collapsible"
```

---

### Task 5: 待处理队列展示组件

**Files:**
- Create: `apps/dsa-web/src/components/decision-signals/DecisionSignalActionQueue.tsx`
- Test: `apps/dsa-web/src/components/decision-signals/__tests__/DecisionSignalActionQueue.test.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh + en 追加队列文案）

**Interfaces:**
- Consumes: `classifyDecisionSignalQueue` / `groupDecisionSignalsByQueue` / `DECISION_SIGNAL_QUEUE_ORDER`（Task 3）；`DecisionSignalCard`（`./DecisionSignalDisplay`）；`Collapsible`（Task 4）。
- Produces:
  ```ts
  type DecisionSignalActionQueueProps = {
    items: readonly DecisionSignalItem[];
    loading: boolean;
    error: ParsedApiError | null;
    onRetry: () => void;
    onSelect: (item: DecisionSignalItem) => void;
    selectedId: number | null;
  };
  export const DecisionSignalActionQueue: React.FC<DecisionSignalActionQueueProps>;
  ```
- i18n key：`decisionSignals.queueActionable` / `queueNeedsReview` / `queueWatch` / `queueActionableHint` / `queueNeedsReviewHint` / `queueWatchHint` / `queueGroupEmpty` / `queueCount` / `queueEmptyTitle` / `queueEmptyDescription` / `queueTruncatedNote`。

- [ ] **Step 1: 补 i18n 文案**

在 `apps/dsa-web/src/i18n/uiText.ts` 的 **zh 对象**中，紧跟 Task 1 追加的 `'decisionSignals.skill.wave_theory': '波浪理论',` 之后追加：

```ts
  'decisionSignals.queueActionable': '可以动手',
  'decisionSignals.queueActionableHint': '动作明确且计划完整（含入场、止损、目标价），可按计划执行。',
  'decisionSignals.queueNeedsReview': '需要确认',
  'decisionSignals.queueNeedsReviewHint': '有明确动作，但计划不完整，需自行补判断。',
  'decisionSignals.queueWatch': '观察',
  'decisionSignals.queueWatchHint': '持有、观望或回避类信号，无需立即动作。',
  'decisionSignals.queueGroupEmpty': '本组暂无信号。',
  'decisionSignals.queueCount': '{count} 条',
  'decisionSignals.queueEmptyTitle': '暂无待处理信号',
  'decisionSignals.queueEmptyDescription': '当前没有启用中的决策信号。',
  'decisionSignals.queueTruncatedNote': '仅在最近 {fetched} 条启用中的信号内分档。',
```

在 **en 对象**中，紧跟 `'decisionSignals.skill.wave_theory': 'Wave Theory',` 之后追加：

```ts
  'decisionSignals.queueActionable': 'Actionable',
  'decisionSignals.queueActionableHint': 'Clear action with a complete plan (entry, stop loss, target). Can be executed as planned.',
  'decisionSignals.queueNeedsReview': 'Needs review',
  'decisionSignals.queueNeedsReviewHint': 'A clear action, but the plan is incomplete. Fill in the missing judgement yourself.',
  'decisionSignals.queueWatch': 'Watch',
  'decisionSignals.queueWatchHint': 'Hold, watch or avoid signals. No immediate action needed.',
  'decisionSignals.queueGroupEmpty': 'No signals in this group.',
  'decisionSignals.queueCount': '{count}',
  'decisionSignals.queueEmptyTitle': 'Nothing pending',
  'decisionSignals.queueEmptyDescription': 'There are no active decision signals right now.',
  'decisionSignals.queueTruncatedNote': 'Grouped within the most recent {fetched} active signals only.',
```

- [ ] **Step 2: 写失败测试**

创建 `apps/dsa-web/src/components/decision-signals/__tests__/DecisionSignalActionQueue.test.tsx`：

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DecisionSignalActionQueue } from '../DecisionSignalActionQueue';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import type { DecisionSignalItem } from '../../../types/decisionSignals';

const signal = (
  id: number,
  stockName: string,
  action: DecisionSignalItem['action'],
  planQuality: DecisionSignalItem['planQuality'],
): DecisionSignalItem => ({
  id,
  stockCode: String(id).padStart(6, '0'),
  stockName,
  action,
  planQuality,
  status: 'active',
  market: 'cn',
  sourceType: 'analysis',
  decisionProfile: 'balanced',
  createdAt: '2026-06-17T09:30:00Z',
} as DecisionSignalItem);

const renderQueue = (items: DecisionSignalItem[]) =>
  render(
    <UiLanguageProvider>
      <DecisionSignalActionQueue
        items={items}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onSelect={vi.fn()}
        selectedId={null}
      />
    </UiLanguageProvider>,
  );

describe('DecisionSignalActionQueue', () => {
  it('renders the three groups with their items', () => {
    renderQueue([
      signal(1, '贵州茅台', 'buy', 'complete'),
      signal(2, '宁德时代', 'buy', 'minimal'),
      signal(3, '腾讯控股', 'watch', 'complete'),
    ]);

    expect(screen.getByRole('heading', { name: '可以动手' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '需要确认' })).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('宁德时代')).toBeInTheDocument();
  });

  it('collapses the watch group by default and expands it on demand', () => {
    renderQueue([signal(3, '腾讯控股', 'watch', 'complete')]);

    // Collapsible 通过 max-h-0 + opacity-0 隐藏内容，子节点始终挂载，
    // 因此这里断言的是可访问的展开状态（aria-expanded），不是元素是否存在。
    const watchToggle = screen.getByRole('button', { name: /^观察/ });
    expect(watchToggle).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(watchToggle);

    expect(watchToggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows the whole-queue empty state when there is nothing to do', () => {
    renderQueue([]);
    expect(screen.getByText('暂无待处理信号')).toBeInTheDocument();
  });

  it('shows a per-group empty state for groups without signals', () => {
    renderQueue([signal(1, '贵州茅台', 'buy', 'complete')]);
    const reviewSection = screen.getByRole('heading', { name: '需要确认' }).closest('section');
    expect(reviewSection).not.toBeNull();
    expect(within(reviewSection as HTMLElement).getByText('本组暂无信号。')).toBeInTheDocument();
  });

  it('surfaces a retryable error alert', () => {
    const onRetry = vi.fn();
    render(
      <UiLanguageProvider>
        <DecisionSignalActionQueue
          items={[]}
          loading={false}
          // ApiErrorAlert 会读 error.rawMessage.trim()，漏掉该字段会在运行时抛错，
          // 因此夹具必须带上它（空串即可，会让「详情」折叠块不渲染）。
          error={{ title: 'x', message: '待处理信号加载失败', rawMessage: '' } as never}
          onRetry={onRetry}
          onSelect={vi.fn()}
          selectedId={null}
        />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/components/decision-signals/__tests__/DecisionSignalActionQueue.test.tsx`
Expected: FAIL —— 无法解析模块 `../DecisionSignalActionQueue`

- [ ] **Step 4: 实现组件**

创建 `apps/dsa-web/src/components/decision-signals/DecisionSignalActionQueue.tsx`：

```tsx
import type React from 'react';
import { ApiErrorAlert, EmptyState } from '../common';
import { Collapsible } from '../common/Collapsible';
import { Activity } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import type { DecisionSignalItem } from '../../types/decisionSignals';
import type { ParsedApiError } from '../../api/error';
import { DecisionSignalCard } from './DecisionSignalDisplay';
import {
  DECISION_SIGNAL_QUEUE_ORDER,
  groupDecisionSignalsByQueue,
  type DecisionSignalQueueGroup,
} from '../../utils/decisionSignalQueue';

export type DecisionSignalActionQueueProps = {
  items: readonly DecisionSignalItem[];
  loading: boolean;
  error: ParsedApiError | null;
  onRetry: () => void;
  onSelect: (item: DecisionSignalItem) => void;
  selectedId: number | null;
};

const GROUP_TITLE_KEYS: Record<DecisionSignalQueueGroup, UiTextKey> = {
  actionable: 'decisionSignals.queueActionable',
  needs_review: 'decisionSignals.queueNeedsReview',
  watch: 'decisionSignals.queueWatch',
};

const GROUP_HINT_KEYS: Record<DecisionSignalQueueGroup, UiTextKey> = {
  actionable: 'decisionSignals.queueActionableHint',
  needs_review: 'decisionSignals.queueNeedsReviewHint',
  watch: 'decisionSignals.queueWatchHint',
};

export const DecisionSignalActionQueue: React.FC<DecisionSignalActionQueueProps> = ({
  items,
  loading,
  error,
  onRetry,
  onSelect,
  selectedId,
}) => {
  const { t } = useUiLanguage();

  if (error) {
    return (
      <ApiErrorAlert
        error={{ ...error, title: t('decisionSignals.errorTitle') }}
        actionLabel={t('common.retry')}
        onAction={onRetry}
      />
    );
  }

  if (loading) {
    return <p className="text-sm text-secondary-text">{t('common.loading')}...</p>;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title={t('decisionSignals.queueEmptyTitle')}
        description={t('decisionSignals.queueEmptyDescription')}
        icon={<Activity className="h-7 w-7" />}
      />
    );
  }

  const grouped = groupDecisionSignalsByQueue(items);

  return (
    <div className="space-y-3">
      {DECISION_SIGNAL_QUEUE_ORDER.map((group) => {
        const groupItems = grouped[group];
        const title = t(GROUP_TITLE_KEYS[group]);
        // 观察档默认收起：它不需要立即动作，不该占据首屏。
        const collapsible = group === 'watch';

        const body =
          groupItems.length === 0 ? (
            <p className="text-sm text-secondary-text">{t('decisionSignals.queueGroupEmpty')}</p>
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {groupItems.map((item) => (
                <DecisionSignalCard
                  key={item.id}
                  item={item}
                  onSelect={onSelect}
                  selected={selectedId === item.id}
                />
              ))}
            </div>
          );

        const heading = (
          <span className="flex items-center gap-2">
            <span>{title}</span>
            <span className="text-xs font-normal text-secondary-text">
              {t('decisionSignals.queueCount', { count: groupItems.length })}
            </span>
          </span>
        );

        return (
          <section key={group} aria-labelledby={`queue-group-${group}`}>
            <h3 id={`queue-group-${group}`} className="sr-only">
              {title}
            </h3>
            <p className="mb-2 text-xs text-secondary-text">{t(GROUP_HINT_KEYS[group])}</p>
            {collapsible ? (
              // Collapsible 的 title 是 string，折叠档只能把条数拼进标题里；
              // spec §4 要求「观察」分组标题带条数。
              <Collapsible
                title={`${title} · ${t('decisionSignals.queueCount', { count: groupItems.length })}`}
              >
                {body}
              </Collapsible>
            ) : (
              <div className="rounded-2xl border border-border/70 bg-card/60 p-3">
                <div className="mb-3 font-medium text-foreground">{heading}</div>
                {body}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
};
```

> 说明：`heading` 变量仅用于非折叠档的可见标题；折叠档的标题由 `Collapsible` 自己渲染。`h3.sr-only` 同时为两档提供稳定的可访问名称，测试才能用 `getByRole('heading', { name: '可以动手' })` 定位。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd apps/dsa-web && npx vitest run src/components/decision-signals/__tests__/DecisionSignalActionQueue.test.tsx`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/components/decision-signals/DecisionSignalActionQueue.tsx \
        apps/dsa-web/src/components/decision-signals/__tests__/DecisionSignalActionQueue.test.tsx \
        apps/dsa-web/src/i18n/uiText.ts
git commit -m "feat: add grouped action queue component for decision signals"
```

---

### Task 6: 页面接入待处理队列与「只看我的持仓」

**Files:**
- Modify: `apps/dsa-web/src/pages/DecisionSignalsPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/DecisionSignalsPage.test.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh + en 追加 2 个 key）

**Interfaces:**
- Consumes: `DecisionSignalActionQueue`（Task 5）、`Checkbox`（`../components/common`）。
- Produces: 页面新增状态 `holdingOnly: boolean`，以及待处理区内部状态
  `rawQueueItems` / `rawQueueLoading` / `rawQueueError`；对外暴露的
  `queueItems` / `queueLoading` / `queueError` 是派生值（`dedupeLatest` 打开时复用主列表
  结果）。`SelectedSignal['source']` 联合类型新增 `'queue'`。

- [ ] **Step 1: 补 i18n 文案**

zh 对象追加（紧跟 Task 5 追加的 `'decisionSignals.queueTruncatedNote'` 之后）：

```ts
  'decisionSignals.queueTitle': '待处理',
  'decisionSignals.queueDescription': '按是否需要动手分组，可执行的信号排在前面。',
  'decisionSignals.holdingOnlyLabel': '只看我的持仓',
  // 待处理区有自己的「刷新」按钮。页面已有两个可见文案为「刷新」的按钮
  // （信号列表 / Skill 表现，见 skillStatsRefreshAria 上方注释），本按钮是第三个，
  // 必须有自己的可访问名称区分，**不得复用 skillStatsRefreshAria**——那会让读屏与
  // 语音控制把「刷新待处理」播报/匹配成「刷新 Skill 表现数据」。
  'decisionSignals.queueRefresh': '刷新',
  'decisionSignals.queueRefreshAria': '刷新待处理信号',
```

en 对象追加：

```ts
  'decisionSignals.queueTitle': 'Pending',
  'decisionSignals.queueDescription': 'Grouped by whether action is needed. Executable signals come first.',
  'decisionSignals.holdingOnlyLabel': 'My holdings only',
  'decisionSignals.queueRefresh': 'Refresh',
  'decisionSignals.queueRefreshAria': 'Refresh pending signals',
```

- [ ] **Step 2: 写失败测试**

在 `apps/dsa-web/src/pages/__tests__/DecisionSignalsPage.test.tsx` 中追加两个用例（放在 `describe('DecisionSignalsPage', ...)` 内）：

```tsx
  it('groups active signals into the action queue and widens the queue fetch to 100', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    // 主列表用 pageSize 20；待处理区单独拉 100 条做全貌分档。
    expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
      ([params]) => params?.status === 'active' && params?.pageSize === 100 && params?.stockCode === undefined,
    )).toBe(true);
    expect(await screen.findByRole('heading', { name: '可以动手' })).toBeInTheDocument();
  });

  it('passes holdingOnly to both the queue and the paged list when the toggle is on', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.click(screen.getByLabelText('只看我的持仓'));

    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.holdingOnly === true && params?.pageSize === 100,
      )).toBe(true);
    });
    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.holdingOnly === true && params?.pageSize === 20,
      )).toBe(true);
    });
  });
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/DecisionSignalsPage.test.tsx -t "action queue"`
Expected: FAIL —— 找不到 pageSize 100 的 active 请求；找不到「可以动手」标题

- [ ] **Step 4: 页面新增状态、加载函数与 holdingOnly 透传**

在 `apps/dsa-web/src/pages/DecisionSignalsPage.tsx` 的常量区（`const DEDUPE_PAGE_SIZE = 100;` 之后）追加：

```ts
const QUEUE_PAGE_SIZE = 100;
```

在 `type SelectedSignal`（:120）的 source 联合中追加 `'queue'`：

```ts
  source: 'list' | 'latest' | 'timeline' | 'persisted' | 'queue';
```

在 `const [dedupeLatest, setDedupeLatest] = useState(false);`（:457）附近追加状态：

```ts
  const [holdingOnly, setHoldingOnly] = useState(false);
  const [rawQueueItems, setRawQueueItems] = useState<DecisionSignalItem[]>([]);
  const [rawQueueLoading, setRawQueueLoading] = useState(true);
  const [rawQueueError, setRawQueueError] = useState<ParsedApiError | null>(null);
  const queueRequestIdRef = useRef(0);

  // 「去重最新」打开时，主列表本身就按 status=active、pageSize=100 查询
  // （loadSignalsForPage 的 effectivePageSize），与待处理区的宽查询参数完全一致。
  // 此时直接复用主列表结果，避免对同一份数据发两次完全相同的请求。
  const queueItems = dedupeLatest ? items : rawQueueItems;
  const queueLoading = dedupeLatest ? loading : rawQueueLoading;
  const queueError = dedupeLatest ? error : rawQueueError;
```

在 `loadSkillOutcomeStats` 定义之后追加加载函数：

```tsx
  // 待处理区要的是全貌，不能受主列表分页影响，因此单独拉最近 QUEUE_PAGE_SIZE 条
  // 启用中的信号在前端分档。dedupeLatest 打开时主列表已按同参拉过一次，直接复用。
  const loadQueue = useCallback(async () => {
    if (dedupeLatest) return;

    const requestId = queueRequestIdRef.current + 1;
    queueRequestIdRef.current = requestId;
    setRawQueueLoading(true);
    try {
      const response = await decisionSignalsApi.list({
        status: 'active',
        page: 1,
        pageSize: QUEUE_PAGE_SIZE,
        ...(holdingOnly ? { holdingOnly: true } : {}),
      });
      if (queueRequestIdRef.current !== requestId) return;
      setRawQueueItems(response.items);
      setRawQueueError(null);
    } catch (err) {
      if (queueRequestIdRef.current !== requestId) return;
      setRawQueueItems([]);
      setRawQueueError(getParsedApiError(err));
    } finally {
      if (queueRequestIdRef.current === requestId) {
        setRawQueueLoading(false);
      }
    }
  }, [holdingOnly, dedupeLatest]);
```

**同一 Step 内继续：把 `holdingOnly` 接进主列表请求。** `holdingOnly` 属于
`DecisionSignalListParams`（`types/decisionSignals.ts:107`），不在 `ListFilters` 上，
因此给 `toListParams`（:198）追加一个**带默认值的第 4 参**，既有调用点不受影响：

```ts
function toListParams(
  filters: ListFilters,
  page: number,
  pageSize: number = PAGE_SIZE,
  holdingOnly: boolean = false,
): DecisionSignalListParams {
  const holding = holdingOnly ? { holdingOnly: true } : {};
  const sourceReportId = parseSourceReportId(filters.sourceReportId);
  if (sourceReportId !== undefined) {
    return {
      sourceReportId,
      sourceType: 'analysis',
      page,
      pageSize,
      ...holding,
    };
  }

  return {
    market: filters.market || undefined,
    stockCode: filters.stockCode.trim() || undefined,
    action: filters.action || undefined,
    marketPhase: filters.marketPhase || undefined,
    sourceType: filters.sourceType || undefined,
    status: filters.status || undefined,
    page,
    pageSize,
    ...holding,
  };
}
```

再把 `loadSignalsForPage`（:557）改为透传该参数，并把 `holdingOnly` 加入依赖
（依赖变化会让既有的挂载 effect 重新拉列表，这正是开关要的效果）：

```tsx
  const loadSignalsForPage = useCallback(async (nextPage: number) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    const effectivePageSize = dedupeLatest ? DEDUPE_PAGE_SIZE : PAGE_SIZE;
    const effectivePage = dedupeLatest ? 1 : nextPage;
    try {
      const response = await decisionSignalsApi.list(
        toListParams(appliedFilters, effectivePage, effectivePageSize, holdingOnly),
      );
      // ...其余函数体保持原样，一行不改
    }
  }, [appliedFilters, dedupeLatest, holdingOnly]);
```

> 说明：这里只给既有请求构造器**追加一个可选参数**、给既有回调**加一个依赖**，
> 未改动筛选、分页、去重、`activeFilterChips`、`requestId` 守卫等任何既有语义。

- [ ] **Step 5: 接上 effect 与刷新按钮**

在既有挂载 effect 区（`useEffect(() => { void loadSignals(); ... }, [loadSignals]);` 之后）追加：

```tsx
  useEffect(() => {
    void loadQueue();
    return () => {
      queueRequestIdRef.current += 1;
    };
  }, [loadQueue]);
```

在页面刷新按钮的 `onClick`（约 :1348）中，`void loadSignals();` 之前追加一行：

```tsx
                void loadQueue();
```

并把该按钮的 `disabled` 条件（约 :1363）改为：

```tsx
              disabled={loading || queueLoading || statsLoading || skillStatsLoading || latestLoading || timelineLoading}
```

> `loadQueue` 内部的 `if (dedupeLatest) return;` 让「去重最新」打开时的这些调用点
> 自动变成空操作（该场景下 `queueLoading` 已由主列表的 `loading` 派生），
> 因此调用点无需再加判断。

在 `handleStatusUpdate` 的成功分支中，`await loadSignalsForPage(page);` 之前追加：

```tsx
      await loadQueue();
```

并在该处 `setSelected` 的 source 分支里，为队列来源补一条与 `persisted` 同款的处理（防止状态更新把来源改写为 `list`）：

```tsx
        if (current.source === 'queue') {
          return { source: 'queue', item: updated };
        }
```

> **有意不做的两件事**（避免越出 spec 范围）：
> 1. 不给 `loadQueue` 添加「刷新后同步/清空队列来源的选中项」逻辑。`loadSignalsForPage`
>    对 `list` 来源会做这件事，但那是既有语义；队列来源的选中项在手动刷新后保持显示，
>    直到用户关闭抽屉或改变状态（改变状态由上面的分支负责同步）。这不是缺陷，
>    只是行为差异，交付说明中会写明。
> 2. 不扩展 `loadSignalsForPage` 里 `current.source !== 'list'` 这个既有守卫去兼容
>    `'queue'`。那会改动既有列表路径的判断；`dedupeLatest` 打开时的上述差异
>    已在 Step 7 的说明中记录。

- [ ] **Step 6: 渲染待处理区**

在 `{!selected && appliedSourceReportId ? ... }` 卡片之前、页面标题行之后，插入首屏队列。同时把 `<p className="text-xs font-semibold uppercase tracking-wide text-muted-text">{t('decisionSignals.sectionAll')}</p>`（:1373）**保留原位**，新块插在它之前：

```tsx
        <Card title={t('decisionSignals.queueTitle')} subtitle={t('decisionSignals.queueDescription')} padding="md">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <Checkbox
              label={t('decisionSignals.holdingOnlyLabel')}
              checked={holdingOnly}
              onChange={(event) => setHoldingOnly(event.target.checked)}
            />
            <button
              type="button"
              className="btn-secondary inline-flex h-9 items-center justify-center gap-2"
              onClick={() => void loadQueue()}
              disabled={queueLoading}
              aria-label={t('decisionSignals.queueRefreshAria')}
            >
              <RefreshCw className={cn('h-4 w-4', queueLoading ? 'animate-spin' : '')} />
              {t('decisionSignals.queueRefresh')}
            </button>
          </div>
          <DecisionSignalActionQueue
            items={queueItems}
            loading={queueLoading}
            error={queueError}
            onRetry={() => void loadQueue()}
            onSelect={(item) => setSelected({ source: 'queue', item })}
            selectedId={selected?.item.id ?? null}
          />
          {queueItems.length >= QUEUE_PAGE_SIZE ? (
            <p className="pt-3 text-xs text-secondary-text">
              {t('decisionSignals.queueTruncatedNote', { fetched: QUEUE_PAGE_SIZE })}
            </p>
          ) : null}
        </Card>
```

补充 import：

1. 把 `Checkbox` 加进页面既有的 `'../components/common'` 花括号块（`:10-20`），保持字母序，
   放在 `Card` 与 `ConfirmDialog` 之间：

```ts
import {
  ApiErrorAlert,
  AppPage,
  Card,
  Checkbox,
  ConfirmDialog,
  Drawer,
  EmptyState,
  InlineAlert,
  Pagination,
  Select,
} from '../components/common';
```

2. 新增一行（放在 `:26` 的 `DecisionSignalProfileCalibration` 导入之后，与该目录其余导入同组）：

```ts
import { DecisionSignalActionQueue } from '../components/decision-signals/DecisionSignalActionQueue';
```

- [ ] **Step 7: 修正既有测试里被新请求打乱的分页计数 helper**

`DecisionSignalsPage.test.tsx:567` 的「refreshes the list, ...」用例用 `stockCode === undefined` 区分主列表调用；待处理区请求同样满足该条件，会让 delta 断言变成 +2。把该 helper 收紧为按 `pageSize` 区分：

```tsx
    const pageListCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.stockCode === undefined && params?.pageSize === 20).length;
```

同时在该用例的刷新断言里补上队列的期望（刷新按钮现在也刷新待处理区）：

```tsx
    expect(vi.mocked(decisionSignalsApi.list).mock.calls.filter(
      ([params]) => params?.pageSize === 100 && params?.stockCode === undefined,
    ).length).toBe(queueCallsBefore + 1);
```

其中 `queueCallsBefore` 与既有 `pageListCallsBefore` 同处定义：

```tsx
    const queueCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 100 && params?.stockCode === undefined).length;
    const queueCallsBefore = queueCalls();
```

> **按 `pageSize` 区分只在 `dedupeLatest` 关闭时成立。** 该开关打开后主列表自身就用
> `DEDUPE_PAGE_SIZE = 100`（`loadSignalsForPage:561`），与待处理区请求同参，
> 单看参数无法区分。这正是 Step 4 让待处理区在该场景下复用主列表结果的原因。
> 因此**任何打开「去重最新」的用例都不得依赖 `pageSize` 去区分两类请求**；
> 若确有需要，改为断言 `list` 的总调用次数。现有 `:1092` 处用
> `pageSize === 20` 过滤的精确计数断言不受影响（待处理区请求被该过滤条件排除）。

- [ ] **Step 8: 运行页面测试**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/DecisionSignalsPage.test.tsx`
Expected: 全绿。若其它用例因新增的 100 条请求失败，逐个按 Step 7 的方式收紧其调用计数断言，**不要改用例语义**。

- [ ] **Step 9: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/pages/DecisionSignalsPage.tsx \
        apps/dsa-web/src/pages/__tests__/DecisionSignalsPage.test.tsx \
        apps/dsa-web/src/i18n/uiText.ts
git commit -m "feat: add action queue and holdings filter to decision signals page"
```

---

### Task 7: 筛选表单折叠 + 统计区下沉与懒加载

**Files:**
- Modify: `apps/dsa-web/src/pages/DecisionSignalsPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/DecisionSignalsPage.test.tsx`
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh + en 追加 3 个 key）

**Interfaces:**
- Consumes: `Collapsible` 受控模式（Task 4）。
- Produces: 页面新增 `statsExpanded: boolean`、`filtersExpanded: boolean`，以及导出的 `hasActiveListFilters(filters: ListFilters): boolean`。

- [ ] **Step 1: 补 i18n 文案**

zh 对象追加：

```ts
  'decisionSignals.filterToggleShow': '高级筛选',
  'decisionSignals.filterToggleHide': '收起筛选',
  'decisionSignals.statsToggleTitle': '统计数据',
```

en 对象追加：

```ts
  'decisionSignals.filterToggleShow': 'Advanced filters',
  'decisionSignals.filterToggleHide': 'Hide filters',
  'decisionSignals.statsToggleTitle': 'Statistics',
```

- [ ] **Step 2: 写失败测试**

在 `DecisionSignalsPage.test.tsx` 追加：

```tsx
  it('collapses the advanced filter form by default and expands it on demand', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    // 条件渲染：收起时整个 Card 不挂载，所以输入框查不到。
    // 默认 status='active' 与 DEFAULT_LIST_FILTERS 相同，不算活跃筛选，不得自动展开。
    expect(screen.queryByLabelText('股票代码')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '高级筛选' }));

    expect(await screen.findByLabelText('股票代码')).toBeInTheDocument();
  });

  it('auto-expands the filter form when the URL carries an active filter', async () => {
    // getInitialFilters 只还原 sourceReportId（:188-196），DEFAULT_LIST_FILTERS.sourceReportId
    // 是 ''，因此 '3001' 构成活跃筛选 → 挂载后应自动展开，无需点「高级筛选」。
    window.history.pushState({}, '', '/decision-signals?sourceReportId=3001');
    renderPage();
    await screen.findByText('贵州茅台');

    expect(await screen.findByLabelText('股票代码')).toBeInTheDocument();
  });

  it('does not request either stats card until the statistics section is expanded', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();
    expect(decisionSignalsApi.getSkillOutcomeStats).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '统计数据' }));

    await waitFor(() => {
      expect(decisionSignalsApi.getOutcomeStats).toHaveBeenCalledTimes(1);
      expect(decisionSignalsApi.getSkillOutcomeStats).toHaveBeenCalledTimes(1);
    });
  });
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/DecisionSignalsPage.test.tsx -t "statistics section"`
Expected: FAIL —— `getOutcomeStats` 仍然在挂载时被调用

- [ ] **Step 4: 实现 `hasActiveListFilters` 并导出**

在 `apps/dsa-web/src/pages/DecisionSignalsPage.tsx` 的 `DEFAULT_LIST_FILTERS` 定义之后追加：

```ts
/**
 * 判定列表筛选表单是否处于「有活跃筛选」状态。
 *
 * 必须与 DEFAULT_LIST_FILTERS 逐字段比较：DEFAULT_LIST_FILTERS.status 本身就是
 * 'active'，按真值判断会导致恒为 true。也不能复用 buildActiveFilterChips —— 它
 * 同样用真值判断，因此默认状态下就恒非空。
 */
export function hasActiveListFilters(filters: ListFilters): boolean {
  return (Object.keys(DEFAULT_LIST_FILTERS) as Array<keyof ListFilters>).some(
    (key) => filters[key] !== DEFAULT_LIST_FILTERS[key],
  );
}
```

- [ ] **Step 5: 新增折叠状态并接入懒加载**

在 Task 6 新增的状态附近追加：

```ts
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [statsExpanded, setStatsExpanded] = useState(false);
```

把两个统计加载 effect 改为受 `statsExpanded` 门控：

```tsx
  useEffect(() => {
    if (!statsExpanded) return undefined;
    void loadOutcomeStats();
    return () => {
      statsRequestIdRef.current += 1;
    };
  }, [loadOutcomeStats, statsExpanded]);

  useEffect(() => {
    if (!statsExpanded) return undefined;
    void loadSkillOutcomeStats();
    return () => {
      skillStatsRequestIdRef.current += 1;
    };
  }, [loadSkillOutcomeStats, statsExpanded]);
```

把这两个 loading 的初值从 `true` 改为 `false`（否则未展开时刷新按钮会被永久禁用）：

```tsx
  const [statsLoading, setStatsLoading] = useState(false);
  const [skillStatsLoading, setSkillStatsLoading] = useState(false);
```

**同一 Step 内继续：给刷新按钮的两个统计调用加门控。** 页面刷新按钮的 `onClick`
（:1346-1349）目前无条件调用两个统计加载函数。若不改，统计区收起时点刷新仍会发出
这两个请求，且 `setStatsLoading(true)` 会经 `disabled` 条件把刷新按钮自己卡住，
与本计划「仅在该区已展开时刷新统计」的交付说明自相矛盾。改为：

```tsx
                void loadSignals();
                // 统计区收起时不请求，避免收起状态下的无谓请求与按钮自锁。
                if (statsExpanded) {
                  void loadOutcomeStats();
                  void loadSkillOutcomeStats();
                }
```

在筛选表单的「自动展开」处加一个 effect（**不要**用派生值，理由见下）：

```tsx
  // spec §4.2：存在活跃筛选时自动展开。用 effect 而不是派生值
  // （如 `filtersExpanded || hasActiveListFilters(...)`），是因为派生值会让
  // Collapsible 的按钮在活跃筛选下永远无法收起——标题写着「收起筛选」却点不动。
  // 用 effect 只在活跃筛选「出现时」自动展开一次，之后用户可自由收起。
  useEffect(() => {
    if (hasActiveListFilters(appliedFilters)) {
      setFiltersExpanded(true);
    }
  }, [appliedFilters]);
```

- [ ] **Step 6: 渲染折叠的筛选表单**

把 `DecisionSignalsPage.tsx:1375-1469` 的筛选 `<Card title={t('decisionSignals.filter')} padding="md">` 整块（`<Card>` 开标签在 :1375，闭合标签在 :1469）原样包进受控 `Collapsible`，展开状态绑定到 `filtersExpanded`：

```tsx
        <Collapsible
          title={t(filtersExpanded ? 'decisionSignals.filterToggleHide' : 'decisionSignals.filterToggleShow')}
          open={filtersExpanded}
          onOpenChange={setFiltersExpanded}
        >
          {filtersExpanded ? (
            <Card title={t('decisionSignals.filter')} padding="md">
              {/* 逐字保留原 Card 内部 :1376-1468 的全部表单 JSX，一行不改 */}
            </Card>
          ) : null}
        </Collapsible>
```

> `{filtersExpanded ? ... : null}` 与统计区同理：`Collapsible` 只用 `max-h-0 opacity-0`
> 隐藏内容，子节点**始终挂载**，不条件渲染的话收起时输入框仍在 DOM 里
> （`getByLabelText` 照样找得到），折叠形同虚设。表单是受控的（`filters` 是页面级
> state），卸载 Card 不会丢失已填内容。

新增 import：把 `Collapsible` 加进 **同一个** `'../components/common'` 花括号块
（Task 6 已把 `Checkbox` 加进去），保持字母序，放在 `Checkbox` 与 `ConfirmDialog` 之间。
**不要再写一行单独的 `import { Collapsible } from '../components/common';`** —— 同一模块
重复导入会被 lint 拦下。

```ts
  Card,
  Checkbox,
  Collapsible,
  ConfirmDialog,
```

- [ ] **Step 7: 把两张统计卡包进受控 Collapsible**

把两张统计卡整体移出「全部信号」区，放到「单股追踪」区之后、外层 `</div>`（:1941）收尾之前，包进：

- 「信号表现统计」卡：`<Card title={t('decisionSignals.statsTitle')}` 开于 :1540，闭合于 :1626。
- 「Skill 表现」卡：`<Card` 开于 :1628，闭合于 :1747。

移动时**整块原样搬运**，不修改卡内任何 JSX；`单股追踪` 的三张卡位于 :1751-1940，保持在它们之前不动。

```tsx
        <Collapsible
          title={t('decisionSignals.statsToggleTitle')}
          open={statsExpanded}
          onOpenChange={setStatsExpanded}
        >
          <div className="space-y-4">
            {statsExpanded ? (
              <>
                {/* 逐字搬运原 :1540-1626 的「信号表现统计」Card 整块 */}
                {/* 逐字搬运原 :1628-1747 的「Skill 表现」Card 整块 */}
              </>
            ) : null}
          </div>
        </Collapsible>
```

`statsExpanded ? ... : null` 是必须的：`Collapsible` 用 `max-h-0 opacity-0` 隐藏子节点，子节点**仍然挂载**，只有条件渲染才能真正做到「未展开不挂载」，从而不触发其中的渲染副作用。

- [ ] **Step 8: 修正既有统计相关测试**

以下 6 个用例原先依赖「挂载即请求统计」，需改为先展开统计区。在 describe 内新增 helper：

```tsx
  const expandStatsSection = async () => {
    fireEvent.click(screen.getByRole('button', { name: '统计数据' }));
    await screen.findByText('信号表现统计');
  };
```

然后逐个用例调整：

1. `:545` `loads active signals by default`：把
   `expect(await screen.findByText('信号表现统计')).toBeInTheDocument();` 之后的三行统计断言（`50%`、全局口径文案、`决策风格历史表现` 标题）与 `expect(getOutcomeStats).toHaveBeenCalledTimes(1)` 移到 `await expandStatsSection();` 之后。
2. `:567` `refreshes the list, both stats cards, ...`：在 `renderPage()` 后、记录 `statsCalls` / `skillStatsCalls` 之前插入 `await expandStatsSection();`。
3. `:616` `keeps the existing stats card usable when the backend omits profile calibration`：在 `renderPage()` 后插入 `await expandStatsSection();`。
4. `:629` `shows a zero-sample outcome stats state ...`：同上。
5. `:652` `shows a skill-specific empty state ...`：同上。
6. `:666` `replaces the skill performance table with a progress summary ...`：同上。
7. `:685` `renders the skill performance table with localized sample sufficiency labels`：同样在 `renderPage()` 后加 `await expandStatsSection();`。

> **关于 skill 名的断言：现有测试里没有任何一条断言 skill 名文本**，所以 Task 1 的接线
> 不会弄坏任何既有用例，**不需要修改既有断言**。夹具里的 skillId 分布是：
> `technical-trend`（不在后端映射中 → 回退显示原始 id）、`box_oscillation` 与
> `shrink_pullback`（都在映射中 → 现在显示「箱体震荡」「缩量回踩」）。
>
> 但「接线是否正确」目前**没有测试覆盖**——Task 1 的单测只覆盖了纯函数。请在
> `:685` 这个用例里补一条断言，把映射后的名字与回退行为都钉住：

```tsx
    // 命中映射的 skill 显示中文名；不在映射中的 id 回退显示原始 id（不得变成 '-'）。
    expect(screen.getByText('箱体震荡')).toBeInTheDocument();
    expect(screen.getByText('缩量回踩')).toBeInTheDocument();
    expect(screen.getByText('technical-trend')).toBeInTheDocument();
```

> 注意 `box_oscillation` / `shrink_pullback` 在夹具中各出现多次（:218/:244 与 :262），
> 若同一次渲染里同一名字出现多于一个节点，`getByText` 会抛「found multiple elements」。
> 届时改用 `getAllByText('箱体震荡').length` 的断言形式，**不要**为了迁就测试去改夹具数据。

- [ ] **Step 9: 运行页面测试与全量前端测试**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/DecisionSignalsPage.test.tsx`
Expected: 全绿

Run: `cd apps/dsa-web && npm run lint && npm run build`
Expected: 均无错误

- [ ] **Step 10: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add apps/dsa-web/src/pages/DecisionSignalsPage.tsx \
        apps/dsa-web/src/pages/__tests__/DecisionSignalsPage.test.tsx \
        apps/dsa-web/src/i18n/uiText.ts
git commit -m "refactor: collapse filters and move stats into a lazy section"
```

---

### Task 8: 文档同步与全量验证

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/decision-signals.md`

**Interfaces:**
- Consumes: Task 1-7 的全部行为。
- Produces: 无

- [ ] **Step 1: 更新 `docs/CHANGELOG.md` 的 `[Unreleased]` 段**

**扁平格式，每条独立一行，禁止新增 `### 类目标题`**。追加：

```markdown
- [改进] 「AI 建议」页新增首屏「待处理」区：启用中的信号按「可以动手 / 需要确认 / 观察」三档分组，只有动作明确（buy/add/reduce/sell）且计划完整（plan_quality=complete）的信号才进入「可以动手」，观察档默认收起。该区分档单独拉取最近 100 条启用信号做全貌判断，不受列表分页影响；超过 100 条时用可见文案说明覆盖范围，不静默截断。原有「全部信号」「单股追踪」两块能力一行未改
- [新功能] 「AI 建议」页支持「只看我的持仓」：复用后端既有 holding_only 查询参数（此前 API 客户端已接入但页面从未使用），开关同时作用于待处理区与信号列表
- [改进] 「AI 建议」页 Skill 表现表的中文名列：此前直接渲染 skill_id 原始值（如 box_oscillation），现映射为中文名（如「箱体震荡」）并随语言切换；未知 skill id 回退显示原始 id，不显示为占位横杠，因 skill 是开放集合（AGENT_SKILLS 可配置自定义策略）
- [改进] 「AI 建议」页高级筛选表单默认收起（有活跃筛选时自动展开），「信号表现统计」与「Skill 表现」两张卡移入页面底部默认收起的「统计数据」区，且展开时才发起请求
- [测试] 新增 tests/test_skill_label_parity.py：断言后端 _STRATEGY_SKILL_TRANSLATIONS 与 Web skill 映射集合一致，防止后端新增策略而前端漏补中文名
```

- [ ] **Step 2: 更新 `docs/decision-signals.md` 的「Web 展示」章节**

在该章节末尾追加下列要点（与既有条目同风格，保持中文）：

```markdown
- 「AI 建议」页首屏为「待处理」区：启用中的信号按「可以动手 / 需要确认 / 观察」三档分组。只有 `action ∈ {buy, add, reduce, sell}` 且 `plan_quality = complete` 才进入「可以动手」；同类动作但计划不完整进入「需要确认」；`hold/watch/avoid/alert` 进入「观察」，默认收起。未知 `action` 或 `plan_quality` 一律归入「观察」，不设兜底桶。
- 「待处理」区分档基于单独一次 `status=active`、`page_size=100` 的列表请求，不受主列表分页影响；结果达到 100 条时 Web 显示覆盖范围说明，不静默截断。
- 「只看我的持仓」开关复用列表 API 既有 `holding_only` 参数，同时作用于「待处理」区与主列表；后端未新增参数或接口。
- 主列表筛选表单默认收起；当筛选条件与 `DEFAULT_LIST_FILTERS` 相异时自动展开（默认 `status=active` 不计为活跃筛选）。
- 「信号表现统计」与「Skill 表现」两张卡位于页面底部默认收起的「统计数据」区，**展开时才发起请求**；折叠状态下不挂载卡片内容。
- Skill 表现表的 skill 列显示当前语言的 skill 名称；未知 skill id 回退显示原始 id，不回退为占位符。映射与后端 `_STRATEGY_SKILL_TRANSLATIONS` 的一致性由 `tests/test_skill_label_parity.py` 守护。
```

- [ ] **Step 3: 全量前端验证**

```bash
cd /Users/anwen/Documents/开发/dsa/apps/dsa-web
npm ci
npm run lint
npm run build
npx vitest run
```

Expected: lint 与 build 无错误；vitest 全绿（含 `tests/ui_governance.test.ts` 的原生 `title` 守卫）

- [ ] **Step 4: 全量后端验证**

```bash
cd /Users/anwen/Documents/开发/dsa
uv run python -m pytest tests/test_skill_label_parity.py -v
./scripts/ci_gate.sh
```

Expected: 新增测试 2 passed；`ci_gate.sh` 通过。若出现失败，先用 `git stash` 之外的方式归因（参考 `AGENTS.md` 与仓库既有的干净 worktree 归因做法），确认是否本计划引入。

- [ ] **Step 5: 准备 PR 证据**

按 `AGENTS.md` 要求，Web UI 改动必须在 PR 描述附**改造前后对比截图**。请在本地 `npm run dev` 后截取 `/decision-signals` 页面的改造前 / 改造后两张图，用于 PR 描述。

- [ ] **Step 6: 提交**

```bash
cd /Users/anwen/Documents/开发/dsa
git add docs/CHANGELOG.md docs/decision-signals.md
git commit -m "docs: document action-first decision signals page"
```

---

## 计划完成后的交付

按 `AGENTS.md` §9 输出：改了什么 / 为什么这么改 / 验证情况 / 未验证项 / 风险点 / 回滚方式。

**PR 描述中必须显式声明的三项行为变更**（spec §9 要求，不可省略）：

1. **统计区改为展开时才请求**：原先挂载页面即发出 `getOutcomeStats` 与
   `getSkillOutcomeStats` 两个请求，现改为展开「统计数据」时请求。首屏网络请求数
   因此减少两次。「刷新」按钮语义不变（仅在该区已展开时刷新统计）。
2. **筛选表单默认收起**：有活跃筛选（与 `DEFAULT_LIST_FILTERS` 逐字段相异）时自动
   展开；默认的 `status=active` 不计为活跃筛选。既有 `activeFilterChips` 恒非空的
   行为**未改动**，仍会默认渲染一个「状态」chip。
3. **新增一次 `pageSize=100` 的列表请求**：待处理区为拿到全貌单独请求最近 100 条
   `status=active` 信号。打开「去重最新」时该请求与主列表同参，此时复用主列表结果，
   不重复发出。

**未验证项需明确写出**：本地数据库仅 8 条信号、4 条持仓，因此「待处理」区在真实大样本下的分页边界、「只看我的持仓」的多账户行为均未做真实数据验证，只覆盖了单测与组件测试。

**回滚**：改动集中在 `apps/dsa-web/src/pages/DecisionSignalsPage.tsx`、`components/decision-signals/`、`components/common/Collapsible.tsx`、`utils/decisionSignalLabels.ts`、`utils/decisionSignalQueue.ts`、`i18n/uiText.ts` 与新增测试文件，无后端与数据库变更。回滚即 revert 对应提交；`tests/test_skill_label_parity.py` 必须与前端 skill 映射同进同退。
