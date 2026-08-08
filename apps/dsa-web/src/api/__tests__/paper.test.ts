import { beforeEach, describe, expect, it, vi } from 'vitest';
import { paperApi } from '../paper';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get,
    post,
  },
}));

describe('paperApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('getAccount returns camelCased account payload', async () => {
    get.mockResolvedValue({
      data: {
        account_id: 1,
        name: '模拟盘',
        initial_capital: 1000000,
        cash: 1000000,
        status: 'active',
        snapshot: {
          account_id: 1,
          cash: 800000,
          market_value: 200000,
          net_value: 1000000,
          return_pct: 0,
          initial_capital: 1000000,
          open_position_count: 1,
        },
      },
    });

    const result = await paperApi.getAccount();
    expect(get).toHaveBeenCalledWith('/api/v1/paper/account');
    expect(result.accountId).toBe(1);
    expect(result.snapshot.openPositionCount).toBe(1);
  });

  it('getEquityCurve passes optional params and camelCases points', async () => {
    get.mockResolvedValue({
      data: [{ trade_date: '2026-01-05', net_value: 1000000, return_pct: 0 }],
    });

    const result = await paperApi.getEquityCurve('2026-01-01', '2026-01-31');
    expect(get).toHaveBeenCalledWith('/api/v1/paper/equity-curve', {
      params: { start: '2026-01-01', end: '2026-01-31' },
    });
    expect(result[0].tradeDate).toBe('2026-01-05');
  });

  it('backfill posts snake_case body and camelCases result', async () => {
    post.mockResolvedValue({
      data: {
        account_id: 1,
        from_date: '2026-01-01',
        to_date: '2026-01-31',
        signals_replayed: 2,
        snapshot: { account_id: 1, net_value: 1000000, return_pct: 0 },
      },
    });

    const result = await paperApi.backfill('2026-01-01', '2026-01-31');
    expect(post).toHaveBeenCalledWith('/api/v1/paper/backfill', {
      from_date: '2026-01-01',
      to_date: '2026-01-31',
    });
    expect(result.signalsReplayed).toBe(2);
  });

  it('getTrades returns camelCased list', async () => {
    get.mockResolvedValue({
      data: {
        total: 1,
        items: [{ stock_code: '600519', side: 'buy', trade_date: '2026-01-05' }],
      },
    });

    const result = await paperApi.getTrades(1, 50);
    expect(get).toHaveBeenCalledWith('/api/v1/paper/trades', { params: { page: 1, limit: 50 } });
    expect(result.items[0].stockCode).toBe('600519');
    expect(result.total).toBe(1);
  });
});
