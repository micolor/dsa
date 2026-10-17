import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { FundNavReviewPanel } from '../FundNavReviewPanel';

const { list, getStats, run } = vi.hoisted(() => ({
  list: vi.fn(),
  getStats: vi.fn(),
  run: vi.fn(),
}));

vi.mock('../../../api/fundNavReviews', () => ({
  fundNavReviewsApi: {
    list,
    getStats,
    run,
  },
}));

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    fundCode: '003095',
    fundName: '中欧医疗健康混合A',
    horizon: '1m',
    engineVersion: 'fund-nav-v1',
    anchorDate: '2024-01-02',
    anchorNav: 1.2345,
    dueDate: '2024-02-06',
    evalStatus: 'evaluated',
    unableReason: null,
    evalDate: '2024-02-01',
    evalNav: 1.2654,
    navReturnPct: 2.5,
    maxDrawdownPct: -1.2,
    evalWindowRecords: 22,
    anchorSource: 'pipeline',
    attempts: 1,
    ...overrides,
  };
}

function makeStats(overrides: Record<string, unknown> = {}) {
  return {
    engineVersion: 'fund-nav-v1',
    horizons: ['1m'],
    total: 3,
    anchorCount: 3,
    pending: 1,
    evaluated: 2,
    unable: 0,
    positiveRatePct: 50,
    avgNavReturnPct: 1.25,
    avgMaxDrawdownPct: -2.5,
    unableReasons: {},
    breakdowns: {
      horizon: [],
      month: [
        {
          dimension: 'month',
          value: '2024-01',
          total: 3,
          anchorCount: 3,
          pending: 1,
          evaluated: 2,
          unable: 0,
          positiveRatePct: 50,
          avgNavReturnPct: 1.25,
          avgMaxDrawdownPct: -2.5,
          unableReasons: {},
        },
      ],
    },
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function expand() {
  fireEvent.click(screen.getByRole('button', { name: /基金净值复盘/ }));
}

describe('FundNavReviewPanel', () => {
  beforeEach(() => {
    list.mockReset();
    getStats.mockReset();
    run.mockReset();
    list.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 10 });
    getStats.mockResolvedValue(makeStats());
  });

  it('does not request anything until expanded', () => {
    render(<FundNavReviewPanel />);

    expect(list).not.toHaveBeenCalled();
    expect(getStats).not.toHaveBeenCalled();
  });

  it('loads the ledger, stats and monthly table after expanding', async () => {
    list.mockResolvedValue({
      items: [makeItem()],
      total: 1,
      page: 1,
      pageSize: 10,
    });

    render(<FundNavReviewPanel />);
    expand();

    await waitFor(() => expect(list).toHaveBeenCalledWith({
      horizon: undefined,
      evalStatus: undefined,
      page: 1,
      pageSize: 10,
    }));
    expect(getStats).toHaveBeenCalledWith(undefined);

    const ledger = screen.getByRole('table', { name: '复盘台账' });
    expect(within(ledger).getByText('003095')).toBeInTheDocument();
    expect(within(ledger).getByText('+2.50%')).toBeInTheDocument();
    expect(within(ledger).getByText('-1.20%')).toBeInTheDocument();

    const monthly = screen.getByRole('table', { name: '月度复盘' });
    expect(within(monthly).getByText('2024-01')).toBeInTheDocument();
    expect(within(monthly).getByText('50.00%')).toBeInTheDocument();
  });

  it('shows the empty state when no anchors exist yet', async () => {
    render(<FundNavReviewPanel />);
    expand();

    expect(await screen.findByText('暂无基金复盘记录')).toBeInTheDocument();
    expect(screen.getByText(/fund:<6位代码>/)).toBeInTheDocument();
  });

  it('renders an error with retry when the ledger request fails', async () => {
    list.mockRejectedValueOnce(new Error('network down'));

    render(<FundNavReviewPanel />);
    expand();

    expect(await screen.findByText('network down')).toBeInTheDocument();
    list.mockResolvedValueOnce({ items: [makeItem()], total: 1, page: 1, pageSize: 10 });
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('003095')).toBeInTheDocument();
  });

  it('runs the evaluation and reloads the ledger afterwards', async () => {
    run.mockResolvedValue({
      items: [],
      evaluated: 2,
      pending: 1,
      unable: 0,
      skipped: 0,
      engineVersion: 'fund-nav-v1',
    });

    render(<FundNavReviewPanel />);
    expand();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /运行评估/ }));

    await waitFor(() => expect(run).toHaveBeenCalledWith({ horizons: undefined }));
    expect(await screen.findByText(/已评估 2 条/)).toBeInTheDocument();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(getStats).toHaveBeenCalledTimes(2);
  });

  it('surfaces a run failure without losing the ledger', async () => {
    run.mockRejectedValue(new Error('fetch failed'));

    render(<FundNavReviewPanel />);
    expand();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /运行评估/ }));

    expect(await screen.findByText('fetch failed')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('pages through the ledger', async () => {
    list.mockResolvedValue({
      items: [makeItem()],
      total: 25,
      page: 1,
      pageSize: 10,
    });

    render(<FundNavReviewPanel />);
    expand();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: '2' }));

    await waitFor(() => expect(list).toHaveBeenLastCalledWith({
      horizon: undefined,
      evalStatus: undefined,
      page: 2,
      pageSize: 10,
    }));
  });

  it('drops a stale response that resolves after a newer request', async () => {
    const first = deferred<{ items: ReturnType<typeof makeItem>[]; total: number; page: number; pageSize: number }>();
    list.mockReturnValueOnce(first.promise);
    list.mockResolvedValueOnce({
      items: [makeItem({ id: 2, fundCode: '000001', fundName: '华夏成长' })],
      total: 1,
      page: 1,
      pageSize: 10,
    });

    render(<FundNavReviewPanel />);
    expand();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    // 切换周期会让第一次请求尚未返回时发起第二次请求。
    fireEvent.click(screen.getByRole('button', { name: '周期' }));
    fireEvent.click(await screen.findByRole('option', { name: '3m' }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(list).toHaveBeenLastCalledWith({
      horizon: '3m',
      evalStatus: undefined,
      page: 1,
      pageSize: 10,
    });
    expect(await screen.findByText('000001')).toBeInTheDocument();

    first.resolve({ items: [makeItem({ id: 3, fundCode: '999999' })], total: 1, page: 1, pageSize: 10 });
    await waitFor(() => expect(screen.queryByText('999999')).not.toBeInTheDocument());
    expect(screen.getByText('000001')).toBeInTheDocument();
  });
});
