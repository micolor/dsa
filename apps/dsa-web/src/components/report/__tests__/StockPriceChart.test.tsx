import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../../../api/stocks';
import { StockPriceChart } from '../StockPriceChart';

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    getStockHistory: vi.fn(),
  },
}));

const KLINE = [
  { date: '2026-08-01', open: 100, high: 108, low: 98, close: 106, volume: 1000 },
  { date: '2026-08-02', open: 106, high: 112, low: 104, close: 108, volume: 1200 },
  { date: '2026-08-03', open: 108, high: 110, low: 100, close: 102, volume: 900 },
];

describe('StockPriceChart', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 让测量包装（ref + ResizeObserver）拿到一个非零尺寸，从而渲染图表而非占位。
    const rect = {
      x: 0,
      y: 0,
      width: 640,
      height: 320,
      top: 0,
      left: 0,
      right: 640,
      bottom: 320,
      toJSON: () => ({}),
    } as DOMRect;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(rect);
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe(): void {}
        disconnect(): void {}
        unobserve(): void {}
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('fetches history and renders candlesticks', async () => {
    vi.mocked(stocksApi.getStockHistory).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      period: 'daily',
      data: KLINE,
    });

    const { container, getByTestId } = render(<StockPriceChart stockCode="600519" />);

    expect(stocksApi.getStockHistory).toHaveBeenCalledWith('600519', 60);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="stock-candle"]')).toBeTruthy();
    });

    expect(getByTestId('stock-price-chart')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-testid="stock-candle"]').length).toBe(KLINE.length);
    expect(screen.getByText('K 线走势')).toBeVisible();
  });

  it('renders the empty state when no data is returned', async () => {
    vi.mocked(stocksApi.getStockHistory).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      period: 'daily',
      data: [],
    });

    render(<StockPriceChart stockCode="600519" />);

    expect(await screen.findByTestId('stock-price-chart-empty')).toBeInTheDocument();
    expect(screen.getByText('暂无行情数据')).toBeVisible();
  });

  it('renders the loading placeholder before size is measured', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      toJSON: () => ({}),
    } as DOMRect);
    vi.mocked(stocksApi.getStockHistory).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      period: 'daily',
      data: KLINE,
    });

    render(<StockPriceChart stockCode="600519" />);

    expect(screen.getByTestId('stock-price-chart-loading')).toBeInTheDocument();
  });

  it('renders nothing when no stock code is provided', () => {
    const { container } = render(<StockPriceChart />);

    expect(container.innerHTML).toBe('');
  });
});
