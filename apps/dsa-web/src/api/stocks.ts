import apiClient from './index';

// 首页 K 线历史行情的短时缓存。默认每次切换/重新选中报告都会重新拉取；
// 用 (code|days) 作键、3 分钟 TTL 缓存成功结果，避免同一股票反复触发网络请求。
const HISTORY_CACHE_TTL_MS = 3 * 60 * 1000;
const stockHistoryCache = new Map<string, { expiresAt: number; promise: Promise<StockHistory> }>();

function cacheGetStockHistory(code: string, days: number, fetcher: () => Promise<StockHistory>): Promise<StockHistory> {
  const key = `${code}|${days}`;
  const hit = stockHistoryCache.get(key);
  if (hit && hit.expiresAt > Date.now()) {
    return hit.promise;
  }
  const promise = fetcher();
  stockHistoryCache.set(key, { expiresAt: Date.now() + HISTORY_CACHE_TTL_MS, promise });
  // 失败条目不缓存：立即驱逐，后续调用可重试，避免一次网络错误卡住整个 TTL
  promise.catch(() => {
    if (stockHistoryCache.get(key)?.promise === promise) {
      stockHistoryCache.delete(key);
    }
  });
  return promise;
}

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export type StockQuote = {
  stockCode: string;
  stockName?: string | null;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prevClose?: number | null;
  volume?: number | null;
  amount?: number | null;
  updateTime?: string | null;
};

export type KLineItem = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
  changePercent?: number | null;
};

export type StockHistory = {
  stockCode: string;
  stockName?: string;
  period: string;
  data: KLineItem[];
};

export const stocksApi = {
  /**
   * 获取日 K 历史行情（period=daily）。
   * @param days 获取的天数
   */
  getStockHistory(code: string, days = 60): Promise<StockHistory> {
    return cacheGetStockHistory(code, days, async () => {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/stocks/${encodeURIComponent(code)}/history`,
        { params: { period: 'daily', days } },
      );
      const d = response.data;
      const raw = (Array.isArray(d.data) ? d.data : []) as Array<Record<string, unknown>>;
      return {
        stockCode: String(d.stock_code ?? code),
        stockName: d.stock_name == null ? undefined : String(d.stock_name),
        period: String(d.period ?? 'daily'),
        data: raw.map((item) => ({
          date: String(item.date ?? ''),
          open: Number(item.open ?? 0),
          high: Number(item.high ?? 0),
          low: Number(item.low ?? 0),
          close: Number(item.close ?? 0),
          volume: item.volume == null ? undefined : Number(item.volume),
          amount: item.amount == null ? undefined : Number(item.amount),
          changePercent: item.change_percent == null ? undefined : Number(item.change_percent),
        })),
      };
    });
  },

  async getQuote(code: string): Promise<StockQuote> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/quote`,
    );
    const d = response.data;
    return {
      stockCode: String(d.stock_code ?? code),
      stockName: d.stock_name == null ? undefined : String(d.stock_name),
      currentPrice: Number(d.current_price ?? 0),
      change: d.change == null ? undefined : Number(d.change),
      changePercent: d.change_percent == null ? undefined : Number(d.change_percent),
      open: d.open == null ? undefined : Number(d.open),
      high: d.high == null ? undefined : Number(d.high),
      low: d.low == null ? undefined : Number(d.low),
      prevClose: d.prev_close == null ? undefined : Number(d.prev_close),
      volume: d.volume == null ? undefined : Number(d.volume),
      amount: d.amount == null ? undefined : Number(d.amount),
      updateTime: d.update_time == null ? undefined : String(d.update_time),
    };
  },

  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },
};
