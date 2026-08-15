import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import PaperTradingPage from '../PaperTradingPage';

const {
  mockGetSnapshot,
  mockGetPositions,
  mockGetEquityCurve,
  mockGetSignals,
  mockGetTrades,
  mockGetAccount,
  mockRefresh,
  mockBackfill,
} = vi.hoisted(() => ({
  mockGetSnapshot: vi.fn(),
  mockGetPositions: vi.fn(),
  mockGetEquityCurve: vi.fn(),
  mockGetSignals: vi.fn(),
  mockGetTrades: vi.fn(),
  mockGetAccount: vi.fn(),
  mockRefresh: vi.fn(),
  mockBackfill: vi.fn(),
}));

vi.mock('../../api/paper', () => ({
  paperApi: {
    getSnapshot: mockGetSnapshot,
    getPositions: mockGetPositions,
    getEquityCurve: mockGetEquityCurve,
    getSignals: mockGetSignals,
    getTrades: mockGetTrades,
    getAccount: mockGetAccount,
    refresh: mockRefresh,
    backfill: mockBackfill,
  },
}));

const emptySnapshot = {
  accountId: 1,
  cash: 1000000,
  marketValue: 0,
  netValue: 1000000,
  returnPct: 0,
  initialCapital: 1000000,
  openPositionCount: 0,
};

const mockAccount = {
  accountId: 1,
  name: '模拟账户',
  initialCapital: 1000000,
  cash: 1000000,
  status: 'active',
  snapshot: emptySnapshot,
};

describe('PaperTradingPage', () => {
  beforeEach(() => {
    mockGetSnapshot.mockResolvedValue(emptySnapshot);
    mockGetPositions.mockResolvedValue([]);
    mockGetEquityCurve.mockResolvedValue([]);
    mockGetSignals.mockResolvedValue({ items: [], total: 0 });
    mockGetTrades.mockResolvedValue({ items: [], total: 0 });
    mockGetAccount.mockResolvedValue(mockAccount);
    mockRefresh.mockResolvedValue({});
    mockBackfill.mockResolvedValue({});
  });

  it('renders metrics and empty states for a fresh account', async () => {
    render(
      <UiLanguageProvider>
        <PaperTradingPage />
      </UiLanguageProvider>
    );

    expect(await screen.findByText('Total assets')).toBeInTheDocument();
    expect(screen.getAllByText('1,000,000').length).toBeGreaterThan(0);
    expect(screen.getByText('No positions')).toBeInTheDocument();
    expect(screen.getByText('No equity data')).toBeInTheDocument();
  });

  it('renders an open position row', async () => {
    mockGetSnapshot.mockResolvedValue({
      ...emptySnapshot,
      marketValue: 200000,
      netValue: 1200000,
      openPositionCount: 1,
    });
    mockGetPositions.mockResolvedValue([
      {
        stockCode: '600519',
        stockName: '贵州茅台',
        market: 'cn',
        quantity: 2000,
        avgCost: 100,
        currentPrice: 100,
        marketValue: 200000,
        entryDate: '2026-01-05',
        stopLoss: 95,
        targetPrice: 115,
        status: 'open',
      },
    ]);

    render(
      <UiLanguageProvider>
        <PaperTradingPage />
      </UiLanguageProvider>
    );

    await waitFor(() => expect(screen.getByText('600519')).toBeInTheDocument());
    expect(screen.getByText('2000')).toBeInTheDocument();
    expect(screen.getByText('Open')).toBeInTheDocument();
  });

  it('switches between positions, signals and trades tabs', async () => {
    mockGetPositions.mockResolvedValue([
      {
        stockCode: '600519',
        stockName: '贵州茅台',
        market: 'cn',
        quantity: 2000,
        avgCost: 100,
        currentPrice: 100,
        marketValue: 200000,
        entryDate: '2026-01-05',
        stopLoss: 95,
        targetPrice: 115,
        status: 'open',
      },
    ]);
    mockGetSignals.mockResolvedValue({
      items: [{ signalId: 'sig-1', action: 'buy', disposition: 'opened', stockCode: '600519', stockName: '贵州茅台', processedAt: '2026-01-05 10:00:00' }],
      total: 1,
    });
    mockGetTrades.mockResolvedValue({
      items: [{ tradeDate: '2026-01-05', stockCode: '600519', side: 'buy', quantity: 2000, price: 100, reason: '测试' }],
      total: 1,
    });

    render(
      <UiLanguageProvider>
        <PaperTradingPage />
      </UiLanguageProvider>
    );

    // Default tab is positions.
    await waitFor(() => expect(screen.getByText('600519')).toBeInTheDocument());

    // The three top-level tabs are present.
    expect(screen.getByRole('button', { name: 'Positions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Signals' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Trades' })).toBeInTheDocument();

    // Switch to signals (positions are unmounted, so 贵州茅台 now refers to the signal row).
    screen.getByRole('button', { name: 'Signals' }).click();
    await waitFor(() => expect(screen.getByText('贵州茅台')).toBeInTheDocument());
    expect(screen.getByText('Buy')).toBeInTheDocument();

    // Switch to trades.
    screen.getByRole('button', { name: 'Trades' }).click();
    await waitFor(() => expect(screen.getByText('测试')).toBeInTheDocument());
    expect(screen.getByText('Buy')).toBeInTheDocument();
  });
});
