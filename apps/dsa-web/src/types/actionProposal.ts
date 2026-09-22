/**
 * 问股助手提出、等待用户确认的写操作提案。
 *
 * 这是跨域类型（告警 / 持仓 / 自选），因此不放进任何一个域文件。
 * `proposal` 保持后端原样的 snake_case，且刻意标为 `unknown`：四个 kind 的请求体形状
 * 不同，硬塞泛型只会堆断言，转换与断言统一放在 `utils/actionProposal.ts` 的 apply 分支里。
 *
 * `ACTION_PROPOSAL_KINDS` 是 kind 的**单一源**（运行时数组在前，类型从中派生）。刻意不写成
 * 「联合类型 + 另在 store 里放一份运行时数组」：那样两份可以漂移，而真正决定事件是否被接受
 * 的是运行时数组（union 只在编译期存在）。漏一个 kind 意味着该类提案的卡片永远不渲染，且
 * `tsc` 不会报错——`readonly ActionProposalKind[]` 只约束成员合法，不要求覆盖全部成员。
 */
export const ACTION_PROPOSAL_KINDS = [
  'alert',
  'portfolio_trade',
  'watchlist_add',
  'watchlist_remove',
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
