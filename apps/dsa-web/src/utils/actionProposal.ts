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
  listName?: string;
}

/**
 * 执行一条经用户确认的提案。
 *
 * 写入发生在浏览器会话下、走既有 REST 接口，AI 从不持有写权限；这里只负责把后端
 * 原样的 snake_case 请求体转成各客户端的入参形状并调用。
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
    default:
      // kind 已在服务端白名单校验过，这里只是纵深防御：宁可直接报错，也不静默什么都不做。
      throw new Error(`unsupported action proposal kind: ${String((proposal as { kind?: unknown }).kind)}`);
  }
}
