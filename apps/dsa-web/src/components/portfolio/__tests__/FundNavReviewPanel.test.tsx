import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { FundNavReviewPanel } from '../FundNavReviewPanel';

const { list, getStats, run, getNavRange } = vi.hoisted(() => ({
  list: vi.fn(),
  getStats: vi.fn(),
  run: vi.fn(),
  getNavRange: vi.fn(),
}));

vi.mock('../../../api/fundNavReviews', () => ({
  fundNavReviewsApi: {
    list,
    getStats,
    run,
    getNavRange,
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
    restoredReturnPct: 2.5,
    restoredMaxDrawdownPct: -1.2,
    restoredBasis: 'dividend_reinvested',
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
    restoredEvaluated: 2,
    restoredPositiveRatePct: 50,
    avgRestoredReturnPct: 1.25,
    avgRestoredMaxDrawdownPct: -2.5,
    restoredBases: { dividend_reinvested: 2 },
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
          restoredEvaluated: 2,
          restoredPositiveRatePct: 50,
          avgRestoredReturnPct: 1.25,
          avgRestoredMaxDrawdownPct: -2.5,
          restoredBases: { dividend_reinvested: 2 },
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
    getNavRange.mockReset();
    getNavRange.mockResolvedValue({
      fundCode: '003095',
      startDate: '2025-01-01',
      endDate: '2025-12-31',
      adjustmentStatus: 'available',
      truncated: false,
      events: { dividends: 0, splits: 0 },
      items: [
        { date: '2025-01-02', unitNav: 1, accNav: 1.1, adjustedNav: 1, changePct: 0 },
      ],
    });
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

  it('defaults to restored basis and switches to unit values', async () => {
    list.mockResolvedValue({
      items: [
        makeItem({
          navReturnPct: -1,
          maxDrawdownPct: -2,
          restoredReturnPct: 10,
          restoredMaxDrawdownPct: -1,
        }),
      ],
      total: 1,
      page: 1,
      pageSize: 10,
    });
    getStats.mockResolvedValue(
      makeStats({
        positiveRatePct: 25,
        avgNavReturnPct: -1,
        avgMaxDrawdownPct: -2,
        restoredPositiveRatePct: 75,
        avgRestoredReturnPct: 10,
        avgRestoredMaxDrawdownPct: -1,
      }),
    );

    render(<FundNavReviewPanel />);
    expand();

    const restoredButton = await screen.findByRole('button', { name: '复权' });
    expect(restoredButton).toHaveAttribute('aria-pressed', 'true');

    await waitFor(() => expect(screen.getAllByText('+10.00%').length).toBeGreaterThan(0));
    expect(screen.getAllByText('75.00%').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '单位' }));

    expect(screen.getByRole('button', { name: '单位' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByText('-1.00%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('-2.00%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('25.00%').length).toBeGreaterThan(0);
    expect(screen.queryByText('+10.00%')).not.toBeInTheDocument();
  });

  it('shows the actual basis badge per row', async () => {
    list.mockResolvedValue({
      items: [makeItem({ restoredBasis: 'acc_nav' })],
      total: 1,
      page: 1,
      pageSize: 10,
    });

    render(<FundNavReviewPanel />);
    expand();

    expect(await screen.findByText('累计')).toBeInTheDocument();
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

  it('mounts the trend chart only after selecting a fund', async () => {
    list.mockResolvedValue({
      items: [makeItem(), makeItem({ id: 2, fundCode: '000001', fundName: '华夏成长' })],
      total: 2,
      page: 1,
      pageSize: 10,
    });

    render(<FundNavReviewPanel />);
    expand();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    expect(screen.queryByLabelText('净值走势')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '003095' }));

    expect(await screen.findByLabelText('净值走势')).toBeInTheDocument();
    await waitFor(() =>
      expect(getNavRange).toHaveBeenCalledWith(expect.objectContaining({ fund_code: '003095' })),
    );
    expect(screen.getByRole('button', { name: '003095' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '000001' }));

    await waitFor(() =>
      expect(getNavRange).toHaveBeenLastCalledWith(expect.objectContaining({ fund_code: '000001' })),
    );
  });
});
