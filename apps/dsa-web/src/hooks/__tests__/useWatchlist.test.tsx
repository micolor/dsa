import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useWatchlist } from '../useWatchlist';

const {
  mockGetWatchlist,
  mockAddToWatchlist,
  mockRemoveFromWatchlist,
  mockGetWatchlistLists,
} = vi.hoisted(() => ({
  mockGetWatchlist: vi.fn(),
  mockAddToWatchlist: vi.fn(),
  mockRemoveFromWatchlist: vi.fn(),
  mockGetWatchlistLists: vi.fn(),
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAddToWatchlist,
    removeFromWatchlist: mockRemoveFromWatchlist,
    getWatchlistLists: mockGetWatchlistLists,
  },
}));

describe('useWatchlist', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWatchlist.mockResolvedValue([]);
    mockAddToWatchlist.mockResolvedValue([]);
    mockRemoveFromWatchlist.mockResolvedValue([]);
    mockGetWatchlistLists.mockResolvedValue([]);
  });

  it('matches raw HK watchlist entries against prefixed and suffixed variants', async () => {
    mockGetWatchlist.mockResolvedValue(['00700']);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isInWatchlist('00700')).toBe(true);
    expect(result.current.isInWatchlist('HK00700')).toBe(true);
    expect(result.current.isInWatchlist('00700.HK')).toBe(true);
    expect(result.current.isInWatchlist('HK01810')).toBe(false);
  });

  it('removes the matched raw watchlist entry instead of adding a duplicate variant', async () => {
    mockGetWatchlist.mockResolvedValue(['00700']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.toggleWatchlist('HK00700');
    });

    expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('00700', undefined);
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(result.current.watchlistCodes).toEqual([]);
    });
  });

  it('compares submitted and stored US tickers case-insensitively', async () => {
    mockGetWatchlist.mockResolvedValue(['aapl']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isInWatchlist('AAPL')).toBe(true);

    await act(async () => {
      await result.current.toggleWatchlist('AAPL');
    });

    expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('aapl', undefined);
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
  });

  it('default list reads via legacy API with no list name', async () => {
    mockGetWatchlist.mockResolvedValue(['600519']);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.activeListId).toBe('__default__');
    expect(mockGetWatchlist).toHaveBeenCalledWith(undefined);
    expect(result.current.watchlistOptions[0]).toMatchObject({ name: '默认自选', isDefault: true });
  });

  it('switching to a named list refetches its codes with the list name', async () => {
    mockGetWatchlist.mockResolvedValueOnce(['600519']).mockResolvedValueOnce(['300750']);
    mockGetWatchlistLists.mockResolvedValue([{ key: 'WATCHLIST_SHORT', name: 'short', count: 1 }]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.onSwitchList('short');
    });

    expect(result.current.activeListId).toBe('short');
    expect(mockGetWatchlist).toHaveBeenLastCalledWith('short');
    expect(result.current.watchlistCodes).toEqual(['300750']);
  });

  it('add/remove scopes the operation to the active list name', async () => {
    mockGetWatchlist.mockResolvedValueOnce([]).mockResolvedValueOnce([]);
    mockGetWatchlistLists.mockResolvedValue([{ key: 'WATCHLIST_LONG', name: 'long', count: 1 }]);
    mockAddToWatchlist.mockResolvedValue(['600519']);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.onSwitchList('long');
    });

    await act(async () => {
      await result.current.addToWatchlist('600519');
    });

    expect(mockAddToWatchlist).toHaveBeenCalledWith('600519', 'long');
  });

  // 切换列表时若旧列表的响应更慢，它会在 activeListId 已指向新列表之后落地，
  // 于是「表头是新列表、行内容是旧列表」——而增删是按 activeListName 写入的，
  // 显示与写入目标静默分叉。
  it('ignores a slower list switch that lands after a newer one', async () => {
    let resolveSlowList: ((codes: string[]) => void) | undefined;
    mockGetWatchlist.mockImplementation((listName?: string) => {
      if (listName === 'slow') {
        return new Promise<string[]>((resolve) => {
          resolveSlowList = resolve;
        });
      }
      if (listName === 'fast') {
        return Promise.resolve(['600519']);
      }
      return Promise.resolve([]);
    });
    mockGetWatchlistLists.mockResolvedValue([
      { key: 'WATCHLIST_SLOW', name: 'slow', count: 1 },
      { key: 'WATCHLIST_FAST', name: 'fast', count: 1 },
    ]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    let slowSwitch: Promise<void> | undefined;
    act(() => {
      slowSwitch = result.current.onSwitchList('slow');
    });
    await act(async () => {
      await result.current.onSwitchList('fast');
    });

    expect(result.current.activeListId).toBe('fast');
    expect(result.current.watchlistCodes).toEqual(['600519']);

    // slow 的响应迟到：不得覆盖 fast 的内容，也不得把激活列表回滚成 slow，
    // 更不得把新列表的加载态提前结束。
    await act(async () => {
      resolveSlowList?.(['000001']);
      await slowSwitch;
    });

    expect(result.current.activeListId).toBe('fast');
    expect(result.current.watchlistCodes).toEqual(['600519']);
    expect(result.current.isLoading).toBe(false);
  });

  it('creating a list optimistically inserts it and switches to it', async () => {
    mockGetWatchlist.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.onCreateList('短线池');
    });

    expect(result.current.activeListId).toBe('短线池');
    expect(mockGetWatchlist).toHaveBeenLastCalledWith('短线池');
    const named = result.current.watchlistOptions.find((opt) => !opt.isDefault);
    expect(named).toMatchObject({ name: '短线池' });
  });
});
