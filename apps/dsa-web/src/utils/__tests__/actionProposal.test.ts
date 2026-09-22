import { beforeEach, describe, expect, it, vi } from 'vitest';
import { applyActionProposal } from '../actionProposal';
import type { ActionProposal } from '../../types/actionProposal';

vi.mock('../../api/alerts', () => ({ alertsApi: { createRule: vi.fn(async () => ({})) } }));
vi.mock('../../api/portfolio', () => ({ portfolioApi: { createTrade: vi.fn(async () => ({})) } }));
vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    addToWatchlist: vi.fn(async () => []),
    removeFromWatchlist: vi.fn(async () => []),
  },
}));

const { alertsApi } = await import('../../api/alerts');
const { portfolioApi } = await import('../../api/portfolio');
const { systemConfigApi } = await import('../../api/systemConfig');

beforeEach(() => {
  vi.clearAllMocks();
});

describe('applyActionProposal', () => {
  it('creates an alert rule from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'alert',
      summary: '「600519」价格上穿 ¥1800',
      proposal: {
        name: '600519 price above 1800',
        target_scope: 'single_symbol',
        target: '600519',
        alert_type: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'info',
      },
    };

    await applyActionProposal(proposal);

    expect(alertsApi.createRule).toHaveBeenCalledWith({
      name: '600519 price above 1800',
      targetScope: 'single_symbol',
      target: '600519',
      alertType: 'price_cross',
      parameters: { direction: 'above', price: 1800 },
      severity: 'info',
    });
  });

  it('records a portfolio trade from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'portfolio_trade',
      summary: '在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706（2026-09-22，约支出 CNY 43268.54）',
      proposal: {
        account_id: 1,
        symbol: '005827',
        trade_date: '2026-09-22',
        side: 'buy',
        quantity: 25900,
        price: 1.6706,
        fee: 0,
        tax: 0,
      },
    };

    await applyActionProposal(proposal);

    expect(portfolioApi.createTrade).toHaveBeenCalledWith({
      accountId: 1,
      symbol: '005827',
      tradeDate: '2026-09-22',
      side: 'buy',
      quantity: 25900,
      price: 1.6706,
      fee: 0,
      tax: 0,
    });
  });

  it('adds a symbol to the default watchlist with positional args', async () => {
    await applyActionProposal({
      kind: 'watchlist_add',
      summary: '把「600519」加入自选',
      proposal: { stock_code: '600519', list_name: null },
    });

    expect(systemConfigApi.addToWatchlist).toHaveBeenCalledWith('600519', undefined);
    expect(systemConfigApi.removeFromWatchlist).not.toHaveBeenCalled();
  });

  it('removes a symbol from a named watchlist', async () => {
    await applyActionProposal({
      kind: 'watchlist_remove',
      summary: '把「600519」移出自选列表「短线池」',
      proposal: { stock_code: '600519', list_name: '短线池' },
    });

    expect(systemConfigApi.removeFromWatchlist).toHaveBeenCalledWith('600519', '短线池');
    expect(systemConfigApi.addToWatchlist).not.toHaveBeenCalled();
  });

  it('rejects an unknown kind instead of silently doing nothing', async () => {
    await expect(
      applyActionProposal({
        kind: 'drop_table' as never,
        summary: 'x',
        proposal: {},
      }),
    ).rejects.toThrow(/drop_table/);
  });
});
