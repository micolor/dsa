import { alertsApi } from '../api/alerts';
import { portfolioApi } from '../api/portfolio';
import { systemConfigApi } from '../api/systemConfig';
import { toCamelCase } from '../api/utils';
import type { AlertRuleCreateRequest } from '../types/alerts';
import type { PortfolioTradeCreateRequest } from '../types/portfolio';
import type { ActionProposal } from '../types/actionProposal';

/** watchlist 接口的请求体形状（对齐 WatchlistRequest，`api/v1/schemas/history.py:389`）。 */
interface WatchlistProposalPayload {
  stockCode: string;
  /** 后端可能给 null（"默认列表"由客户端用 undefined 表达）。 */
  listName?: string | null;
}

/**
 * 执行一条经用户确认的提案。
 *
 * 写入发生在浏览器会话下、走既有 REST 接口，AI 从不持有写权限；这里只负责把后端
 * 原样的 snake_case 请求体转成各客户端的入参形状并调用。
 *
 * kind 不在白名单时抛错（不会静默什么都不做），调用方可据此包 try/catch 提示用户。
 */
export async function applyActionProposal(proposal: ActionProposal): Promise<void> {
  switch (proposal.kind) {
    case 'alert':
      await alertsApi.createRule(toCamelCase<AlertRuleCreateRequest>(proposal.proposal));
      return;
    case 'portfolio_trade':
      await portfolioApi.createTrade(
        toCamelCase<PortfolioTradeCreateRequest>(proposal.proposal),
      );
      return;
    case 'watchlist_add': {
      const payload = toCamelCase<WatchlistProposalPayload>(proposal.proposal);
      // list_name 后端可能给 null；客户端的可选第二参数用 undefined 表达"默认列表"。
      await systemConfigApi.addToWatchlist(payload.stockCode, payload.listName ?? undefined);
      return;
    }
    case 'watchlist_remove': {
      const payload = toCamelCase<WatchlistProposalPayload>(proposal.proposal);
      await systemConfigApi.removeFromWatchlist(payload.stockCode, payload.listName ?? undefined);
      return;
    }
    default: {
      // 穷尽性检查：新增 kind 而忘了在这里处理时，`tsc -b` 会失败，而不是让用户看到「提交失败」。
      const exhaustive: never = proposal.kind;
      throw new Error(`unsupported action proposal kind: ${String(exhaustive)}`);
    }
  }
}
