import type React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FundNavTrendChart } from '../FundNavTrendChart';

const { getNavRange } = vi.hoisted(() => ({
  getNavRange: vi.fn(),
}));

vi.mock('../../../api/fundNavReviews', () => ({
  fundNavReviewsApi: {
    getNavRange,
  },
}));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="nav-line-chart">{children}</div>
  ),
  Line: ({ dataKey }: { dataKey: string }) => <div data-testid={`nav-line-${dataKey}`} />,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  CartesianGrid: () => null,
}));

function makeRange(overrides: Record<string, unknown> = {}) {
  return {
    fundCode: '003095',
    startDate: '2025-01-01',
    endDate: '2025-01-31',
    adjustmentStatus: 'available',
    truncated: false,
    events: { dividends: 1, splits: 0 },
    items: [
      { date: '2025-01-02', unitNav: 1, accNav: 1.1, adjustedNav: 1, changePct: 0 },
      { date: '2025-01-03', unitNav: 0.9, accNav: 1.1, adjustedNav: 1, changePct: -10 },
    ],
    ...overrides,
  };
}

describe('FundNavTrendChart', () => {
  beforeEach(() => {
    getNavRange.mockReset();
  });

  it('loads the range and renders the adjusted series by default', async () => {
    getNavRange.mockResolvedValue(makeRange());

    render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);

    expect(await screen.findByLabelText('净值走势')).toBeInTheDocument();
    expect(getNavRange).toHaveBeenCalledWith({
      fund_code: '003095',
      start_date: '2025-01-01',
      end_date: '2025-01-31',
    });
    expect(screen.getByRole('button', { name: '复权' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByTestId('nav-line-adjusted')).toBeInTheDocument();
  });

  it('switches the displayed series', async () => {
    getNavRange.mockResolvedValue(makeRange());

    render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);
    await screen.findByTestId('nav-line-adjusted');

    fireEvent.click(screen.getByRole('button', { name: '单位' }));
    expect(await screen.findByTestId('nav-line-unit')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '单位' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '累计' }));
    expect(await screen.findByTestId('nav-line-acc')).toBeInTheDocument();
  });

  it('shows an empty state when the range has no records', async () => {
    getNavRange.mockResolvedValue(makeRange({ items: [] }));

    render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);

    expect(await screen.findByText(/暂无净值记录/)).toBeInTheDocument();
  });

  it('shows an error with retry when the request fails', async () => {
    getNavRange.mockRejectedValueOnce(new Error('network down'));
    getNavRange.mockResolvedValueOnce(makeRange());

    render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);

    expect(await screen.findByText(/加载净值走势失败/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByTestId('nav-line-adjusted')).toBeInTheDocument();
    expect(getNavRange).toHaveBeenCalledTimes(2);
  });

  it('disables the adjusted option when events are unavailable', async () => {
    getNavRange.mockResolvedValue(
      makeRange({
        adjustmentStatus: 'unavailable',
        items: [
          { date: '2025-01-02', unitNav: 1, accNav: 1.1, adjustedNav: null, changePct: 0 },
        ],
      }),
    );

    render(<FundNavTrendChart fundCode="003095" startDate="2025-01-01" endDate="2025-01-31" />);

    const adjustedButton = await screen.findByRole('button', { name: '复权' });
    expect(adjustedButton).toBeDisabled();
    expect(screen.getByRole('button', { name: '累计' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByText(/分红数据不可用/)).toBeInTheDocument();
  });
});
