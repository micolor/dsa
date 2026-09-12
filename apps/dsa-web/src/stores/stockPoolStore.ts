import { create } from 'zustand';
import { analysisApi, DuplicateTaskError } from '../api/analysis';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { historyApi } from '../api/history';
import type { AnalysisReport, HistoryItem, HistoryListResponse, ReportLanguage, StockBarItem, StockHistoryFilters, StockHistoryRange, TaskInfo } from '../types/analysis';
import { getRecentStartDate, getTodayInShanghai } from '../utils/format';
import { stockCodeKey } from '../utils/stockCode';
import { isMarketReviewTask } from '../utils/taskKind';
import { isObviouslyInvalidStockQuery, looksLikeStockCode, validateStockCode } from '../utils/validation';

const PAGE_SIZE = 20;
const STOCK_HISTORY_PAGE_SIZE = 20;
const MARKET_REVIEW_HISTORY_PAGE_SIZE = 10;
const MARKET_REVIEW_HISTORY_CODE = 'MARKET';

type SelectionSource = 'manual' | 'autocomplete' | 'import' | 'image';

/** `error` 的来源路径；null 表示不可自动回收（分析 / 任务失败等一次性事件）。 */
type ErrorScope = 'history' | 'marketReviewHistory';

type FetchHistoryOptions = {
  autoSelectFirst?: boolean;
  reset?: boolean;
  silent?: boolean;
  selectLatestForStockCode?: string;
};

type SubmitAnalysisOptions = {
  stockCode?: string;
  stockName?: string;
  originalQuery?: string;
  selectionSource?: SelectionSource;
  notify?: boolean;
  forceRefresh?: boolean;
  skills?: string[];
  reportLanguage?: ReportLanguage;
};

type CompletedTaskSelectionIntent = {
  manualSelectionSeq: number;
  selectedReportId: number | undefined;
};

let reportRequestSeq = 0;
let analyzeRequestSeq = 0;
let historyRequestSeq = 0;
let marketReviewHistoryRequestSeq = 0;
let stockHistoryRequestSeq = 0;
let stockBarRequestSeq = 0;
let activeTaskRequestSeq = 0;
let activeTaskLocalRevision = 0;
let manualSelectionRequestSeq = 0;
let manualSelectionRequestId = 0;
const dismissedTaskIds = new Set<string>();
const pendingCompletedTaskSelectionKeys = new Map<string, CompletedTaskSelectionIntent>();

export interface StockPoolState {
  query: string;
  selectionSource: SelectionSource;
  notify: boolean;
  inputError?: string;
  duplicateError: string | null;
  error: ParsedApiError | null;
  /**
   * `error` 的来源路径；无来源为 null。
   *
   * `error` 是共享槽位（历史列表 / 复盘历史 / 分析失败 / 任务失败都往里写），而首页顶部
   * 那条红条是事件通知，只在手动关闭时消失。给来源打标后，同一条路径重新取数成功就能
   * 把自己的失败提示收回去——「无法连接到本地服务」这类**状态性**描述在后端恢复后
   * 不该继续挂着；没有标记的来源（分析 / 任务失败）不会被别的请求顺手抹掉。
   */
  errorScope: ErrorScope | null;
  isAnalyzing: boolean;
  historyItems: HistoryItem[];
  selectedHistoryIds: number[];
  isDeletingHistory: boolean;
  isLoadingHistory: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  currentPage: number;
  marketReviewHistoryItems: HistoryItem[];
  selectedMarketReviewHistoryIds: number[];
  isLoadingMarketReviewHistory: boolean;
  isLoadingMoreMarketReviewHistory: boolean;
  isDeletingMarketReviewHistory: boolean;
  marketReviewHistoryHasMore: boolean;
  marketReviewHistoryPage: number;
  selectedReport: AnalysisReport | null;
  isLoadingReport: boolean;
  isHistoryTrendOpen: boolean;
  stockHistoryItems: HistoryItem[];
  stockHistoryTotal: number;
  stockHistoryPage: number;
  stockHistoryHasMore: boolean;
  isLoadingStockHistory: boolean;
  isLoadingMoreStockHistory: boolean;
  stockHistoryError: ParsedApiError | null;
  stockHistoryFilters: StockHistoryFilters;
  activeTasks: TaskInfo[];
  markdownDrawerOpen: boolean;
  stockBarItems: StockBarItem[];
  isLoadingStockBar: boolean;
  stockBarRefreshFailed: boolean;
  setQuery: (query: string) => void;
  clearError: () => void;
  clearInlineMessages: () => void;
  openMarkdownDrawer: () => void;
  closeMarkdownDrawer: () => void;
  openHistoryTrend: () => Promise<void>;
  closeHistoryTrend: () => void;
  setStockHistoryRange: (range: StockHistoryRange) => Promise<void>;
  loadMoreStockHistory: () => Promise<void>;
  loadInitialHistory: () => Promise<void>;
  refreshHistory: (silent?: boolean) => Promise<void>;
  refreshHistoryForCompletedTask: (task: TaskInfo) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  loadMarketReviewHistory: () => Promise<void>;
  // 返回最新一页结果，便于调用方（如大盘复盘任务完成时）直接取到刚落库的记录 ID。
  refreshMarketReviewHistory: (silent?: boolean) => Promise<HistoryListResponse | null>;
  loadMoreMarketReviewHistory: () => Promise<void>;
  selectHistoryItem: (recordId: number, isUserInitiated?: boolean) => Promise<void>;
  toggleHistorySelection: (recordId: number) => void;
  toggleSelectAllVisible: () => void;
  deleteSelectedHistory: () => Promise<void>;
  toggleMarketReviewHistorySelection: (recordId: number) => void;
  toggleSelectAllVisibleMarketReviewHistory: () => void;
  deleteSelectedMarketReviewHistory: () => Promise<void>;
  submitAnalysis: (options?: SubmitAnalysisOptions) => Promise<void>;
  setNotify: (notify: boolean) => void;
  syncTaskCreated: (task: TaskInfo) => void;
  syncTaskUpdated: (task: TaskInfo) => void;
  syncTaskFailed: (task: TaskInfo) => void;
  refreshActiveTasks: () => Promise<void>;
  removeTask: (taskId: string) => void;
  resetDashboardState: () => void;
  loadStockBar: () => Promise<void>;
  /** silent=true 用于后台刷新：不置 isLoadingStockBar，避免界面进入交互假态。 */
  refreshStockBar: (silent?: boolean) => Promise<void>;
}

const initialState = {
  query: '',
  selectionSource: 'manual' as SelectionSource,
  notify: true,
  inputError: undefined,
  duplicateError: null,
  error: null,
  errorScope: null,
  isAnalyzing: false,
  historyItems: [] as HistoryItem[],
  selectedHistoryIds: [] as number[],
  isDeletingHistory: false,
  isLoadingHistory: false,
  isLoadingMore: false,
  hasMore: true,
  currentPage: 1,
  marketReviewHistoryItems: [] as HistoryItem[],
  selectedMarketReviewHistoryIds: [] as number[],
  isLoadingMarketReviewHistory: false,
  isLoadingMoreMarketReviewHistory: false,
  isDeletingMarketReviewHistory: false,
  marketReviewHistoryHasMore: false,
  marketReviewHistoryPage: 1,
  selectedReport: null as AnalysisReport | null,
  isLoadingReport: false,
  isHistoryTrendOpen: false,
  stockHistoryItems: [] as HistoryItem[],
  stockHistoryTotal: 0,
  stockHistoryPage: 1,
  stockHistoryHasMore: false,
  isLoadingStockHistory: false,
  isLoadingMoreStockHistory: false,
  stockHistoryError: null as ParsedApiError | null,
  stockHistoryFilters: {
    range: 'all' as StockHistoryRange,
    model: 'all',
    sort: 'desc' as const,
  },
  activeTasks: [] as TaskInfo[],
  markdownDrawerOpen: false,
  stockBarItems: [] as StockBarItem[],
  isLoadingStockBar: false,
  stockBarRefreshFailed: false,
};

function buildHistoryParams(page: number) {
  return {
    startDate: getRecentStartDate(30),
    endDate: getTodayInShanghai(),
    page,
    limit: PAGE_SIZE,
  };
}

function buildMarketReviewHistoryParams(page: number) {
  return {
    stockCode: MARKET_REVIEW_HISTORY_CODE,
    reportType: 'market_review' as const,
    page,
    limit: MARKET_REVIEW_HISTORY_PAGE_SIZE,
  };
}

function buildStockHistoryParams(stockCode: string, page: number, filters: StockHistoryFilters) {
  const params: {
    stockCode: string;
    reportType?: 'market_review';
    startDate?: string;
    endDate?: string;
    page: number;
    limit: number;
  } = {
    stockCode,
    page,
    limit: STOCK_HISTORY_PAGE_SIZE,
  };

  if (stockCode === MARKET_REVIEW_HISTORY_CODE) {
    params.reportType = 'market_review';
  }

  if (filters.range === '30d') {
    params.startDate = getRecentStartDate(30);
    params.endDate = getTodayInShanghai();
  } else if (filters.range === '90d') {
    params.startDate = getRecentStartDate(90);
    params.endDate = getTodayInShanghai();
  }

  return params;
}

function reportToHistoryItem(report: AnalysisReport): HistoryItem | null {
  if (report.meta.id === undefined) {
    return null;
  }

  return {
    id: report.meta.id,
    queryId: report.meta.queryId,
    stockCode: report.meta.stockCode,
    stockName: report.meta.stockName,
    reportType: report.meta.reportType,
    trendPrediction: report.summary.trendPrediction,
    analysisSummary: report.summary.analysisSummary,
    sentimentScore: report.summary.sentimentScore,
    operationAdvice: report.summary.operationAdvice,
    action: report.summary.action,
    actionLabel: report.summary.actionLabel,
    currentPrice: report.meta.currentPrice,
    changePct: report.meta.changePct,
    modelUsed: report.meta.modelUsed,
    createdAt: report.meta.createdAt,
  };
}

function normalizeSelectedReport(report: AnalysisReport): AnalysisReport {
  if (report.meta.reportType !== 'market_review' || report.meta.stockCode) {
    return report;
  }
  return {
    ...report,
    meta: {
      ...report.meta,
      stockCode: MARKET_REVIEW_HISTORY_CODE,
    },
  };
}

function queueCompletedTaskSelection(
  stockCode: string | undefined,
  selectedReport: AnalysisReport | null,
): void {
  const key = stockCodeKey(stockCode);
  if (key) {
    pendingCompletedTaskSelectionKeys.set(key, {
      manualSelectionSeq: manualSelectionRequestSeq,
      selectedReportId: selectedReport?.meta.id,
    });
  }
}

function consumeCompletedTaskSelection(items: HistoryItem[], selectedReport: AnalysisReport | null): HistoryItem | undefined {
  if (pendingCompletedTaskSelectionKeys.size === 0) {
    return undefined;
  }
  if (manualSelectionRequestId !== 0) {
    pendingCompletedTaskSelectionKeys.clear();
    return undefined;
  }

  if (selectedReport?.meta.reportType === 'market_review') {
    pendingCompletedTaskSelectionKeys.clear();
    return undefined;
  }

  if (selectedReport) {
    const selectedStockCode = stockCodeKey(selectedReport.meta.stockCode);
    const pendingSelectionIntent = selectedStockCode
      ? pendingCompletedTaskSelectionKeys.get(selectedStockCode)
      : undefined;
    if (!selectedStockCode || pendingSelectionIntent === undefined) {
      pendingCompletedTaskSelectionKeys.clear();
      return undefined;
    }
    if (pendingSelectionIntent.manualSelectionSeq !== manualSelectionRequestSeq) {
      pendingCompletedTaskSelectionKeys.clear();
      return undefined;
    }
    if (pendingSelectionIntent.selectedReportId !== selectedReport.meta.id) {
      pendingCompletedTaskSelectionKeys.clear();
      return undefined;
    }

    for (const key of Array.from(pendingCompletedTaskSelectionKeys.keys())) {
      if (key !== selectedStockCode) {
        pendingCompletedTaskSelectionKeys.delete(key);
      }
    }

    const latestItem = items.find(
      (item) =>
        item.reportType !== 'market_review' &&
        stockCodeKey(item.stockCode) === selectedStockCode,
    );
    if (latestItem) {
      pendingCompletedTaskSelectionKeys.delete(selectedStockCode);
    }
    return latestItem;
  }

  const latestItem = items.find((item) => {
    if (item.reportType === 'market_review') {
      return false;
    }
    const stockCode = stockCodeKey(item.stockCode);
    const pendingSelectionIntent = pendingCompletedTaskSelectionKeys.get(stockCode);
    return stockCode.length > 0 && pendingSelectionIntent?.manualSelectionSeq === manualSelectionRequestSeq;
  });
  if (latestItem) {
    pendingCompletedTaskSelectionKeys.clear();
  }
  return latestItem;
}

function isDateInHistoryRange(createdAt: string | undefined, range: StockHistoryRange): boolean {
  if (range === 'all') {
    return true;
  }
  if (!createdAt) {
    return false;
  }

  const reportDate = createdAt.slice(0, 10);
  const startDate = range === '30d' ? getRecentStartDate(30) : getRecentStartDate(90);
  const endDate = getTodayInShanghai();

  return reportDate >= startDate && reportDate <= endDate;
}

function includeSelectedReport(
  items: HistoryItem[],
  report: AnalysisReport,
  range: StockHistoryRange,
): HistoryItem[] {
  const current = reportToHistoryItem(report);
  if (!current || !isDateInHistoryRange(current.createdAt, range) || items.some((item) => item.id === current.id)) {
    return items;
  }
  return [current, ...items];
}

function dedupeHistoryItems(items: HistoryItem[]): HistoryItem[] {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.id)) {
      return false;
    }
    seen.add(item.id);
    return true;
  });
}

function resetStockHistoryState(set: (partial: Partial<StockPoolState>) => void) {
  set({
    stockHistoryItems: [],
    stockHistoryTotal: 0,
    stockHistoryPage: 1,
    stockHistoryHasMore: false,
    isLoadingStockHistory: false,
    isLoadingMoreStockHistory: false,
    stockHistoryError: null,
  });
}

async function fetchStockHistory(
  get: () => StockPoolState,
  set: (partial: Partial<StockPoolState>) => void,
  options: { reset?: boolean } = {},
): Promise<HistoryListResponse | null> {
  const { reset = true } = options;
  const state = get();
  const report = state.selectedReport;

  if (!report || !report.meta.stockCode) {
    resetStockHistoryState(set);
    set({
      isHistoryTrendOpen: false,
    });
    return null;
  }

  const page = reset ? 1 : state.stockHistoryPage + 1;
  const requestId = ++stockHistoryRequestSeq;
  set(
    reset
      ? { isLoadingStockHistory: true, isLoadingMoreStockHistory: false, stockHistoryError: null }
      : { isLoadingMoreStockHistory: true, stockHistoryError: null },
  );

  try {
    const response = await historyApi.getList(
      buildStockHistoryParams(report.meta.stockCode, page, state.stockHistoryFilters),
    );
    if (requestId !== stockHistoryRequestSeq) {
      return null;
    }

    const nextItems = reset
      ? dedupeHistoryItems(includeSelectedReport(response.items, report, state.stockHistoryFilters.range))
      : dedupeHistoryItems([...get().stockHistoryItems, ...response.items]);
    const nextTotal = Math.max(response.total, nextItems.length);
    set({
      stockHistoryItems: nextItems,
      stockHistoryTotal: nextTotal,
      stockHistoryPage: page,
      stockHistoryHasMore: nextItems.length < nextTotal,
    });
    return response;
  } catch (error) {
    if (requestId !== stockHistoryRequestSeq) {
      return null;
    }
    set({ stockHistoryError: getParsedApiError(error) });
    return null;
  } finally {
    if (requestId === stockHistoryRequestSeq) {
      set({
        isLoadingStockHistory: false,
        isLoadingMoreStockHistory: false,
      });
    }
  }
}

async function fetchHistory(
  get: () => StockPoolState,
  set: (partial: Partial<StockPoolState>) => void,
  options: FetchHistoryOptions = {},
): Promise<HistoryListResponse | null> {
  const {
    autoSelectFirst = false,
    reset = true,
    silent = false,
    selectLatestForStockCode,
  } = options;
  const currentState = get();
  const page = reset ? 1 : currentState.currentPage + 1;
  if (reset) {
    queueCompletedTaskSelection(selectLatestForStockCode, currentState.selectedReport);
  }
  const requestId = ++historyRequestSeq;

  if (!silent) {
    set(
      reset
        ? { isLoadingHistory: true, isLoadingMore: false, currentPage: 1 }
        : { isLoadingMore: true },
    );
  }

  try {
    const response = await historyApi.getList(buildHistoryParams(page));
    if (requestId !== historyRequestSeq) {
      return null;
    }

    // 本路径重新取数成功，就把自己上次失败留下的红条收回去（包括 30 秒静默刷新的成功：
    // 「无法连接到本地服务」是对当前状态的描述，后端恢复后不该继续挂着）。
    // 只看 errorScope：别的路径（分析 / 任务失败）写的提示不会被这次刷新顺手抹掉。
    if (get().errorScope === 'history') {
      set({ error: null, errorScope: null });
    }

    if (silent && reset) {
      const existingIds = new Set(get().historyItems.map((item) => item.id));
      const newItems = response.items.filter((item) => !existingIds.has(item.id));
      if (newItems.length > 0) {
        set({ historyItems: [...newItems, ...get().historyItems] });
      }
    } else if (reset) {
      set({
        historyItems: response.items,
        currentPage: 1,
      });
    } else {
      set({
        historyItems: [...get().historyItems, ...response.items],
        currentPage: page,
      });
    }

    if (!silent) {
      const totalLoaded = reset ? response.items.length : get().historyItems.length;
      set({ hasMore: totalLoaded < response.total });
    }

    const visibleIds = new Set(get().historyItems.map((item) => item.id));
    set({
      selectedHistoryIds: get().selectedHistoryIds.filter((id) => visibleIds.has(id)),
    });

    if (reset) {
      const latestCompletedTaskItem = consumeCompletedTaskSelection(response.items, get().selectedReport);
      const selectedReport = get().selectedReport;
      if (latestCompletedTaskItem && latestCompletedTaskItem.id !== selectedReport?.meta.id) {
        await get().selectHistoryItem(latestCompletedTaskItem.id, false);
      } else if (autoSelectFirst && response.items.length > 0 && !selectedReport) {
        await get().selectHistoryItem(response.items[0].id, false);
      }
    }

    return response;
  } catch (error) {
    if (requestId !== historyRequestSeq) {
      return null;
    }
    // 后台静默刷新（30s 定时 / 回到前台）失败不写全局 error：
    // 用户没有做任何操作，却会在首页顶部看到一条粘性红条，而数据其实还在。
    if (!silent) {
      set({ error: getParsedApiError(error), errorScope: 'history' });
    }
    return null;
  } finally {
    if (requestId === historyRequestSeq) {
      set({
        isLoadingHistory: false,
        isLoadingMore: false,
      });
    }
  }
}

async function fetchMarketReviewHistory(
  get: () => StockPoolState,
  set: (partial: Partial<StockPoolState>) => void,
  options: FetchHistoryOptions = {},
): Promise<HistoryListResponse | null> {
  const { reset = true, silent = false } = options;
  const currentState = get();
  const page = reset ? 1 : currentState.marketReviewHistoryPage + 1;
  const requestId = ++marketReviewHistoryRequestSeq;

  if (!silent) {
    set(
      reset
        ? { isLoadingMarketReviewHistory: true, isLoadingMoreMarketReviewHistory: false, marketReviewHistoryPage: 1 }
        : { isLoadingMoreMarketReviewHistory: true },
    );
  }

  try {
    const response = await historyApi.getList(buildMarketReviewHistoryParams(page));
    if (requestId !== marketReviewHistoryRequestSeq) {
      return null;
    }

    // 同 fetchHistory：本路径重新取数成功就收回自己的失败提示，静默成功同样生效。
    if (get().errorScope === 'marketReviewHistory') {
      set({ error: null, errorScope: null });
    }

    if (silent && reset) {
      const existingIds = new Set(get().marketReviewHistoryItems.map((item) => item.id));
      const newItems = response.items.filter((item) => !existingIds.has(item.id));
      if (newItems.length > 0) {
        set({ marketReviewHistoryItems: [...newItems, ...get().marketReviewHistoryItems] });
      }
    } else if (reset) {
      set({
        marketReviewHistoryItems: response.items,
        marketReviewHistoryPage: 1,
      });
    } else {
      set({
        marketReviewHistoryItems: dedupeHistoryItems([...get().marketReviewHistoryItems, ...response.items]),
        marketReviewHistoryPage: page,
      });
    }

    const totalLoaded = reset ? response.items.length : get().marketReviewHistoryItems.length;
    set({ marketReviewHistoryHasMore: totalLoaded < response.total });

    const visibleIds = new Set(get().marketReviewHistoryItems.map((item) => item.id));
    set({
      selectedMarketReviewHistoryIds: get().selectedMarketReviewHistoryIds.filter((id) => visibleIds.has(id)),
    });

    return response;
  } catch (error) {
    if (requestId !== marketReviewHistoryRequestSeq) {
      return null;
    }
    // 同上：后台静默刷新失败不弹全局红条。
    if (!silent) {
      set({ error: getParsedApiError(error), errorScope: 'marketReviewHistory' });
    }
    return null;
  } finally {
    if (requestId === marketReviewHistoryRequestSeq) {
      set({
        isLoadingMarketReviewHistory: false,
        isLoadingMoreMarketReviewHistory: false,
      });
    }
  }
}

export const useStockPoolStore = create<StockPoolState>((set, get) => ({
  ...initialState,

  setQuery: (query) => {
    set({
      query,
      selectionSource: 'manual',
      inputError: undefined,
      duplicateError: null,
    });
  },

  clearError: () => set({ error: null, errorScope: null }),

  clearInlineMessages: () => set({ inputError: undefined, duplicateError: null }),

  setNotify: (notify) => set({ notify }),

  openMarkdownDrawer: () => set({ markdownDrawerOpen: true }),

  closeMarkdownDrawer: () => set({ markdownDrawerOpen: false }),

  openHistoryTrend: async () => {
    if (!get().selectedReport || !get().selectedReport?.meta.stockCode) {
      return;
    }
    set({ isHistoryTrendOpen: true });
    await fetchStockHistory(get, set, { reset: true });
  },

  closeHistoryTrend: () => {
    stockHistoryRequestSeq += 1;
    resetStockHistoryState(set);
    set({
      isHistoryTrendOpen: false,
    });
  },

  setStockHistoryRange: async (range) => {
    set({
      stockHistoryFilters: {
        ...get().stockHistoryFilters,
        range,
      },
    });
    if (get().isHistoryTrendOpen) {
      await fetchStockHistory(get, set, { reset: true });
    }
  },

  loadMoreStockHistory: async () => {
    const state = get();
    if (!state.isHistoryTrendOpen || state.isLoadingMoreStockHistory || !state.stockHistoryHasMore) {
      return;
    }
    await fetchStockHistory(get, set, { reset: false });
  },

  loadInitialHistory: async () => {
    await fetchHistory(get, set, { autoSelectFirst: true, reset: true });
  },

  refreshHistory: async (silent = false) => {
    await fetchHistory(get, set, { reset: true, silent });
  },

  refreshHistoryForCompletedTask: async (task) => {
    await fetchHistory(get, set, {
      reset: true,
      silent: true,
      // 大盘复盘任务没有可自动选中的个股记录（它落库的 stock_code 是 MARKET，
      // 不走 historyItems 这条列表），传 undefined 表示不做补选。此前用
      // reportType === 'market_review' 判断，而这类任务的 reportType 实际是 'detailed'，
      // 于是把 'market_review' 当股票代码塞进了待补选队列。
      selectLatestForStockCode: isMarketReviewTask(task) ? undefined : task.stockCode,
    });
  },

  loadMoreHistory: async () => {
    const state = get();
    if (state.isLoadingMore || !state.hasMore) {
      return;
    }
    await fetchHistory(get, set, { reset: false });
  },

  loadMarketReviewHistory: async () => {
    await fetchMarketReviewHistory(get, set, { reset: true });
  },

  refreshMarketReviewHistory: async (silent = false) => {
    return fetchMarketReviewHistory(get, set, { reset: true, silent });
  },

  loadMoreMarketReviewHistory: async () => {
    const state = get();
    if (state.isLoadingMoreMarketReviewHistory || !state.marketReviewHistoryHasMore) {
      return;
    }
    await fetchMarketReviewHistory(get, set, { reset: false });
  },

  selectHistoryItem: async (recordId, isUserInitiated = true) => {
    const requestId = ++reportRequestSeq;
    if (isUserInitiated) {
      manualSelectionRequestSeq += 1;
      manualSelectionRequestId = requestId;
    }
    const shouldShowInitialLoading = !get().selectedReport;

    if (shouldShowInitialLoading) {
      set({ isLoadingReport: true });
    }

    try {
      const report = normalizeSelectedReport(await historyApi.getDetail(recordId));
      if (requestId !== reportRequestSeq) {
        return;
      }

      set({
        selectedReport: report,
        error: null,
        errorScope: null,
        isLoadingReport: false,
      });

      if (!report.meta.stockCode) {
        stockHistoryRequestSeq += 1;
        resetStockHistoryState(set);
        set({ isHistoryTrendOpen: false });
        return;
      }

      if (get().isHistoryTrendOpen) {
        await fetchStockHistory(get, set, { reset: true });
      }
    } catch (error) {
      if (requestId !== reportRequestSeq) {
        return;
      }

      set({
        // 报告详情失败没有「重新取数」的自动路径，红条只能手动关；不打来源标记，
        // 免得被后续历史列表刷新顺手抹掉。
        error: getParsedApiError(error),
        errorScope: null,
        isLoadingReport: false,
      });
    } finally {
      if (isUserInitiated && manualSelectionRequestId === requestId) {
        manualSelectionRequestId = 0;
      }
    }
  },

  toggleHistorySelection: (recordId) => {
    const selected = new Set(get().selectedHistoryIds);
    if (selected.has(recordId)) {
      selected.delete(recordId);
    } else {
      selected.add(recordId);
    }

    set({ selectedHistoryIds: Array.from(selected) });
  },

  toggleSelectAllVisible: () => {
    const visibleIds = get().historyItems.map((item) => item.id);
    const selectedIds = get().selectedHistoryIds;
    const visibleSet = new Set(visibleIds);
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));

    set({
      selectedHistoryIds: allSelected
        ? selectedIds.filter((id) => !visibleSet.has(id))
        : Array.from(new Set([...selectedIds, ...visibleIds])),
    });
  },

  deleteSelectedHistory: async () => {
    const state = get();
    const recordIds = Array.from(new Set(state.selectedHistoryIds));
    if (recordIds.length === 0 || state.isDeletingHistory) {
      return;
    }

    set({ isDeletingHistory: true });
    try {
      await historyApi.deleteRecords(recordIds);

      const deletedIds = new Set(recordIds);
      const selectedWasDeleted = state.selectedReport?.meta.id !== undefined
        && deletedIds.has(state.selectedReport.meta.id);

      set({ selectedHistoryIds: [] });

      const freshPage = await fetchHistory(get, set, { reset: true });

      if (selectedWasDeleted) {
        const nextItem = freshPage?.items?.[0];
        if (nextItem) {
          await get().selectHistoryItem(nextItem.id, false);
        } else {
          stockHistoryRequestSeq += 1;
          resetStockHistoryState(set);
          set({
            isHistoryTrendOpen: false,
            selectedReport: null,
          });
        }
      }
    } catch (error) {
      set({ error: getParsedApiError(error), errorScope: null });
    } finally {
      set({ isDeletingHistory: false });
    }
  },

  toggleMarketReviewHistorySelection: (recordId) => {
    const selected = new Set(get().selectedMarketReviewHistoryIds);
    if (selected.has(recordId)) {
      selected.delete(recordId);
    } else {
      selected.add(recordId);
    }

    set({ selectedMarketReviewHistoryIds: Array.from(selected) });
  },

  toggleSelectAllVisibleMarketReviewHistory: () => {
    const visibleIds = get().marketReviewHistoryItems.map((item) => item.id);
    const selectedIds = get().selectedMarketReviewHistoryIds;
    const visibleSet = new Set(visibleIds);
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));

    set({
      selectedMarketReviewHistoryIds: allSelected
        ? selectedIds.filter((id) => !visibleSet.has(id))
        : Array.from(new Set([...selectedIds, ...visibleIds])),
    });
  },

  deleteSelectedMarketReviewHistory: async () => {
    const state = get();
    const recordIds = Array.from(new Set(state.selectedMarketReviewHistoryIds));
    if (recordIds.length === 0 || state.isDeletingMarketReviewHistory) {
      return;
    }

    set({ isDeletingMarketReviewHistory: true });
    try {
      await historyApi.deleteRecords(recordIds);

      const deletedIds = new Set(recordIds);
      const selectedWasDeleted = state.selectedReport?.meta.id !== undefined
        && state.selectedReport.meta.reportType === 'market_review'
        && deletedIds.has(state.selectedReport.meta.id);

      set({ selectedMarketReviewHistoryIds: [] });

      const freshPage = await fetchMarketReviewHistory(get, set, { reset: true });

      if (selectedWasDeleted) {
        const nextItem = freshPage?.items?.[0];
        if (nextItem) {
          await get().selectHistoryItem(nextItem.id, false);
        } else {
          set({ selectedReport: null });
        }
      }
    } catch (error) {
      set({ error: getParsedApiError(error), errorScope: null });
    } finally {
      set({ isDeletingMarketReviewHistory: false });
    }
  },

  submitAnalysis: async (options) => {
    const state = get();
    const rawStockCode = options?.stockCode ?? state.query;
    const stockCodeInput = rawStockCode.trim();
    const stockName = options?.stockName;
    const selectionSource = options?.selectionSource ?? state.selectionSource;
    const originalQuery = (options?.originalQuery ?? state.query).trim();
    const notify = options?.notify ?? state.notify;
    const forceRefresh = options?.forceRefresh ?? false;
    const skills = options?.skills;

    if (!stockCodeInput) {
      set({ inputError: '请输入股票代码', duplicateError: null });
      return;
    }

    if (selectionSource !== 'autocomplete' && isObviouslyInvalidStockQuery(stockCodeInput)) {
      set({ inputError: '请输入有效的股票代码或股票名称', duplicateError: null });
      return;
    }

    let normalizedStockCode = stockCodeInput;
    if (selectionSource === 'autocomplete' || looksLikeStockCode(stockCodeInput)) {
      const { valid, message, normalized } = validateStockCode(stockCodeInput);
      if (!valid) {
        set({ inputError: message, duplicateError: null });
        return;
      }
      normalizedStockCode = normalized;
    }

    set({
      inputError: undefined,
      duplicateError: null,
      error: null,
      errorScope: null,
      isAnalyzing: true,
    });

    const requestId = ++analyzeRequestSeq;
    try {
      await analysisApi.analyzeAsync({
        stockCode: normalizedStockCode,
        reportType: 'detailed',
        stockName,
        originalQuery: originalQuery || stockCodeInput,
        selectionSource,
        notify,
        forceRefresh,
        skills,
        ...(options?.reportLanguage !== undefined && { reportLanguage: options.reportLanguage }),
      });

      if (requestId !== analyzeRequestSeq) {
        return;
      }

      set({
        query: '',
        selectionSource: 'manual',
      });
    } catch (error) {
      if (requestId !== analyzeRequestSeq) {
        return;
      }

      if (error instanceof DuplicateTaskError) {
        set({
          duplicateError: `股票 ${error.stockCode} 正在分析中，请等待完成`,
        });
        return;
      }

      set({ error: getParsedApiError(error), errorScope: null });
    } finally {
      if (requestId === analyzeRequestSeq) {
        set({ isAnalyzing: false });
      }
    }
  },

  syncTaskCreated: (task) => {
    if (dismissedTaskIds.has(task.taskId)) {
      return;
    }
    if (get().activeTasks.some((item) => item.taskId === task.taskId)) {
      return;
    }
    activeTaskLocalRevision += 1;
    set({ activeTasks: [...get().activeTasks, task] });
  },

  syncTaskUpdated: (task) => {
    if (dismissedTaskIds.has(task.taskId)) {
      return;
    }
    const nextTasks = [...get().activeTasks];
    const index = nextTasks.findIndex((item) => item.taskId === task.taskId);
    if (index >= 0) {
      nextTasks[index] = task;
      activeTaskLocalRevision += 1;
      set({ activeTasks: nextTasks });
    }
  },

  syncTaskFailed: (task) => {
    get().syncTaskUpdated(task);
    set({ error: getParsedApiError(task.error || '分析失败'), errorScope: null });
  },

  refreshActiveTasks: async () => {
    const requestId = ++activeTaskRequestSeq;
    const localRevisionAtRequest = activeTaskLocalRevision;
    try {
      const response = await analysisApi.getTasks({
        status: 'pending,processing,cancel_requested',
        limit: 100,
      });
      if (requestId !== activeTaskRequestSeq) {
        return;
      }

      const remoteTasks = response.tasks.filter(
        (task) => !dismissedTaskIds.has(task.taskId),
      );
      const remoteTaskIds = new Set(remoteTasks.map((task) => task.taskId));
      const remoteTaskById = new Map(remoteTasks.map((task) => [task.taskId, task]));
      const activeTaskCount = response.pending
        + response.processing
        + response.tasks.filter((task) => task.status === 'cancel_requested').length;
      const isCompleteSnapshot = response.tasks.length === activeTaskCount;
      const canPruneLocalTasks = isCompleteSnapshot && activeTaskLocalRevision === localRevisionAtRequest;

      const currentTasks = get().activeTasks;
      const nextTasks = currentTasks
        .filter((task) => !dismissedTaskIds.has(task.taskId))
        .filter((task) => !canPruneLocalTasks || remoteTaskIds.has(task.taskId))
        .map((task) => remoteTaskById.get(task.taskId) ?? task);

      const localTaskIds = new Set(nextTasks.map((task) => task.taskId));
      for (const task of remoteTasks) {
        if (!localTaskIds.has(task.taskId)) {
          nextTasks.push(task);
        }
      }

      const hasActiveTaskChanges = nextTasks.length !== currentTasks.length
        || nextTasks.some((task, index) => task !== currentTasks[index]);
      if (hasActiveTaskChanges) {
        activeTaskLocalRevision += 1;
        set({ activeTasks: nextTasks });
      }
    } catch {
      // Keep the current task panel when reconciliation cannot reach the API.
    }
  },

  removeTask: (taskId) => {
    dismissedTaskIds.add(taskId);
    const currentTasks = get().activeTasks;
    const nextTasks = currentTasks.filter((task) => task.taskId !== taskId);
    if (nextTasks.length !== currentTasks.length) {
      activeTaskLocalRevision += 1;
    }
    set({ activeTasks: nextTasks });
  },

  resetDashboardState: () => {
    historyRequestSeq += 1;
    marketReviewHistoryRequestSeq += 1;
    stockHistoryRequestSeq += 1;
    reportRequestSeq = 0;
    analyzeRequestSeq = 0;
    manualSelectionRequestSeq = 0;
    manualSelectionRequestId = 0;
    stockBarRequestSeq += 1;
    activeTaskRequestSeq += 1;
    activeTaskLocalRevision += 1;
    dismissedTaskIds.clear();
    pendingCompletedTaskSelectionKeys.clear();
    set({ ...initialState });
  },

  loadStockBar: async () => {
    const state = get();
    if (state.isLoadingStockBar) return;
    const requestSeq = ++stockBarRequestSeq;
    set({ isLoadingStockBar: true });
    try {
      const response = await historyApi.getStockBarList({
        startDate: getRecentStartDate(90),
        endDate: getTodayInShanghai(),
      });
      if (requestSeq !== stockBarRequestSeq) {
        return;
      }
      set({ stockBarItems: response.items, stockBarRefreshFailed: false });
    } catch {
      if (requestSeq !== stockBarRequestSeq) {
        return;
      }
      set({ stockBarRefreshFailed: true });
    } finally {
      if (requestSeq === stockBarRequestSeq) {
        set({ isLoadingStockBar: false });
      }
    }
  },

  refreshStockBar: async (silent = false) => {
    const requestSeq = ++stockBarRequestSeq;
    // 后台刷新（30s 定时 / 回到前台 / 任务完成）不置 isLoadingStockBar。
    // HomePage 把该标志当作「单行今日状态未知」使用，每 30s 置一次会让所有自选行的
    // 今日覆盖瞬间归零、行内出现 spinner、点行只弹「最新详情加载中」而不打开报告——
    // 即常驻性的「点了没反应」。silent 失败仍会置 stockBarRefreshFailed（轻提示）。
    if (!silent) {
      set({ isLoadingStockBar: true });
    }
    try {
      const response = await historyApi.getStockBarList({
        startDate: getRecentStartDate(90),
        endDate: getTodayInShanghai(),
      });
      if (requestSeq !== stockBarRequestSeq) {
        return;
      }
      set({ stockBarItems: response.items, stockBarRefreshFailed: false });
    } catch {
      if (requestSeq !== stockBarRequestSeq) {
        return;
      }
      set({ stockBarRefreshFailed: true });
    } finally {
      // 仍然无条件清（而不是只在 !silent 时清）：如果本次 silent 刷新顶掉了上一次
      // 可见加载，那次加载的 finally 会因 seq 失配而跳过，只有这里能把它置回 false，
      // 否则 isLoadingStockBar 会永久卡在 true。silent 自己没置过 true，清一次是空操作。
      if (requestSeq === stockBarRequestSeq) {
        set({ isLoadingStockBar: false });
      }
    }
  },
}));
