import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiGetMock = vi.hoisted(() => vi.fn());
vi.mock('../src/api/index.ts', () => ({
  default: { get: apiGetMock },
}));

import { stocksApi } from '../src/api/stocks';

function makeResponse(data: unknown) {
  return { data };
}

beforeEach(() => {
  apiGetMock.mockReset();
});

describe('stocksApi.getStockHistory cache', () => {
  it('uses one network call for repeated same code+days within TTL', async () => {
    const payload = {
      stock_code: '600519',
      stock_name: '贵州茅台',
      period: 'daily',
      data: [{ date: '2026-09-01', open: 1, high: 2, low: 0.5, close: 1.5 }],
    };
    apiGetMock.mockResolvedValue(makeResponse(payload));

    const first = await stocksApi.getStockHistory('600519', 60);
    const second = await stocksApi.getStockHistory('600519', 60);

    expect(apiGetMock).toHaveBeenCalledTimes(1);
    expect(first.stockCode).toBe('600519');
    expect(first.data[0].close).toBe(1.5);
    expect(second).toBe(first); // 同一 promise 被复用
  });

  it('uses separate cache keys for different days', async () => {
    apiGetMock.mockResolvedValue(makeResponse({ stock_code: 'A1', period: 'daily', data: [] }));

    await stocksApi.getStockHistory('A1', 30);
    await stocksApi.getStockHistory('A1', 60);

    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });

  it('evicts failed entries so a later call can retry', async () => {
    apiGetMock
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(makeResponse({ stock_code: 'B1', period: 'daily', data: [] }));

    await expect(stocksApi.getStockHistory('B1', 60)).rejects.toThrow('boom');
    await expect(stocksApi.getStockHistory('B1', 60)).resolves.toBeDefined();

    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });
});
