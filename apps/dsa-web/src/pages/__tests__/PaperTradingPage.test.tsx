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
  mockRefresh,
  mockBackfill,
} = vi.hoisted(() => ({
  mockGetSnapshot: vi.fn(),
  mockGetPositions: vi.fn(),
  mockGetEquityCurve: vi.fn(),
  mockGetSignals: vi.fn(),
  mockGetTrades: vi.fn(),
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

describe('PaperTradingPage', () => {
  beforeEach(() => {
    mockGetSnapshot.mockResolvedValue(emptySnapshot);
    mockGetPositions.mockResolvedValue([]);
    mockGetEquityCurve.mockResolvedValue([]);
    mockGetSignals.mockResolvedValue({ items: [], total: 0 });
    mockGetTrades.mockResolvedValue({ items: [], total: 0 });
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
});
