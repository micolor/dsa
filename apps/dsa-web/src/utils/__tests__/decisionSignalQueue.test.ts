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
    // plan_quality 不是 complete 的改仓位动作不该被推成「可以动手」——分档只看 action 与 plan_quality。
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

  it('keeps a position-changing action with a missing or non-string plan quality out of actionable', () => {
    // 计划质量缺失不等同于「计划完整」：plan_quality 缺失或非字符串的改仓位动作不进 actionable。
    expect(classifyDecisionSignalQueue(signal('buy', null as never))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('buy', undefined as never))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('sell', null as never))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('sell', '' as never))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('add', 1 as never))).toBe('needs_review');
    expect(classifyDecisionSignalQueue(signal('reduce', {} as never))).toBe('needs_review');
    // 完全不携带 planQuality 字段的脏数据也走同一条路径。
    expect(classifyDecisionSignalQueue({ action: 'buy' } as DecisionSignalItem)).toBe('needs_review');
    expect(classifyDecisionSignalQueue({ action: 'reduce' } as DecisionSignalItem)).toBe('needs_review');
  });

  it('routes an unknown action to watch, but an unknown plan quality on a position-changing action to needs_review', () => {
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
