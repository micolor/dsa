/**
 * 问股助手提出、等待用户确认的写操作提案。
 *
 * 这是跨域类型（告警 / 持仓 / 信号 / 历史 / 情报 / 系统配置），因此不放进任何一个域文件。
 * `proposal` 保持后端原样的 snake_case，且刻意标为 `unknown`：各 kind 的请求体形状
 * 不同，硬塞泛型只会堆断言，转换与断言统一放在 `utils/actionProposal.ts` 的 apply 分支里。
 * 本数组与后端 `src/agent/action_contract.py::ACTION_PROPOSAL_KINDS` 严格相等，
 * 由 `tests/test_action_proposal_kinds_parity.py` 钉住。
 *
 * `ACTION_PROPOSAL_KINDS` 是 kind 的**单一源**（运行时数组在前，类型从中派生）。刻意不写成
 * 「联合类型 + 另在 store 里放一份运行时数组」：那样两份可以漂移，而真正决定事件是否被接受
 * 的是运行时数组（union 只在编译期存在）。漏一个 kind 意味着该类提案的卡片永远不渲染，且
 * `tsc` 不会报错——`readonly ActionProposalKind[]` 只约束成员合法，不要求覆盖全部成员。
 */
export const ACTION_PROPOSAL_KINDS = [
  'alert_create',
  'alert_update',
  'alert_delete',
  'portfolio_trade',
  'portfolio_cash_create',
  'portfolio_cash_delete',
  'portfolio_corporate_create',
  'portfolio_corporate_delete',
  'portfolio_account_create',
  'portfolio_account_update',
  'portfolio_account_deactivate',
  'signal_create',
  'signal_status',
  'signal_feedback',
  'signal_reassess',
  'paper_reset',
  'history_delete_records',
  'history_delete_by_code',
  'intelligence_source_create',
  'intelligence_defaults_create',
  'config_update',
  'scheduler_run',
  'notification_test',
] as const;

/** kind 类型从运行时数组派生：单一源，二者不可能不一致（详见上方注释）。 */
export type ActionProposalKind = (typeof ACTION_PROPOSAL_KINDS)[number];

/** 未知 kind 一律丢弃，避免把前端无法分发的卡片挂到消息上。 */
export function isActionProposalKind(value: unknown): value is ActionProposalKind {
  return typeof value === 'string' && (ACTION_PROPOSAL_KINDS as readonly string[]).includes(value);
}

export interface ActionProposal {
  kind: ActionProposalKind;
  /** 卡片展示的中文摘要，由后端工具处理器生成，不是模型自由生成 */
  summary: string;
  /** 对应写接口的请求体（snake_case） */
  proposal: unknown;
}

/**
 * 从 SSE 事件里解析提案；形状不合规一律返回 undefined（不抛错、不部分接受）。
 *
 * 放在这里而不是 `utils/actionProposal.ts`：那个模块 import 三个 API 客户端，是 apply 边界；
 * 这里只是 ingest 边界，纯解析、无副作用。
 */
export function parseActionProposalEvent(event: unknown): ActionProposal | undefined {
  const { kind, summary, proposal } = (event ?? {}) as {
    kind?: unknown;
    summary?: unknown;
    proposal?: unknown;
  };
  if (!isActionProposalKind(kind)) return undefined;
  // 数组也是 object，但不是合法的请求体（FastAPI 422 的 detail 就是数组，见 api/error.ts 的 isRecord）。
  if (!proposal || typeof proposal !== 'object' || Array.isArray(proposal)) return undefined;
  // 空摘要会渲染出一张「用户不知道自己在确认什么」的卡片，宁可不出卡片。
  if (typeof summary !== 'string' || summary.trim() === '') return undefined;
  return { kind, summary, proposal };
}
