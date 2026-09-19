import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../history';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

describe('historyApi', () => {
  beforeEach(() => {
    get.mockReset();
  });

  // 载荷缺 items（或 items: null）时不应在 API 层抛 TypeError：
  // 那会把「后端没给列表」变成「页面解析失败」，用户看到的是失败横幅而不是空列表。
  it('treats a list response without items as an empty list', async () => {
    get.mockResolvedValueOnce({ data: { total: 0, page: 1, limit: 20 } });

    await expect(historyApi.getList()).resolves.toEqual({
      total: 0,
      page: 1,
      limit: 20,
      items: [],
    });
  });

  it('treats a null items field as an empty list', async () => {
    get.mockResolvedValueOnce({ data: { total: 0, page: 1, limit: 20, items: null } });

    const result = await historyApi.getList();

    expect(result.items).toEqual([]);
  });

  it('treats a stock-bar response without items as an empty list', async () => {
    get.mockResolvedValueOnce({ data: { total: 0 } });

    const result = await historyApi.getStockBarList();

    expect(result.items).toEqual([]);
  });
});
