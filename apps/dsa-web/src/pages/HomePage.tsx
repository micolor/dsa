import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { BarChart3, Check, SlidersHorizontal, X } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { analysisApi, DuplicateTaskError } from '../api/analysis';
import { historyApi } from '../api/history';
import { agentApi, type SkillInfo } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import { Button, Drawer, InlineAlert } from '../components/common';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { ReportMarkdownDrawer } from '../components/report/ReportMarkdownDrawer';
import { HomeReportRegion, type MarketReviewNotice } from '../components/report/HomeReportRegion';
import { MarketReviewRegionSelector } from '../components/market-review/MarketReviewRegionSelector';
import { RunFlowPanel } from '../components/run-flow';
import {
  HomeStockWorkspace,
  type HomeWatchlistRow,
  type HomeWorkspaceTab,
  type WatchlistAnalyzeMode,
} from '../components/watchlist/HomeStockWorkspace';
import { useDashboardLifecycle, useHomeDashboardState } from '../hooks';
import { useWatchlist } from '../hooks/useWatchlist';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { SetupStatusResponse } from '../types/systemConfig';
import { normalizeReportLanguage } from '../utils/reportLanguage';
import type {
  AnalyzeAsyncResponse,
  HistoryItem,
  MarketReviewPayload,
  MarketReviewRegion,
  StockBarItem,
  TaskInfo,
} from '../types/analysis';
import type { RunFlowSnapshotSource } from '../types/runFlow';
import { getTodayInShanghai } from '../utils/format';
import { normalizeStockCode } from '../utils/stockCode';

type RunFlowDrawerState =
  | { open: false }
  | { open: true; source: RunFlowSnapshotSource; title: string };

type StockAnalysisNavigationState = {
  stockCode?: string;
  stockName?: string;
  autoAnalyze?: boolean;
  selectionSource?: string;
  skills?: string[];
};

const DUPLICATE_BANNER_AUTO_DISMISS_MS = 5000;
const BATCH_ANALYSIS_CHUNK_SIZE = 50;
const TODAY_ANALYSIS_PAGE_SIZE = 100;
const WATCHLIST_HISTORY_LOOKUP_CONCURRENCY = 4;
const SERVER_LOCAL_DATE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;
const MARKET_REVIEW_POLL_MAX_ATTEMPTS = 120;
const MARKET_REVIEW_POLL_INTERVAL_MS = 2000;

type BatchAnalyzeStatus = {
  variant: 'success' | 'warning' | 'danger';
  message: string;
} | null;

type WatchlistHistoryLookupState = {
  signature: string;
  settledKeys: Set<string>;
  failedKeys: Set<string>;
};

type WatchlistHistoryLookupResult = {
  code: string;
  item: HistoryItem | null;
  failed: boolean;
};

async function lookupWatchlistHistory(
  codes: string[],
  isCanceled: () => boolean,
  signal: AbortSignal,
): Promise<WatchlistHistoryLookupResult[]> {
  const results: Array<WatchlistHistoryLookupResult | undefined> = new Array(codes.length);
  let nextIndex = 0;

  const runWorker = async () => {
    while (!isCanceled()) {
      const index = nextIndex;
      if (index >= codes.length) {
        return;
      }
      nextIndex += 1;
      const code = codes[index];
      try {
        const response = await historyApi.getList(
          { stockCode: code, limit: 1 },
          { signal },
        );
        results[index] = { code, item: response.items[0] ?? null, failed: false };
      } catch {
        results[index] = { code, item: null, failed: true };
      }
    }
  };

  const workerCount = Math.min(WATCHLIST_HISTORY_LOOKUP_CONCURRENCY, codes.length);
  await Promise.all(Array.from({ length: workerCount }, () => runWorker()));
  return results.filter((entry): entry is WatchlistHistoryLookupResult => entry !== undefined);
}

function getShanghaiDateKey(value?: string | null): string {
  if (!value) return '';
  const trimmed = value.trim();
  const normalized = SERVER_LOCAL_DATE_TIME_PATTERN.test(trimmed)
    ? `${trimmed.replace(' ', 'T')}+08:00`
    : trimmed;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(date);
}

function getShanghaiTimeValue(value?: string | null): number {
  if (!value) return 0;
  const trimmed = value.trim();
  const normalized = SERVER_LOCAL_DATE_TIME_PATTERN.test(trimmed)
    ? `${trimmed.replace(' ', 'T')}+08:00`
    : trimmed;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
}

function shiftDateKey(dateKey: string, days: number): string {
  const date = new Date(`${dateKey}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function getStockCodeKey(code?: string | null): string {
  const trimmed = (code ?? '').trim();
  return trimmed ? normalizeStockCode(trimmed).toUpperCase() : '';
}

function chunkStockCodes(codes: string[]): string[][] {
  const chunks: string[][] = [];
  for (let index = 0; index < codes.length; index += BATCH_ANALYSIS_CHUNK_SIZE) {
    chunks.push(codes.slice(index, index + BATCH_ANALYSIS_CHUNK_SIZE));
  }
  return chunks;
}

function countBatchAccepted(result: AnalyzeAsyncResponse): { accepted: number; duplicates: number } {
  if ('accepted' in result) {
    return {
      accepted: result.accepted.length,
      duplicates: result.duplicates.length,
    };
  }
  return { accepted: 1, duplicates: 0 };
}

function toStockBarItemFromHistoryItem(item: HistoryItem): StockBarItem {
  return {
    id: item.id,
    stockCode: item.stockCode,
    stockName: item.stockName,
    reportType: item.reportType,
    sentimentScore: item.sentimentScore,
    operationAdvice: item.operationAdvice,
    action: item.action ?? null,
    actionLabel: item.actionLabel ?? null,
    analysisCount: 0,
    lastAnalysisTime: item.createdAt,
    modelUsed: item.modelUsed,
    marketPhaseSummary: item.marketPhaseSummary ?? null,
  };
}

async function getTodayAnalysisItems(dateKey: string): Promise<StockBarItem[]> {
  const items: StockBarItem[] = [];
  let loadedRecordCount = 0;
  let page = 1;

  while (true) {
    const response = await historyApi.getList({
      // History dates are filtered in the server's local timezone. Query the
      // adjacent dates too, then apply the exact Shanghai-day filter below.
      startDate: shiftDateKey(dateKey, -1),
      endDate: shiftDateKey(dateKey, 1),
      page,
      limit: TODAY_ANALYSIS_PAGE_SIZE,
    });

    loadedRecordCount += response.items.length;
    for (const item of response.items) {
      if (item.stockCode === 'MARKET' || item.reportType === 'market_review') {
        continue;
      }
      items.push(toStockBarItemFromHistoryItem(item));
    }

    if (
      response.items.length === 0
      || response.items.length < TODAY_ANALYSIS_PAGE_SIZE
      || loadedRecordCount >= response.total
    ) {
      break;
    }

    page += 1;
  }

  return items;
}

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { language: uiLanguage, t } = useUiLanguage();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isSubmittingMarketReview, setIsSubmittingMarketReview] = useState(false);
  const [marketReviewNotice, setMarketReviewNotice] = useState<MarketReviewNotice>(null);
  const marketReviewNoticeRef = useRef<MarketReviewNotice>(null);
  // 统一入口：所有对 notice 的写入都要同步 ref，避免 ref 与 state 脱节，
  // 导致轮询时的 unchanged 短路基于陈旧 ref 误判、漏掉一次合法更新。
  const updateMarketReviewNotice = useCallback((notice: MarketReviewNotice) => {
    marketReviewNoticeRef.current = notice;
    setMarketReviewNotice(notice);
  }, []);
  const [marketReviewError, setMarketReviewError] = useState<ParsedApiError | null>(null);
  const [marketReviewReport, setMarketReviewReport] = useState<string | null>(null);
  const [marketReviewPayload, setMarketReviewPayload] = useState<MarketReviewPayload | null>(null);
  const [marketReviewRegionOverride, setMarketReviewRegionOverride] = useState<MarketReviewRegion[] | undefined>();
  const [analysisSkills, setAnalysisSkills] = useState<SkillInfo[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState('');
  const [strategyMenuOpen, setStrategyMenuOpen] = useState(false);
  const [runFlowDrawer, setRunFlowDrawer] = useState<RunFlowDrawerState>({ open: false });
  const [duplicateBannerVisible, setDuplicateBannerVisible] = useState(false);
  const [sidebarWorkspaceTab, setSidebarWorkspaceTab] = useState<HomeWorkspaceTab>('history');
  const [isBatchAnalyzingWatchlist, setIsBatchAnalyzingWatchlist] = useState(false);
  const [batchAnalyzeStatus, setBatchAnalyzeStatus] = useState<BatchAnalyzeStatus>(null);
  const [watchlistHistoryItemsByCode, setWatchlistHistoryItemsByCode] = useState<Map<string, StockBarItem>>(new Map());
  const [watchlistHistoryLookupState, setWatchlistHistoryLookupState] = useState<WatchlistHistoryLookupState>({
    signature: '',
    settledKeys: new Set(),
    failedKeys: new Set(),
  });
  const [watchlistHistoryRetryVersion, setWatchlistHistoryRetryVersion] = useState(0);
  const [todayHistoryItems, setTodayHistoryItems] = useState<StockBarItem[]>([]);
  const [isLoadingTodayAnalysisItems, setIsLoadingTodayAnalysisItems] = useState(false);
  const [todayAnalysisLoadFailed, setTodayAnalysisLoadFailed] = useState(false);
  const [todayAnalysisRefreshVersion, setTodayAnalysisRefreshVersion] = useState(0);
  const [isStockBarInitialLoadSettled, setIsStockBarInitialLoadSettled] = useState(false);
  const [completedTaskRefreshPendingCounts, setCompletedTaskRefreshPendingCounts] = useState<Map<string, number>>(
    new Map(),
  );
  const duplicateBannerTimer = useRef<number | null>(null);
  const marketReviewPollTimer = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const stockBarLoadStartedRef = useRef(false);
  const dashboardScrollRef = useRef<HTMLElement | null>(null);
  const strategyMenuRef = useRef<HTMLDivElement | null>(null);
  const strategyButtonRef = useRef<HTMLButtonElement | null>(null);
  const strategyItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const strategyInitialFocusIndexRef = useRef<number | null>(null);

  const stopMarketReviewPolling = useCallback(() => {
    if (marketReviewPollTimer.current !== null) {
      window.clearInterval(marketReviewPollTimer.current);
      marketReviewPollTimer.current = null;
    }
  }, []);

  const scrollMarketReviewFeedbackIntoView = useCallback(() => {
    const scrollContainer = dashboardScrollRef.current;
    if (!scrollContainer) {
      return;
    }

    if (typeof scrollContainer.scrollTo === 'function') {
      scrollContainer.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    scrollContainer.scrollTop = 0;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  useEffect(() => stopMarketReviewPolling, [stopMarketReviewPolling]);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);

  const {
    query,
    inputError,
    duplicateError,
    error,
    isAnalyzing,
    selectedReport,
    isLoadingReport,
    isHistoryTrendOpen,
    marketReviewHistoryItems,
    stockHistoryItems,
    stockHistoryTotal,
    stockHistoryHasMore,
    isLoadingStockHistory,
    isLoadingMoreStockHistory,
    stockHistoryError,
    stockHistoryFilters,
    markdownDrawerOpen,
    setQuery,
    clearError,
    loadInitialHistory,
    refreshHistory,
    refreshHistoryForCompletedTask,
    loadMarketReviewHistory,
    refreshMarketReviewHistory,
    selectHistoryItem,
    submitAnalysis,
    notify,
    setNotify,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    refreshActiveTasks,
    removeTask,
    openMarkdownDrawer,
    closeMarkdownDrawer,
    openHistoryTrend,
    closeHistoryTrend,
    setStockHistoryRange,
    loadMoreStockHistory,
    stockBarItems,
    isLoadingStockBar,
    stockBarRefreshFailed,
    loadStockBar,
    refreshStockBar,
  } = useHomeDashboardState();

  const clearDuplicateBannerTimer = useCallback(() => {
    if (duplicateBannerTimer.current !== null) {
      window.clearTimeout(duplicateBannerTimer.current);
      duplicateBannerTimer.current = null;
    }
  }, []);

  const dismissDuplicateBanner = useCallback(() => {
    clearDuplicateBannerTimer();
    setDuplicateBannerVisible(false);
  }, [clearDuplicateBannerTimer]);

  useEffect(() => {
    if (!duplicateError) {
      clearDuplicateBannerTimer();
      setDuplicateBannerVisible(false);
      return undefined;
    }

    setDuplicateBannerVisible(true);
    clearDuplicateBannerTimer();
    duplicateBannerTimer.current = window.setTimeout(() => {
      duplicateBannerTimer.current = null;
      setDuplicateBannerVisible(false);
    }, DUPLICATE_BANNER_AUTO_DISMISS_MS);

    return clearDuplicateBannerTimer;
  }, [clearDuplicateBannerTimer, duplicateError]);

  useEffect(() => {
    document.title = t('home.pageTitle');
  }, [t]);

  useEffect(() => {
    let active = true;
    systemConfigApi.getSetupStatus()
      .then((status) => {
        if (active) {
          setSetupStatus(status);
        }
      })
      .catch(() => {
        if (active) {
          setSetupStatus(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    agentApi.getSkills()
      .then((response) => {
        if (active) {
          setAnalysisSkills(response.skills);
        }
      })
      .catch(() => {
        if (active) {
          setAnalysisSkills([]);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!strategyMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && strategyMenuRef.current?.contains(target)) {
        return;
      }
      setStrategyMenuOpen(false);
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [strategyMenuOpen]);

  useEffect(() => {
    if (selectedStrategyId && !analysisSkills.some((skill) => skill.id === selectedStrategyId)) {
      setSelectedStrategyId('');
    }
  }, [analysisSkills, selectedStrategyId]);

  const reportLanguage = normalizeReportLanguage(selectedReport?.meta.reportLanguage);
  const isHistoryTrendUnavailable = !selectedReport || !selectedReport.meta.stockCode;

  useEffect(() => {
    if (!isHistoryTrendUnavailable || !isHistoryTrendOpen) {
      return;
    }
    closeHistoryTrend();
  }, [closeHistoryTrend, isHistoryTrendOpen, isHistoryTrendUnavailable]);

  const selectedStrategy = useMemo(
    () => analysisSkills.find((skill) => skill.id === selectedStrategyId),
    [analysisSkills, selectedStrategyId],
  );
  const selectedAnalysisSkills = useMemo(
    () => (selectedStrategyId ? [selectedStrategyId] : undefined),
    [selectedStrategyId],
  );
  const strategyOptions = useMemo(
    () => [
      { id: '', name: t('home.defaultStrategyName'), description: t('home.defaultStrategyDescription') },
      ...analysisSkills.map((skill) => ({
        id: skill.id,
        name: skill.name,
        description: skill.description,
      })),
    ],
    [analysisSkills, t],
  );
  const closeStrategyMenu = useCallback((restoreFocus = false) => {
    setStrategyMenuOpen(false);
    if (restoreFocus) {
      strategyButtonRef.current?.focus();
    }
  }, []);
  const selectStrategy = useCallback((strategyId: string) => {
    setSelectedStrategyId(strategyId);
    setStrategyMenuOpen(false);
  }, []);
  const focusStrategyItem = useCallback((index: number) => {
    const itemCount = strategyOptions.length;
    if (itemCount === 0) {
      return;
    }
    const nextIndex = (index + itemCount) % itemCount;
    strategyItemRefs.current[nextIndex]?.focus();
  }, [strategyOptions.length]);
  const getSelectedStrategyIndex = useCallback(() => {
    const selectedIndex = strategyOptions.findIndex((option) => option.id === selectedStrategyId);
    return selectedIndex >= 0 ? selectedIndex : 0;
  }, [selectedStrategyId, strategyOptions]);
  useEffect(() => {
    strategyItemRefs.current = strategyItemRefs.current.slice(0, strategyOptions.length);
  }, [strategyOptions.length]);
  useEffect(() => {
    if (!strategyMenuOpen) {
      return undefined;
    }

    const targetIndex = strategyInitialFocusIndexRef.current ?? getSelectedStrategyIndex();
    strategyInitialFocusIndexRef.current = null;
    const timeout = window.setTimeout(() => focusStrategyItem(targetIndex), 0);
    return () => window.clearTimeout(timeout);
  }, [focusStrategyItem, getSelectedStrategyIndex, strategyMenuOpen]);
  const handleStrategyButtonKeyDown = useCallback((event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
      return;
    }

    event.preventDefault();
    const targetIndex = event.key === 'ArrowUp' ? strategyOptions.length - 1 : 0;
    if (strategyMenuOpen) {
      focusStrategyItem(targetIndex);
      return;
    }
    strategyInitialFocusIndexRef.current = targetIndex;
    setStrategyMenuOpen(true);
  }, [focusStrategyItem, strategyMenuOpen, strategyOptions.length]);
  const handleStrategyMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const itemCount = strategyOptions.length;
    if (itemCount === 0) {
      return;
    }

    const currentIndex = strategyItemRefs.current.findIndex((item) => item === document.activeElement);
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        closeStrategyMenu(true);
        break;
      case 'ArrowDown':
        event.preventDefault();
        focusStrategyItem(currentIndex >= 0 ? currentIndex + 1 : 0);
        break;
      case 'ArrowUp':
        event.preventDefault();
        focusStrategyItem(currentIndex >= 0 ? currentIndex - 1 : itemCount - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusStrategyItem(0);
        break;
      case 'End':
        event.preventDefault();
        focusStrategyItem(itemCount - 1);
        break;
      case 'Tab':
        setStrategyMenuOpen(false);
        break;
      default:
        break;
    }
  }, [closeStrategyMenu, focusStrategyItem, strategyOptions.length]);
  const setupNeedsAction = setupStatus ? !setupStatus.isComplete : false;
  const setupMissingLabels = useMemo(() => {
    if (!setupStatus) {
      return '';
    }
    const requiredNeedsAction = setupStatus.checks
      .filter((check) => check.required && check.status === 'needs_action')
      .map((check) => check.title);
    return requiredNeedsAction.slice(0, 3).join(uiLanguage === 'en' ? ', ' : '、');
  }, [setupStatus, uiLanguage]);

  const handleCompletedTaskDataRefreshStarted = useCallback((task: TaskInfo) => {
    if (task.reportType === 'market_review') {
      return;
    }
    const key = getStockCodeKey(task.stockCode);
    if (!key) {
      return;
    }
    setCompletedTaskRefreshPendingCounts((current) => {
      const next = new Map(current);
      next.set(key, (next.get(key) ?? 0) + 1);
      return next;
    });
  }, []);

  const handleCompletedTaskDataRefreshed = useCallback((task: TaskInfo) => {
    if (task.reportType === 'market_review') {
      return;
    }
    const key = getStockCodeKey(task.stockCode);
    if (key) {
      setCompletedTaskRefreshPendingCounts((current) => {
        const pendingCount = current.get(key) ?? 0;
        if (pendingCount === 0) {
          return current;
        }
        const next = new Map(current);
        if (pendingCount === 1) {
          next.delete(key);
        } else {
          next.set(key, pendingCount - 1);
        }
        return next;
      });
    }
    setTodayAnalysisRefreshVersion((version) => version + 1);
  }, []);

  const handleDashboardDataRefresh = useCallback(() => {
    setTodayAnalysisRefreshVersion((version) => version + 1);
  }, []);

  useDashboardLifecycle({
    loadInitialHistory,
    refreshHistory,
    refreshHistoryForCompletedTask,
    loadMarketReviewHistory,
    refreshMarketReviewHistory,
    loadStockBar,
    refreshStockBar,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    refreshActiveTasks,
    removeTask,
    onDashboardDataRefresh: handleDashboardDataRefresh,
    onCompletedTaskDataRefreshStarted: handleCompletedTaskDataRefreshStarted,
    onCompletedTaskDataRefreshed: handleCompletedTaskDataRefreshed,
  });

  useEffect(() => {
    if (isLoadingStockBar) {
      stockBarLoadStartedRef.current = true;
      return;
    }
    if (stockBarLoadStartedRef.current || stockBarItems.length > 0) {
      setIsStockBarInitialLoadSettled(true);
    }
  }, [isLoadingStockBar, stockBarItems.length]);

  const watchlistState = useWatchlist();
  const refreshWatchlist = watchlistState.refresh;
  const watchlistCodesByNormalized = useMemo(() => {
    const codesByNormalized = new Map<string, string>();
    for (const code of watchlistState.watchlistCodes) {
      const key = getStockCodeKey(code);
      if (!key || key === 'MARKET' || codesByNormalized.has(key)) {
        continue;
      }
      codesByNormalized.set(key, code);
    }
    return Array.from(codesByNormalized.entries());
  }, [watchlistState.watchlistCodes]);

  const stockBarItemByCode = useMemo(() => {
    const itemsByCode = new Map<string, StockBarItem>();
    for (const item of stockBarItems) {
      if (item.stockCode === 'MARKET') {
        continue;
      }
      const key = getStockCodeKey(item.stockCode);
      if (key) {
        itemsByCode.set(key, item);
      }
    }
    return itemsByCode;
  }, [stockBarItems]);

  const canLookupWatchlistHistory = !isLoadingStockBar && isStockBarInitialLoadSettled;

  const watchlistMissingHistoryEntries = useMemo(
    () => (
      canLookupWatchlistHistory
        ? watchlistCodesByNormalized.filter(([key]) => !stockBarItemByCode.has(key))
        : []
    ),
    [canLookupWatchlistHistory, stockBarItemByCode, watchlistCodesByNormalized],
  );

  const watchlistMissingHistorySignature = useMemo(
    () => watchlistMissingHistoryEntries.map(([key]) => key).join('\n'),
    [watchlistMissingHistoryEntries],
  );

  useEffect(() => {
    if (!canLookupWatchlistHistory) {
      setWatchlistHistoryItemsByCode(new Map());
      setWatchlistHistoryLookupState({ signature: '', settledKeys: new Set(), failedKeys: new Set() });
      return undefined;
    }

    const missingCodes = watchlistMissingHistoryEntries.map(([, code]) => code);
    const missingKeys = watchlistMissingHistoryEntries.map(([key]) => key);
    const currentSignature = watchlistMissingHistorySignature;

    if (missingCodes.length === 0) {
      setWatchlistHistoryItemsByCode(new Map());
      setWatchlistHistoryLookupState({ signature: '', settledKeys: new Set(), failedKeys: new Set() });
      return;
    }

    let isCanceled = false;
    const abortController = new AbortController();
    setWatchlistHistoryLookupState({ signature: currentSignature, settledKeys: new Set(), failedKeys: new Set() });
    void (async () => {
      try {
        const results = await lookupWatchlistHistory(
          missingCodes,
          () => isCanceled,
          abortController.signal,
        );

        if (isCanceled) {
          return;
        }

        const next = new Map<string, StockBarItem>();
        const failedKeys = new Set<string>();
        for (const entry of results) {
          const key = getStockCodeKey(entry.code);
          if (!key) {
            continue;
          }
          if (entry.failed) {
            failedKeys.add(key);
            continue;
          }
          if (entry.item) {
            next.set(key, toStockBarItemFromHistoryItem(entry.item));
          }
        }
        setWatchlistHistoryItemsByCode(next);
        setWatchlistHistoryLookupState({
          signature: currentSignature,
          settledKeys: new Set(missingKeys),
          failedKeys,
        });
      } catch {
        if (!isCanceled) {
          setWatchlistHistoryItemsByCode(new Map());
          setWatchlistHistoryLookupState({
            signature: currentSignature,
            settledKeys: new Set(missingKeys),
            failedKeys: new Set(missingKeys),
          });
        }
      }
    })();

    return () => {
      isCanceled = true;
      abortController.abort();
    };
  }, [canLookupWatchlistHistory, watchlistHistoryRetryVersion, watchlistMissingHistoryEntries, watchlistMissingHistorySignature]);

  const clearMarketReviewState = useCallback(() => {
    stopMarketReviewPolling();
    setMarketReviewReport(null);
    setMarketReviewPayload(null);
    updateMarketReviewNotice(null);
    setMarketReviewError(null);
  }, [stopMarketReviewPolling, updateMarketReviewNotice]);

  const dismissMarketReviewError = useCallback(() => {
    setMarketReviewError(null);
  }, []);

  const toggleHistoryTrend = useCallback(() => {
    if (isHistoryTrendOpen) {
      closeHistoryTrend();
      return;
    }
    void openHistoryTrend();
  }, [closeHistoryTrend, isHistoryTrendOpen, openHistoryTrend]);

  const handleHistoryItemClick = useCallback((recordId: number) => {
    clearMarketReviewState();
    void selectHistoryItem(recordId);
    setSidebarOpen(false);
  }, [clearMarketReviewState, selectHistoryItem]);

  const handleRefreshWatchlist = useCallback(async () => {
    await Promise.all([
      refreshWatchlist(),
      refreshStockBar(),
    ]);
    setWatchlistHistoryRetryVersion((version) => version + 1);
  }, [refreshStockBar, refreshWatchlist]);

  const [isDeletingStock, setIsDeletingStock] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const handleDeleteStock = useCallback(async (stockCode: string) => {
    if (isDeletingStock) return;
    setIsDeletingStock(true);
    setDeleteError(null);
    try {
      await historyApi.deleteByCode(stockCode);
      await refreshStockBar();
      await refreshHistory(true);
      if (stockCode === 'MARKET') {
        await refreshMarketReviewHistory(false);
      }
    } catch (err) {
      setDeleteError(getParsedApiError(err).message);
    } finally {
      setIsDeletingStock(false);
    }
  }, [isDeletingStock, refreshMarketReviewHistory, refreshStockBar, refreshHistory]);

  const handleSubmitAnalysis = useCallback(
    (
      stockCode?: string,
      stockName?: string,
      selectionSource?: 'manual' | 'autocomplete' | 'import' | 'image',
      analysisSkills?: string[],
      originalQueryOverride?: string,
    ) => {
      void submitAnalysis({
        stockCode,
        stockName,
        // 默认用当前 query 作为原始输入；导入/自动分析等场景 query 尚未更新（setQuery 异步），
        // 需显式传入真实来源，避免提交空字符串作为 originalQuery。
        originalQuery: originalQueryOverride ?? query,
        selectionSource: selectionSource ?? 'manual',
        skills: analysisSkills ?? selectedAnalysisSkills,
      });
    },
    [query, selectedAnalysisSkills, submitAnalysis],
  );

  // 稳定引用，配合 StockAutocomplete 的 memo 让组件在父重渲染时跳过。
  const handleAutocompleteSubmit = useCallback(
    (stockCode: string, stockName?: string, selectionSource?: 'manual' | 'autocomplete' | 'import' | 'image') => {
      handleSubmitAnalysis(stockCode, stockName, selectionSource);
    },
    [handleSubmitAnalysis],
  );

  useEffect(() => {
    const state = location.state as StockAnalysisNavigationState | null;
    const stockCode = typeof state?.stockCode === 'string' ? state.stockCode.trim() : '';
    if (!stockCode) {
      return;
    }
    const stockName = typeof state?.stockName === 'string' ? state.stockName.trim() : '';
    setQuery(stockCode);
    navigate(location.pathname, { replace: true, state: null });
    if (state?.autoAnalyze) {
      handleSubmitAnalysis(stockCode, stockName || undefined, 'import', state.skills, stockCode);
    }
  }, [handleSubmitAnalysis, location.pathname, location.state, navigate, setQuery]);

  const handleAskFollowUp = useCallback(() => {
    if (selectedReport?.meta.id === undefined || selectedReport.meta.reportType === 'market_review') {
      return;
    }

    const code = selectedReport.meta.stockCode;
    const name = selectedReport.meta.stockName;
    const rid = selectedReport.meta.id;
    navigate(`/chat?stock=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}&recordId=${rid}`);
  }, [navigate, selectedReport]);

  const handleReanalyze = useCallback(() => {
    if (!selectedReport || selectedReport.meta.reportType === 'market_review') {
      return;
    }

    void submitAnalysis({
      stockCode: selectedReport.meta.stockCode,
      stockName: selectedReport.meta.stockName,
      originalQuery: selectedReport.meta.stockCode,
      selectionSource: 'manual',
      forceRefresh: true,
      skills: selectedAnalysisSkills,
    });
  }, [selectedAnalysisSkills, selectedReport, submitAnalysis]);

  const openHistoryRunFlow = useCallback((recordId: number) => {
    const meta = selectedReport?.meta.id === recordId ? selectedReport.meta : null;
    const stock = meta?.stockName || meta?.stockCode || String(recordId);
    setRunFlowDrawer({
      open: true,
      source: { type: 'history', recordId },
      title: t('runFlow.historyDrawerTitle', { stock }),
    });
  }, [selectedReport, t]);

  const closeRunFlowDrawer = useCallback(() => {
    setRunFlowDrawer({ open: false });
  }, []);

  // 任务浮窗跳转：打开对应任务的运行流抽屉。
  // 依赖 location.search，这样从任意页面（包括首页自身）跳转都会重新触发，
  // 而不仅是首页首次挂载时。
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const taskId = params.get('runFlowTaskId');
    if (!taskId) {
      return;
    }
    const source: RunFlowSnapshotSource = { type: 'task', taskId };
    setRunFlowDrawer({
      open: true,
      source,
      title: t('runFlow.taskDrawerTitle', { stock: taskId }),
    });
    // 清除 query，避免刷新后重复打开；用 navigate 而非裸 replaceState，
    // 让 router 状态同步，关闭抽屉后再点同一任务仍能再次触发。
    navigate(location.pathname, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search, navigate]);

  const pollMarketReviewStatus = useCallback(
    async (taskId: string) => {
      stopMarketReviewPolling();

      const maxAttempts = MARKET_REVIEW_POLL_MAX_ATTEMPTS;
      const intervalMs = MARKET_REVIEW_POLL_INTERVAL_MS;
      let attempts = 0;

      // 轮询每 2s 重建 notice 对象；内容未变化时跳过 setState，避免无谓的整页重渲染。
      const applyMarketReviewNotice = (notice: MarketReviewNotice) => {
        const prev = marketReviewNoticeRef.current;
        const unchanged = prev != null && notice != null
          && prev.variant === notice.variant && prev.title === notice.title && prev.message === notice.message;
        if (unchanged) {
          return;
        }
        marketReviewNoticeRef.current = notice;
        setMarketReviewNotice(notice);
      };

      const poll = async (): Promise<boolean> => {
        if (attempts >= maxAttempts) {
          stopMarketReviewPolling();
          setMarketReviewReport(null);
          setMarketReviewPayload(null);
          applyMarketReviewNotice({
            variant: 'danger',
            title: t('home.marketReviewTimeout'),
            message: t('home.marketReviewTimeoutMessage'),
          });
          scrollMarketReviewFeedbackIntoView();
          return false;
        }

        attempts += 1;

        try {
          const status = await analysisApi.getStatus(taskId);
          if (!mountedRef.current) {
            return false;
          }
          if (status.status === 'pending' || status.status === 'processing') {
            setMarketReviewReport(null);
            setMarketReviewPayload(null);
            const progress = typeof status.progress === 'number'
              ? `${status.progress}%`
              : t('home.progressActive');
            applyMarketReviewNotice({
              variant: 'warning',
              title: t('home.marketReviewInProgress'),
              message: status.region
                ? t('home.taskStatusWithRegion', { status: status.status, progress, region: status.region })
                : t('home.taskStatus', { status: status.status, progress }),
            });
            return true;
          }

          if (status.status === 'completed') {
            stopMarketReviewPolling();
            const marketReviewText = typeof status.marketReviewReport === 'string'
              ? status.marketReviewReport
              : '';
            setMarketReviewReport(marketReviewText ? marketReviewText.trim() : null);
            setMarketReviewPayload(status.marketReviewPayload ?? null);
            applyMarketReviewNotice({
              variant: 'success',
              title: t('home.marketReviewCompleted'),
              message: marketReviewText ? t('home.marketReviewCompletedWithReport') : t('home.marketReviewCompletedWithoutReport'),
            });
            setMarketReviewError(null);
            await refreshMarketReviewHistory(true);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }

          if (status.status === 'failed') {
            stopMarketReviewPolling();
            setMarketReviewReport(null);
            setMarketReviewPayload(null);
            setMarketReviewError(
              getParsedApiError({
                response: {
                  status: 500,
                  data: {
                    error: 'market_review_failed',
                    message: status.error || t('home.marketReviewFailed'),
                  },
                },
              }),
            );
            applyMarketReviewNotice(null);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }

          stopMarketReviewPolling();
          setMarketReviewReport(null);
          setMarketReviewPayload(null);
          applyMarketReviewNotice({
            variant: 'danger',
            title: t('home.marketReviewUnknownStatus'),
            message: t('home.unknownTaskStatus', { status: status.status }),
          });
          scrollMarketReviewFeedbackIntoView();
          return false;
        } catch (err: unknown) {
          const parsed = getParsedApiError(err);
          if (!mountedRef.current) {
            return false;
          }
          if (attempts >= maxAttempts) {
            stopMarketReviewPolling();
            setMarketReviewReport(null);
            setMarketReviewPayload(null);
            setMarketReviewError(parsed);
            updateMarketReviewNotice(null);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }
          return true;
        }
      };

      if (await poll()) {
        marketReviewPollTimer.current = window.setInterval(() => {
          void poll().then((shouldContinue) => {
            if (!shouldContinue) {
              stopMarketReviewPolling();
            }
          });
        }, intervalMs);
      }
    },
    [refreshMarketReviewHistory, scrollMarketReviewFeedbackIntoView, stopMarketReviewPolling, t, updateMarketReviewNotice],
  );

  const handleTriggerMarketReview = useCallback(async () => {
    setIsSubmittingMarketReview(true);
    updateMarketReviewNotice(null);
    setMarketReviewError(null);
    setMarketReviewReport(null);
    setMarketReviewPayload(null);
    scrollMarketReviewFeedbackIntoView();
    try {
      const result = await analysisApi.triggerMarketReview({
        sendNotification: notify,
        regions: marketReviewRegionOverride,
      });
      updateMarketReviewNotice({
        variant: 'success',
        title: t('home.marketReviewSubmitted'),
        message: t('home.marketReviewSubmittedWithRegion', {
          message: result.message,
          region: result.region,
        }),
      });
      scrollMarketReviewFeedbackIntoView();

      if (result.taskId) {
        await pollMarketReviewStatus(result.taskId);
      }
    } catch (err: unknown) {
      setMarketReviewError(getParsedApiError(err));
      updateMarketReviewNotice(null);
      scrollMarketReviewFeedbackIntoView();
    } finally {
      setIsSubmittingMarketReview(false);
    }
  }, [marketReviewRegionOverride, notify, pollMarketReviewStatus, scrollMarketReviewFeedbackIntoView, t, updateMarketReviewNotice]);

  const todayDateKey = useMemo(() => getTodayInShanghai(), []);
  useEffect(() => {
    if (sidebarWorkspaceTab !== 'today') {
      return undefined;
    }

    let active = true;
    setIsLoadingTodayAnalysisItems(true);
    setTodayAnalysisLoadFailed(false);
    void getTodayAnalysisItems(todayDateKey)
      .then((items) => {
        if (active) {
          setTodayHistoryItems(items);
          setTodayAnalysisLoadFailed(false);
        }
      })
      .catch(() => {
        if (active) {
          setTodayHistoryItems([]);
          setTodayAnalysisLoadFailed(true);
        }
      })
      .finally(() => {
        if (active) {
          setIsLoadingTodayAnalysisItems(false);
        }
      });

    return () => {
      active = false;
    };
  }, [sidebarWorkspaceTab, todayAnalysisRefreshVersion, todayDateKey]);

  const watchlistRows = useMemo<HomeWatchlistRow[]>(() => (
    watchlistState.watchlistCodes.map((code) => {
      const key = getStockCodeKey(code);
      const latestItemCandidate = key
        ? stockBarItemByCode.get(key) ?? watchlistHistoryItemsByCode.get(key)
        : undefined;
      const isMissingFromStockBar = Boolean(key && !stockBarItemByCode.has(key));
      const hasPendingHistoryLookup = Boolean(
        isMissingFromStockBar
        && (
          !canLookupWatchlistHistory
          ||
          watchlistHistoryLookupState.signature !== watchlistMissingHistorySignature
          || !watchlistHistoryLookupState.settledKeys.has(key)
        ),
      );
      const hasFailedHistoryLookup = Boolean(
        isMissingFromStockBar
        && canLookupWatchlistHistory
        && watchlistHistoryLookupState.signature === watchlistMissingHistorySignature
        && watchlistHistoryLookupState.failedKeys.has(key)
      );
      const isTodayStatusLoading = Boolean(
        isLoadingStockBar
        || hasPendingHistoryLookup
        || (key && completedTaskRefreshPendingCounts.has(key))
      );
      const isTodayStatusUnknown = Boolean(
        hasFailedHistoryLookup
        || (stockBarRefreshFailed && !hasPendingHistoryLookup)
      );
      // Keep the last-known item while a refresh/status check is in flight so the
      // row keeps showing its score/name/color instead of blanking out (visible
      // jitter on every stock-bar refresh). Stale-detail clicks are still blocked
      // separately via `isTodayStatusLoading` in the open handler.
      const latestItem = latestItemCandidate || undefined;
      return {
        code,
        latestItem,
        analyzedToday: !isTodayStatusLoading && !isTodayStatusUnknown && getShanghaiDateKey(latestItem?.lastAnalysisTime) === todayDateKey,
        isTodayStatusLoading,
        isTodayStatusUnknown,
      };
    })
  ), [
    canLookupWatchlistHistory,
    completedTaskRefreshPendingCounts,
    isLoadingStockBar,
    stockBarRefreshFailed,
    stockBarItemByCode,
    todayDateKey,
    watchlistHistoryItemsByCode,
    watchlistHistoryLookupState,
    watchlistMissingHistorySignature,
    watchlistState.watchlistCodes,
  ]);

  const watchlistAnalyzedTodayCount = useMemo(
    () => watchlistRows.filter((row) => row.analyzedToday).length,
    [watchlistRows],
  );

  const pendingWatchlistCodes = useMemo(
    () => watchlistRows
      .filter((row) => !row.analyzedToday && !row.isTodayStatusLoading && !row.isTodayStatusUnknown)
      .map((row) => row.code),
    [watchlistRows],
  );

  const watchlistTodayStatusBlocked = useMemo(
    () => watchlistRows.some((row) => row.isTodayStatusLoading || row.isTodayStatusUnknown),
    [watchlistRows],
  );

  const todayAnalysisItems = useMemo(() => {
    const itemsById = new Map<number, StockBarItem>();
    const addItem = (item: StockBarItem) => {
      if (item.stockCode === 'MARKET' || item.reportType === 'market_review') {
        return;
      }
      if (getShanghaiDateKey(item.lastAnalysisTime) !== todayDateKey) {
        return;
      }
      itemsById.set(item.id, item);
    };

    for (const item of todayHistoryItems) {
      addItem(item);
    }

    return Array.from(itemsById.values())
      .sort((left, right) => {
        const leftScore = typeof left.sentimentScore === 'number' ? left.sentimentScore : -1;
        const rightScore = typeof right.sentimentScore === 'number' ? right.sentimentScore : -1;
        if (rightScore !== leftScore) {
          return rightScore - leftScore;
        }
        const leftTime = getShanghaiTimeValue(left.lastAnalysisTime);
        const rightTime = getShanghaiTimeValue(right.lastAnalysisTime);
        return rightTime - leftTime;
      });
  }, [todayDateKey, todayHistoryItems]);

  const handleAnalyzeWatchlist = useCallback(async (mode: WatchlistAnalyzeMode) => {
    if (mode === 'pending' && watchlistTodayStatusBlocked) {
      setBatchAnalyzeStatus({
        variant: 'warning',
        message: t('watchlist.pendingStatusUnavailable'),
      });
      return;
    }

    const sourceCodes = mode === 'pending' ? pendingWatchlistCodes : watchlistState.watchlistCodes;
    const seen = new Set<string>();
    const targetCodes = sourceCodes.filter((code) => {
      const key = getStockCodeKey(code);
      if (!key || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });

    if (targetCodes.length === 0) {
      setBatchAnalyzeStatus({
        variant: 'warning',
        message: mode === 'pending' ? t('watchlist.noPendingAnalyze') : t('watchlist.noStocksAnalyze'),
      });
      return;
    }

    setIsBatchAnalyzingWatchlist(true);
    setBatchAnalyzeStatus(null);
    let acceptedCount = 0;
    let duplicateCount = 0;
    let confirmedCodeCount = 0;
    let submissionError: ParsedApiError | null = null;
    try {
      for (const chunk of chunkStockCodes(targetCodes)) {
        try {
          const result = await analysisApi.analyzeAsync({
            stockCodes: chunk,
            reportType: 'detailed',
            notify,
            skills: selectedAnalysisSkills,
          });
          const counts = countBatchAccepted(result);
          acceptedCount += counts.accepted;
          duplicateCount += counts.duplicates;
          const confirmedInChunk = counts.accepted + counts.duplicates;
          confirmedCodeCount += Math.min(confirmedInChunk, chunk.length);
          if (confirmedInChunk !== chunk.length) {
            submissionError = getParsedApiError(new Error(t('watchlist.batchIncompleteResponse', {
              confirmed: confirmedInChunk,
              requested: chunk.length,
            })));
            break;
          }
        } catch (error: unknown) {
          if (error instanceof DuplicateTaskError && chunk.length === 1) {
            duplicateCount += 1;
            confirmedCodeCount += 1;
            continue;
          }
          submissionError = getParsedApiError(error);
          break;
        }
      }

      // Reconcile even after a failed request: a timeout or disconnect may occur
      // after the server has accepted tasks, and earlier chunks may be running.
      await refreshActiveTasks();
      setSidebarWorkspaceTab('watchlist');

      if (submissionError) {
        if (acceptedCount > 0 || duplicateCount > 0) {
          setBatchAnalyzeStatus({
            variant: 'warning',
            message: t('watchlist.batchPartiallySubmitted', {
              accepted: acceptedCount,
              duplicates: duplicateCount,
              unconfirmed: targetCodes.length - confirmedCodeCount,
              error: submissionError.message || t('watchlist.batchFailed'),
            }),
          });
        } else {
          setBatchAnalyzeStatus({
            variant: 'danger',
            message: submissionError.message || t('watchlist.batchFailed'),
          });
        }
        return;
      }

      setBatchAnalyzeStatus({
        variant: acceptedCount > 0 ? 'success' : 'warning',
        message: t('watchlist.batchSubmitted', {
          accepted: acceptedCount,
          duplicates: duplicateCount,
        }),
      });
    } catch (error: unknown) {
      const parsed = getParsedApiError(error);
      setBatchAnalyzeStatus({
        variant: 'danger',
        message: parsed.message || t('watchlist.batchFailed'),
      });
    } finally {
      setIsBatchAnalyzingWatchlist(false);
    }
  }, [
    notify,
    pendingWatchlistCodes,
    refreshActiveTasks,
    selectedAnalysisSkills,
    t,
    watchlistTodayStatusBlocked,
    watchlistState.watchlistCodes,
  ]);

  const mergedStockBarItems = useMemo<StockBarItem[]>(() => {
    const latestMarketReview = marketReviewHistoryItems[0];
    const stockItems = stockBarItems.filter((item) => item.stockCode !== 'MARKET');
    if (!latestMarketReview) {
      return stockItems;
    }

    const marketReviewItem: StockBarItem = {
      id: latestMarketReview.id,
      stockCode: 'MARKET',
      stockName: latestMarketReview.stockName || t('home.marketReview'),
      reportType: 'market_review',
      sentimentScore: latestMarketReview.sentimentScore,
      operationAdvice: latestMarketReview.operationAdvice,
      analysisCount: Math.max(marketReviewHistoryItems.length, 1),
      lastAnalysisTime: latestMarketReview.createdAt,
      modelUsed: latestMarketReview.modelUsed,
      marketPhaseSummary: latestMarketReview.marketPhaseSummary,
    };

    return [marketReviewItem, ...stockItems].sort((left, right) => {
      const leftTime = left.lastAnalysisTime ? Date.parse(left.lastAnalysisTime) : 0;
      const rightTime = right.lastAnalysisTime ? Date.parse(right.lastAnalysisTime) : 0;
      return rightTime - leftTime;
    });
  }, [marketReviewHistoryItems, stockBarItems, t]);

  const sidebarContent = useMemo(
    () => (
      <div className="flex h-full min-h-0 flex-col gap-2 overflow-hidden">
        <HomeStockWorkspace
          activeTab={sidebarWorkspaceTab}
          onTabChange={setSidebarWorkspaceTab}
          watchlistRows={watchlistRows}
          watchlistLoading={watchlistState.isLoading}
          watchlistActioning={watchlistState.isActioning}
          watchlistMessage={watchlistState.actionMessage}
          onAddToWatchlist={watchlistState.addToWatchlist}
          onRemoveFromWatchlist={watchlistState.removeFromWatchlist}
          onRefreshWatchlist={handleRefreshWatchlist}
          onRefreshToday={handleDashboardDataRefresh}
          onAnalyzeWatchlist={handleAnalyzeWatchlist}
          isBatchAnalyzing={isBatchAnalyzingWatchlist}
          batchStatus={batchAnalyzeStatus}
          todayItems={todayAnalysisItems}
          isLoadingTodayItems={isLoadingTodayAnalysisItems}
          todayLoadError={todayAnalysisLoadFailed}
          watchlistAnalyzedTodayCount={watchlistAnalyzedTodayCount}
          historyItems={mergedStockBarItems}
          isLoadingHistory={isLoadingStockBar}
          selectedStockCode={selectedReport?.meta.stockCode}
          selectedRecordId={selectedReport?.meta.id}
          onHistoryItemClick={handleHistoryItemClick}
          onDeleteStock={handleDeleteStock}
          isDeleting={isDeletingStock}
          className="flex-1 overflow-hidden"
        />
      </div>
    ),
    [
      batchAnalyzeStatus,
      handleAnalyzeWatchlist,
      handleDeleteStock,
      handleDashboardDataRefresh,
      handleHistoryItemClick,
      handleRefreshWatchlist,
      isBatchAnalyzingWatchlist,
      isDeletingStock,
      isLoadingStockBar,
      isLoadingTodayAnalysisItems,
      todayAnalysisLoadFailed,
      mergedStockBarItems,
      selectedReport?.meta.id,
      selectedReport?.meta.stockCode,
      sidebarWorkspaceTab,
      todayAnalysisItems,
      watchlistAnalyzedTodayCount,
      watchlistRows,
      watchlistState.actionMessage,
      watchlistState.addToWatchlist,
      watchlistState.isActioning,
      watchlistState.isLoading,
      watchlistState.removeFromWatchlist,
    ],
  );

  // The homepage is a fixed-height box whose content scrolls in an inner
  // container, so the mouse wheel only works while hovering that container.
  // Forward wheel events over the header / any non-scrollable region to the
  // scroll container so scrolling works from anywhere on the page.
  const handleDashboardWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    const section = dashboardScrollRef.current;
    if (!section) return;

    const target = e.target as Node;
    let el: Element | null = target instanceof Element ? target : null;
    while (el && el !== document.body) {
      if (el === section) return; // already inside the scroll container -> native
      const style = window.getComputedStyle(el);
      const oy = style.overflowY;
      if (
        (oy === 'auto' || oy === 'scroll' || oy === 'overlay') &&
        el.scrollHeight > el.clientHeight
      ) {
        return; // inside another scrollable (dropdown / menu) -> native
      }
      el = el.parentElement;
    }

    // Wheel over a non-scrollable region (search header, gaps) -> forward it.
    e.preventDefault();
    section.scrollTop += e.deltaY;
  }, []);

  return (
    <MotionConfig reducedMotion="user">
    <div
      data-testid="home-dashboard"
      onWheel={handleDashboardWheel}
      className="flex h-[calc(100vh-4rem)] w-full flex-col overflow-hidden md:flex-row sm:h-[calc(100vh-4.5rem)]"
    >
      <div className="flex-1 flex flex-col min-h-0 min-w-0 w-full">
        <header className="relative z-30 flex min-w-0 flex-shrink-0 items-center overflow-visible pt-5 px-3 pb-3 md:px-4 md:pb-4">
          <div className="flex min-w-0 flex-1 flex-col gap-2.5 lg:flex-row lg:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <button
                onClick={() => setSidebarOpen(true)}
                className="md:hidden -ml-1 flex-shrink-0 rounded-lg p-1.5 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                aria-label={t('home.historyButton')}
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <div className="relative min-w-0 flex-1">
                <StockAutocomplete
                  value={query}
                  onChange={setQuery}
                  onSubmit={handleAutocompleteSubmit}
                  placeholder={t('home.placeholder')}
                  disabled={isAnalyzing}
                  className={inputError ? 'border-danger/50' : undefined}
                />
              </div>
              {analysisSkills.length > 0 ? (
                <div ref={strategyMenuRef} className="relative flex-shrink-0">
                  <button
                    ref={strategyButtonRef}
                    id="strategy-menu-button"
                    type="button"
                    aria-haspopup="menu"
                    aria-expanded={strategyMenuOpen}
                    aria-controls={strategyMenuOpen ? 'strategy-menu' : undefined}
                    onClick={() => setStrategyMenuOpen((open) => !open)}
                    onKeyDown={handleStrategyButtonKeyDown}
                    disabled={isAnalyzing}
                    className="inline-flex h-10 cursor-pointer items-center justify-center gap-1.5 rounded-xl border border-border/60 bg-card/55 px-4 text-sm text-foreground shadow-soft-card backdrop-blur-md transition-colors duration-200 hover:bg-card/70 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <SlidersHorizontal className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                    <span className="truncate">{selectedStrategy?.name || t('home.strategy')}</span>
                  </button>
                  <AnimatePresence>
                    {strategyMenuOpen ? (
                      <motion.div
                        id="strategy-menu"
                        role="menu"
                        aria-labelledby="strategy-menu-button"
                        onKeyDown={handleStrategyMenuKeyDown}
                        initial={{ opacity: 0, scale: 0.97, transformOrigin: 'top right' }}
                        animate={{ opacity: 1, scale: 1, transformOrigin: 'top right' }}
                        exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.12 } }}
                        transition={{ type: 'spring', duration: 0.28, bounce: 0 }}
                        className="absolute right-0 top-11 z-[120] max-h-80 w-[min(18rem,calc(100vw-1.5rem))] overflow-y-auto rounded-xl border border-subtle bg-elevated p-1.5 text-sm text-foreground shadow-2xl"
                      >
                        {strategyOptions.map((option, index) => {
                          const selected = selectedStrategyId === option.id;
                          return (
                            <button
                              key={option.id || 'default'}
                              ref={(node) => {
                                strategyItemRefs.current[index] = node;
                              }}
                              type="button"
                              role="menuitemradio"
                              aria-checked={selected}
                              tabIndex={-1}
                              onClick={() => selectStrategy(option.id)}
                              className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-hover"
                            >
                              <Check className={`mt-0.5 h-4 w-4 flex-shrink-0 ${selected ? 'opacity-100' : 'opacity-0'}`} aria-hidden="true" />
                              <span className="min-w-0">
                                <span className="block font-medium">{option.name}</span>
                                <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-muted-text">{option.description}</span>
                              </span>
                            </button>
                          );
                        })}
                      </motion.div>
                    ) : null}
                  </AnimatePresence>
                </div>
              ) : null}
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2.5">
              <MarketReviewRegionSelector
                value={marketReviewRegionOverride}
                disabled={isSubmittingMarketReview}
                onChange={setMarketReviewRegionOverride}
              />
              <label className="flex h-10 flex-shrink-0 cursor-pointer items-center gap-1.5 rounded-xl border border-subtle bg-surface/60 px-3 text-xs text-secondary-text select-none transition-colors hover:border-subtle-hover hover:text-foreground has-disabled:cursor-not-allowed has-disabled:opacity-50">
                <input
                  type="checkbox"
                  name="notifyPush"
                  checked={notify}
                  disabled={isSubmittingMarketReview}
                  onChange={(e) => setNotify(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border accent-primary"
                />
                {t('home.notify')}
              </label>
              <Button
                type="button"
                variant="secondary"
                size="md"
                isLoading={isSubmittingMarketReview}
                loadingText={t('home.submitMarketReview')}
                onClick={() => void handleTriggerMarketReview()}
                className="h-10 flex-1 whitespace-nowrap md:flex-none"
              >
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
                {t('home.marketReview')}
              </Button>
              <Button
                type="button"
                variant="primary"
                size="md"
                isLoading={isAnalyzing}
                loadingText={t('home.analyzing')}
                onClick={() => handleSubmitAnalysis()}
                disabled={!query}
                className="flex-1 whitespace-nowrap md:flex-none"
              >
                {t('home.analyze')}
              </Button>
            </div>
          </div>
        </header>

        {deleteError ? (
          <div className="px-3 pb-2 md:px-4">
            <InlineAlert
              variant="danger"
              title={t('common.deleteFailed')}
              message={deleteError}
              className="rounded-xl px-3 py-2 text-xs shadow-none"
            />
          </div>
        ) : null}

        {inputError || (duplicateError && duplicateBannerVisible) ? (
          <div className="px-3 pb-2 md:px-4">
            {inputError ? (
              <InlineAlert
                variant="danger"
                title={t('home.inputInvalid')}
                message={inputError}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
            {!inputError && duplicateError && duplicateBannerVisible ? (
              <InlineAlert
                variant="warning"
                title={t('home.duplicateTask')}
                message={duplicateError}
                action={(
                  <button
                    type="button"
                    onClick={dismissDuplicateBanner}
                    aria-label={t('common.close')}
                    className="-my-1 -mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg opacity-70 transition-colors hover:bg-warning/15 hover:opacity-100"
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                )}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
          </div>
        ) : null}

        {setupNeedsAction ? (
          <div className="px-3 pb-2 md:px-4">
            <InlineAlert
              variant="warning"
              title={t('home.setupIncomplete')}
              message={
                setupMissingLabels
                  ? t('home.setupMissingWithLabels', { labels: setupMissingLabels })
                  : t('home.setupMissingGeneric')
              }
              action={(
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => navigate('/settings')}
                >
                  {t('home.goSettings')}
                </Button>
              )}
              className="rounded-xl px-3 py-2 text-xs shadow-none"
            />
          </div>
        ) : null}

        <div className="flex-1 flex min-h-0 overflow-hidden">
          <div className="hidden min-h-0 w-64 shrink-0 flex-col overflow-hidden pl-4 pb-4 md:flex lg:w-72">
            {sidebarContent}
          </div>

          <AnimatePresence>
            {sidebarOpen ? (
              <div className="fixed inset-0 z-40 md:hidden" onClick={() => setSidebarOpen(false)}>
                <motion.div
                  className="page-drawer-overlay absolute inset-0"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18, ease: 'easeOut' }}
                />
                <motion.div
                  className="dashboard-card absolute bottom-0 left-0 top-0 flex w-72 flex-col overflow-hidden !rounded-none !rounded-r-xl p-3 shadow-2xl"
                  onClick={(event) => event.stopPropagation()}
                  initial={{ x: '-100%' }}
                  animate={{ x: 0 }}
                  exit={{ x: '-100%', transition: { duration: 0.18 } }}
                  transition={{ type: 'spring', duration: 0.32, bounce: 0 }}
                >
                  {sidebarContent}
                </motion.div>
              </div>
            ) : null}
          </AnimatePresence>

          <section
            ref={dashboardScrollRef}
            data-testid="home-dashboard-scroll"
            className="flex-1 min-w-0 min-h-0 overflow-x-auto overflow-y-auto px-3 pb-4 md:px-4 touch-pan-y"
          >
            <HomeReportRegion
              marketReviewNotice={marketReviewNotice}
              marketReviewError={marketReviewError}
              onDismissMarketReviewError={dismissMarketReviewError}
              marketReviewReport={marketReviewReport}
              marketReviewPayload={marketReviewPayload}
              error={error}
              onDismissError={clearError}
              isLoadingReport={isLoadingReport}
              selectedReport={selectedReport}
              isAnalyzing={isAnalyzing}
              isSubmittingMarketReview={isSubmittingMarketReview}
              onReanalyze={handleReanalyze}
              onAskFollowUp={handleAskFollowUp}
              onTriggerMarketReview={handleTriggerMarketReview}
              isHistoryTrendOpen={isHistoryTrendOpen}
              onToggleHistoryTrend={toggleHistoryTrend}
              onOpenMarkdownDrawer={openMarkdownDrawer}
              stockHistoryItems={stockHistoryItems}
              stockHistoryTotal={stockHistoryTotal}
              stockHistoryHasMore={stockHistoryHasMore}
              isLoadingStockHistory={isLoadingStockHistory}
              isLoadingMoreStockHistory={isLoadingMoreStockHistory}
              stockHistoryError={stockHistoryError}
              stockHistoryFilters={stockHistoryFilters}
              onCloseHistoryTrend={closeHistoryTrend}
              onSetStockHistoryRange={setStockHistoryRange}
              onLoadMoreStockHistory={loadMoreStockHistory}
              onSelectStockHistoryRecord={selectHistoryItem}
              onOpenHistoryTrend={openHistoryTrend}
              isInWatchlist={watchlistState.isInWatchlist}
              onToggleWatchlist={watchlistState.toggleWatchlist}
              isActioning={watchlistState.isActioning}
              actionMessage={watchlistState.actionMessage}
              onOpenRunFlow={openHistoryRunFlow}
            />
          </section>
        </div>
      </div>

      {markdownDrawerOpen && selectedReport?.meta.id ? (
        <ReportMarkdownDrawer
          key={selectedReport.meta.id}
          recordId={selectedReport.meta.id}
          stockName={selectedReport.meta.stockName || ''}
          stockCode={selectedReport.meta.stockCode}
          reportLanguage={reportLanguage}
          onClose={closeMarkdownDrawer}
        />
      ) : null}

      {runFlowDrawer.open ? (
        <Drawer
          isOpen={runFlowDrawer.open}
          onClose={closeRunFlowDrawer}
          title={t('runFlow.drawerTitle')}
          width="max-w-[96vw]"
          zIndex={80}
        >
          <RunFlowPanel
            key={`${runFlowDrawer.source.type}-${runFlowDrawer.source.type === 'task' ? runFlowDrawer.source.taskId : runFlowDrawer.source.recordId}`}
            source={runFlowDrawer.source}
            title={runFlowDrawer.title}
          />
        </Drawer>
      ) : null}
    </div>
    </MotionConfig>
  );
};

export default HomePage;
