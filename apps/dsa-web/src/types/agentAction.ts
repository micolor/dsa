/**
 * 问股助手已经执行完的写操作，以及已提交到后台执行的长任务。
 *
 * 与 `actionProposal.ts` 一样，kind 白名单以运行时数组为单一源，类型从中派生：
 * 真正决定事件是否被接受的是运行时数组，漏一个 kind 意味着对应卡片永远不渲染，
 * 且 `tsc` 不会报错。
 */

export const ACTION_EXECUTED_KINDS = [
  'watchlist_change',
  'alert_rule_toggle',
  'alert_rule_test',
  'fx_refresh',
  'paper_refresh',
  'intel_source_test',
  'llm_channel_test',
] as const;

export type ActionExecutedKind = (typeof ACTION_EXECUTED_KINDS)[number];

export interface ActionExecuted {
  kind: ActionExecutedKind;
  summary: string;
  /** 执行结果，形状由各 kind 决定，保持后端原样 */
  result: Record<string, unknown>;
  /** 撤销提示；后端保证非空 */
  undoHint: string;
}

export const ACTION_TASK_KINDS = [
  'stock_analysis',
  'market_review',
  'backtest_run',
  'screening_run',
  'intelligence_fetch',
  'fund_nav_backfill',
  'fund_nav_run',
  'signal_outcomes_run',
  'paper_backfill',
] as const;

export type ActionTaskKind = (typeof ACTION_TASK_KINDS)[number];

export interface ActionTask {
  kind: ActionTaskKind;
  summary: string;
  taskId: string;
  statusEndpoint: string;
  pollIntervalMs: number;
}

function isKind<T extends readonly string[]>(values: T, value: unknown): value is T[number] {
  return typeof value === 'string' && (values as readonly string[]).includes(value);
}

/** 从 SSE 事件里解析已执行动作；形状不合规一律返回 undefined（不抛错、不部分接受）。 */
export function parseActionExecutedEvent(event: unknown): ActionExecuted | undefined {
  const payload = (event ?? {}) as Record<string, unknown>;
  if (!isKind(ACTION_EXECUTED_KINDS, payload.kind)) return undefined;
  if (typeof payload.summary !== 'string' || !payload.summary.trim()) return undefined;
  if (!payload.result || typeof payload.result !== 'object' || Array.isArray(payload.result)) return undefined;
  if (typeof payload.undo_hint !== 'string' || !payload.undo_hint.trim()) return undefined;
  return {
    kind: payload.kind,
    summary: payload.summary,
    result: payload.result as Record<string, unknown>,
    undoHint: payload.undo_hint,
  };
}

/**
 * 从 SSE 事件里解析后台任务；形状不合规一律返回 undefined。
 *
 * 轮询间隔限制在 1s–10s：后端值缺失时用 2s，越界值夹到边界，避免前端高频打爆状态接口
 * 或因间隔过大长时间看不到完成。
 */
export function parseActionTaskEvent(event: unknown): ActionTask | undefined {
  const payload = (event ?? {}) as Record<string, unknown>;
  if (!isKind(ACTION_TASK_KINDS, payload.kind)) return undefined;
  if (typeof payload.summary !== 'string' || !payload.summary.trim()) return undefined;
  if (typeof payload.task_id !== 'string' || !payload.task_id) return undefined;
  if (typeof payload.status_endpoint !== 'string' || !payload.status_endpoint.startsWith('/')) return undefined;
  const pollIntervalMs = typeof payload.poll_interval_ms === 'number'
    ? Math.max(1000, Math.min(10_000, payload.poll_interval_ms))
    : 2000;
  return {
    kind: payload.kind,
    summary: payload.summary,
    taskId: payload.task_id,
    statusEndpoint: payload.status_endpoint,
    pollIntervalMs,
  };
}
