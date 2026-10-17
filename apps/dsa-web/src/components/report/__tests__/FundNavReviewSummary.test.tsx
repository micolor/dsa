import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FundNavReviewSummary } from '../FundNavReviewSummary';

const { list } = vi.hoisted(() => ({
  list: vi.fn(),
}));

vi.mock('../../../api/fundNavReviews', () => ({
  fundNavReviewsApi: {
    list,
  },
}));

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    fundCode: '003095',
    fundName: '中欧医疗健康混合A',
    horizon: '3m',
    engineVersion: 'fund-nav-v1',
    anchorDate: '2025-01-02',
    anchorNav: 1.2,
    dueDate: '2025-04-12',
    evalStatus: 'evaluated',
    evalDate: '2025-04-10',
    evalNav: 1.3,
    navReturnPct: -2.5,
    maxDrawdownPct: -8.0,
    restoredReturnPct: 6.5,
    restoredMaxDrawdownPct: -4.0,
    restoredBasis: 'dividend_reinvested',
    anchorSource: 'pipeline',
    attempts: 1,
    ...overrides,
  };
}

describe('FundNavReviewSummary', () => {
  beforeEach(() => {
    list.mockReset();
    list.mockResolvedValue({ items: [makeItem()], total: 1, page: 1, pageSize: 5 });
  });

  it('loads recent reviews for a fund code', async () => {
    render(<FundNavReviewSummary fundCode="fund:003095" />);

    expect(await screen.findByText('净值复盘')).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith({ fundCode: '003095', page: 1, pageSize: 5 });
    expect(await screen.findByText('+6.50%')).toBeInTheDocument();
    expect(screen.getByText('-4.00%')).toBeInTheDocument();
    expect(screen.getByText('3m')).toBeInTheDocument();
  });

  it('does not fetch for a stock code', () => {
    render(<FundNavReviewSummary fundCode="600519" />);

    expect(list).not.toHaveBeenCalled();
    expect(screen.queryByText('净值复盘')).not.toBeInTheDocument();
  });

  it('shows an empty state when the fund has no anchors yet', async () => {
    list.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 5 });

    render(<FundNavReviewSummary fundCode="fund:003095" />);

    expect(await screen.findByText('暂无复盘记录')).toBeInTheDocument();
  });

  it('offers a retry when loading fails', async () => {
    list.mockRejectedValueOnce(new Error('network down'));
    list.mockResolvedValueOnce({ items: [makeItem()], total: 1, page: 1, pageSize: 5 });

    render(<FundNavReviewSummary fundCode="fund:003095" />);

    expect(await screen.findByText(/加载净值复盘失败/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('+6.50%')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });
});
