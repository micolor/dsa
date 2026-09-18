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
    // 清掉上一个用例的调用历史，否则「不应被调用」这类断言会被前一个用例的调用污染。
    vi.clearAllMocks();
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

  it('renders a readable label for the dispositions that mean "wanted to trade but could not"', async () => {
    // 这几种 disposition 此前分别混在 ignored / hold 里，页面上显示成「忽略」「维持」，
    // 而实际原因是账户侧被现金或交易单位挡住了。新增取值若漏配标签，徽章会回退成
    // 裸英文串（`no_cash`），后端侧的 tests/test_paper_disposition_labels.py 守住了
    // 「标签存在」，这里守住「标签真的渲染到了页面上」。
    mockGetSignals.mockResolvedValue({
      items: [
        { signalId: 'sig-nc', action: 'buy', disposition: 'no_cash', stockCode: '600519', stockName: '贵州茅台', processedAt: '2026-01-05 10:00:00' },
        { signalId: 'sig-lt', action: 'buy', disposition: 'lot_too_small', stockCode: '000001', stockName: '平安银行', processedAt: '2026-01-05 10:00:00' },
        { signalId: 'sig-np', action: 'sell', disposition: 'no_position', stockCode: '300750', stockName: '宁德时代', processedAt: '2026-01-05 10:00:00' },
        { signalId: 'sig-nf', action: 'buy', disposition: 'no_fill', stockCode: '601318', stockName: '中国平安', processedAt: '2026-01-05 10:00:00' },
      ],
      total: 4,
    });

    render(
      <UiLanguageProvider>
        <PaperTradingPage />
      </UiLanguageProvider>
    );

    // 默认落在持仓页且本用例没有持仓，先切到信号页。
    await waitFor(() => expect(screen.getByRole('button', { name: 'Signals' })).toBeInTheDocument());
    screen.getByRole('button', { name: 'Signals' }).click();

    await waitFor(() => expect(screen.getByText('No cash')).toBeInTheDocument());
    expect(screen.getByText('Below one lot')).toBeInTheDocument();
    expect(screen.getByText('No position')).toBeInTheDocument();
    expect(screen.getByText('No fill')).toBeInTheDocument();

    // 取值本身不得直接透给用户。
    for (const raw of ['no_cash', 'lot_too_small', 'no_position', 'no_fill']) {
      expect(screen.queryByText(raw)).not.toBeInTheDocument();
    }
  });
});
