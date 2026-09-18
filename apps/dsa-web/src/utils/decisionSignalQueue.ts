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
 * 判定只看两个字段：action 是否属于会改仓位的集合，以及 plan_quality 是否恰好
 * 等于 complete —— 两者是「与」的关系。本函数不读取 stop_loss，也不读取任何价格
 * 计划字段，所以 actionable 只表示「动作明确且计划质量标记为完整」，并不保证
 * 信号带有止损位。未知 action 一律归入 watch；改仓位的动作即使 plan_quality
 * 未知或不等于 complete，也归入 needs_review。分档的方向是「告诉用户该动手」，
 * 宁可不提示也不能误报。
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
