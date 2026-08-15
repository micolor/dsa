import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pie, PieChart, ResponsiveContainer, Tooltip, Legend, Cell } from 'recharts';
import { RefreshCw, X } from 'lucide-react';
import { decisionSignalsApi } from '../api/decisionSignals';
import { portfolioApi } from '../api/portfolio';
import { stocksApi } from '../api/stocks';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  EmptyState,
  InlineAlert,
  Loading,
  StatCard,
} from '../components/common';
import { PortfolioSignalSummary } from '../components/decision-signals/DecisionSignalDisplay';
import { DashboardPanelHeader } from '../components/dashboard';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';
import { PORTFOLIO_TEXT } from '../locales/featureText';
import type { FxRefreshFeedback } from '../utils/portfolioFormat';
import {
  buildFxRefreshFeedback,
  formatBrokerLabel,
  formatCashDirectionLabel,
  formatCorporateActionLabel,
  formatMoney,
  formatPct,
  formatPositionMoney,
  formatPositionPrice,
  formatPriceDecimal,
  formatSideLabel,
  formatSignedPct,
  getCsvCommitVariant,
  getCsvParseVariant,
  getFxRefreshFeedbackVariant,
  getPositionPriceLabel,
  getTodayIso,
  hasPositionPrice,
} from '../utils/portfolioFormat';
import type {
  DecisionSignalItem,
  DecisionSignalMarket,
} from '../types/decisionSignals';
import type {
  PortfolioAccountItem,
  PortfolioCashDirection,
  PortfolioCashLedgerListItem,
  PortfolioCorporateActionListItem,
  PortfolioCorporateActionType,
  PortfolioCostMethod,
  PortfolioImportBrokerItem,
  PortfolioImportCommitResponse,
  PortfolioImportParseResponse,
  PortfolioPositionItem,
  PortfolioRiskResponse,
  PortfolioSide,
  PortfolioSnapshotResponse,
  PortfolioTradeListItem,
} from '../types/portfolio';
import { useStockIndex } from '../hooks/useStockIndex';
import { areStockCodesEquivalent, normalizeStockCode } from '../utils/stockCode';
import { parseDecisionSignalDate } from '../utils/decisionSignalTime';
import { buildDecisionActionLabelMap, getDecisionActionLabel } from '../utils/decisionAction';
import { SELECT_CHEVRON_CLASS } from '../utils/formClasses';

const PIE_COLORS = ['#00d4ff', '#00ff88', '#ffaa00', '#ff7a45', '#7f8cff', '#ff4466'];
const DEFAULT_PAGE_SIZE = 20;
const PORTFOLIO_SIGNAL_LOOKUP_CONCURRENCY = 6;
const FALLBACK_BROKERS: PortfolioImportBrokerItem[] = [
  { broker: 'huatai', aliases: [], displayName: '华泰' },
  { broker: 'citic', aliases: ['zhongxin'], displayName: '中信' },
  { broker: 'cmb', aliases: ['cmbchina', 'zhaoshang'], displayName: '招商' },
];

type AccountOption = 'all' | number;
type EventType = 'trade' | 'cash' | 'corporate';

type FlatPosition = PortfolioPositionItem & {
  accountId: number;
  accountName: string;
};

type PortfolioSignalLookup = {
  stockCode: string;
  market?: DecisionSignalMarket;
};

type PortfolioSignalLookupResult = {
  items: DecisionSignalItem[];
  error: string | null;
};

type PortfolioPageLanguage = 'zh' | 'en';

const PORTFOLIO_LIMITATION_LABELS: Record<string, Record<PortfolioPageLanguage, string>> = {
  realtime_quote_best_effort: {
    zh: '实时行情为尽力获取',
    en: 'Realtime quotes are best-effort',
  },
  fx_and_cost_basis_partial: {
    zh: '汇率与成本基础为部分口径',
    en: 'FX and cost basis are partial',
  },
  sector_and_risk_metrics_limited: {
    zh: '行业与风险指标覆盖有限',
    en: 'Sector and risk metrics are limited',
  },
};

type PendingDelete =
  | { eventType: 'trade'; id: number; message: string }
  | { eventType: 'cash'; id: number; message: string }
  | { eventType: 'corporate'; id: number; message: string };

type PendingAccountDelete = {
  accountId: number;
  accountName: string;
};

type FxRefreshContext = {
  viewKey: string;
  requestId: number;
};

const PORTFOLIO_INPUT_CLASS =
  'input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const PORTFOLIO_SELECT_CLASS = `${SELECT_CHEVRON_CLASS} w-full`;
const PORTFOLIO_FILE_PICKER_CLASS =
  'input-surface input-focus-glow flex h-11 w-full cursor-pointer items-center justify-center rounded-xl border bg-transparent px-4 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

function getSignalTime(item: DecisionSignalItem): number {
  return parseDecisionSignalDate(item.createdAt)?.getTime()
    ?? parseDecisionSignalDate(item.updatedAt)?.getTime()
    ?? 0;
}

function isNewerSignal(left: DecisionSignalItem | undefined, right: DecisionSignalItem): boolean {
  if (!left) return true;
  return getSignalTime(right) > getSignalTime(left);
}

function formatPortfolioLimitation(limitation: string, language: PortfolioPageLanguage): string {
  return PORTFOLIO_LIMITATION_LABELS[limitation]?.[language] ?? limitation;
}

const DECISION_SIGNAL_MARKETS = new Set<DecisionSignalMarket>(['cn', 'hk', 'us', 'jp', 'kr', 'tw']);
type PortfolioAccountMarket = 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw';

function toDecisionSignalMarket(value: string | null | undefined): DecisionSignalMarket | undefined {
  const normalized = String(value || '').toLowerCase();
  return DECISION_SIGNAL_MARKETS.has(normalized as DecisionSignalMarket)
    ? normalized as DecisionSignalMarket
    : undefined;
}

function toPositionSignalLookupKey(stockCode: string, market?: DecisionSignalMarket): string {
  return `${market || ''}:${normalizeStockCode(stockCode).toUpperCase()}`;
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  mapper: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(1, concurrency), items.length);

  await Promise.all(Array.from({ length: workerCount }, async () => {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      results[currentIndex] = await mapper(items[currentIndex]);
    }
  }));

  return results;
}

async function loadPortfolioSignalLookup(lookup: PortfolioSignalLookup): Promise<PortfolioSignalLookupResult> {
  try {
    const response = await decisionSignalsApi.getLatest(lookup.stockCode, {
      market: lookup.market,
      limit: 1,
    });
    return { items: response.items, error: null };
  } catch (err) {
    return { items: [], error: getParsedApiError(err).message };
  }
}

const PortfolioPage: React.FC = () => {
  const { language, t } = useUiLanguage();
  const text = PORTFOLIO_TEXT[language];
  const decisionActionLabels = useMemo(() => buildDecisionActionLabelMap(t), [t]);
  const { index: stockIndex } = useStockIndex();
  const getStockName = useCallback((symbol: string): string | undefined => {
    const item = stockIndex.find((s) =>
      areStockCodesEquivalent(symbol, s.displayCode) || areStockCodesEquivalent(symbol, s.canonicalCode),
    );
    return item?.nameZh;
  }, [stockIndex]);

  // Set page title
  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const [accounts, setAccounts] = useState<PortfolioAccountItem[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<AccountOption>(() => {
    const saved = window.localStorage.getItem('portfolio.selectedAccount');
    if (saved === 'all') return 'all';
    if (saved !== null) {
      const parsed = Number(saved);
      if (!Number.isNaN(parsed) && parsed >= 0) return parsed;
    }
    return 'all';
  });
  useEffect(() => {
    window.localStorage.setItem('portfolio.selectedAccount', String(selectedAccount));
  }, [selectedAccount]);
  const [showCreateAccount, setShowCreateAccount] = useState(false);
  const [accountCreating, setAccountCreating] = useState(false);
  const [accountCreateError, setAccountCreateError] = useState<string | null>(null);
  const [accountCreateSuccess, setAccountCreateSuccess] = useState<string | null>(null);
  const [accountForm, setAccountForm] = useState({
    name: '',
    broker: 'Demo',
    market: 'cn' as PortfolioAccountMarket,
    baseCurrency: 'CNY',
  });
  const [costMethod, setCostMethod] = useState<PortfolioCostMethod>('fifo');
  const [snapshot, setSnapshot] = useState<PortfolioSnapshotResponse | null>(null);
  const [risk, setRisk] = useState<PortfolioRiskResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [fxRefreshing, setFxRefreshing] = useState(false);
  const [fxRefreshFeedback, setFxRefreshFeedback] = useState<FxRefreshFeedback | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [riskWarning, setRiskWarning] = useState<string | null>(null);
  const [writeWarning, setWriteWarning] = useState<string | null>(null);
  const [portfolioSignals, setPortfolioSignals] = useState<DecisionSignalItem[]>([]);
  const [portfolioSignalsLoading, setPortfolioSignalsLoading] = useState(false);
  const [portfolioSignalsWarning, setPortfolioSignalsWarning] = useState<string | null>(null);
  const [portfolioSignalsRefreshKey, setPortfolioSignalsRefreshKey] = useState(0);
  const portfolioSignalsRequestRef = useRef(0);
  // 快照/风险与事件列表的并发守卫：账户/成本法或事件筛选快速切换时，
  // 只让最新一次请求生效，避免慢响应覆盖新数据或提前清除加载态。
  const snapshotRequestRef = useRef(0);
  const eventsRequestRef = useRef(0);
  const [positionAnalysisLoadingKey, setPositionAnalysisLoadingKey] = useState<string | null>(null);
  const [positionAnalysisMessage, setPositionAnalysisMessage] = useState<string | null>(null);

  const [brokers, setBrokers] = useState<PortfolioImportBrokerItem[]>([]);
  const [selectedBroker, setSelectedBroker] = useState('huatai');
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvDryRun, setCsvDryRun] = useState(true);
  const [csvParsing, setCsvParsing] = useState(false);
  const [csvCommitting, setCsvCommitting] = useState(false);
  const [csvParseResult, setCsvParseResult] = useState<PortfolioImportParseResponse | null>(null);
  const [csvCommitResult, setCsvCommitResult] = useState<PortfolioImportCommitResponse | null>(null);
  const [brokerLoadWarning, setBrokerLoadWarning] = useState<string | null>(null);

  const [eventType, setEventType] = useState<EventType>('trade');
  const [eventDateFrom, setEventDateFrom] = useState('');
  const [eventDateTo, setEventDateTo] = useState('');
  const [eventSymbol, setEventSymbol] = useState('');
  const [eventSide, setEventSide] = useState<'' | PortfolioSide>('');
  const [eventDirection, setEventDirection] = useState<'' | PortfolioCashDirection>('');
  const [eventActionType, setEventActionType] = useState<'' | PortfolioCorporateActionType>('');
  const [eventPage, setEventPage] = useState(1);
  const [eventTotal, setEventTotal] = useState(0);
  const [eventLoading, setEventLoading] = useState(false);
  const [tradeEvents, setTradeEvents] = useState<PortfolioTradeListItem[]>([]);
  const [cashEvents, setCashEvents] = useState<PortfolioCashLedgerListItem[]>([]);
  const [corporateEvents, setCorporateEvents] = useState<PortfolioCorporateActionListItem[]>([]);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [pendingAccountDelete, setPendingAccountDelete] = useState<PendingAccountDelete | null>(null);
  const [accountDeleteLoading, setAccountDeleteLoading] = useState(false);
  const [tradeModalOpen, setTradeModalOpen] = useState(false);
  const [cashModalOpen, setCashModalOpen] = useState(false);
  const [corpModalOpen, setCorpModalOpen] = useState(false);
  const [csvModalOpen, setCsvModalOpen] = useState(false);
  const [eventDialogOpen, setEventDialogOpen] = useState(false);
  const tradeFormRef = useRef<HTMLFormElement>(null);

  const [tradeForm, setTradeForm] = useState({
    symbol: '',
    tradeDate: getTodayIso(),
    side: 'buy' as PortfolioSide,
    quantity: '',
    price: '',
    fee: '',
    tax: '',
    tradeUid: '',
    note: '',
  });
  const [cashForm, setCashForm] = useState({
    eventDate: getTodayIso(),
    direction: 'in' as PortfolioCashDirection,
    amount: '',
    currency: '',
    note: '',
  });
  const [corpForm, setCorpForm] = useState({
    symbol: '',
    effectiveDate: getTodayIso(),
    actionType: 'cash_dividend' as PortfolioCorporateActionType,
    cashDividendPerShare: '',
    splitRatio: '',
    note: '',
  });

  const queryAccountId = selectedAccount === 'all' ? undefined : selectedAccount;
  const refreshViewKey = `${selectedAccount === 'all' ? 'all' : `account:${selectedAccount}`}:cost:${costMethod}`;
  const refreshContextRef = useRef<FxRefreshContext>({ viewKey: refreshViewKey, requestId: 0 });
  const hasAccounts = accounts.length > 0;
  const writableAccount = selectedAccount === 'all' ? undefined : accounts.find((item) => item.id === selectedAccount);
  const writableAccountId = writableAccount?.id;
  const writeBlocked = !writableAccountId;
  const canDeleteSelectedAccount = Boolean(writableAccountId) && !isLoading && !fxRefreshing && !accountDeleteLoading;
  const accountNameOf = (accountId: number): string => accounts.find((a) => a.id === accountId)?.name ?? '';
  const totalEventPages = Math.max(1, Math.ceil(eventTotal / DEFAULT_PAGE_SIZE));
  const currentEventCount = eventType === 'trade'
    ? tradeEvents.length
    : eventType === 'cash'
      ? cashEvents.length
      : corporateEvents.length;

  const isActiveRefreshContext = (requestedViewKey: string, requestedRequestId: number) => {
    return (
      refreshContextRef.current.viewKey === requestedViewKey
      && refreshContextRef.current.requestId === requestedRequestId
    );
  };

  const loadAccounts = useCallback(async () => {
    try {
      const response = await portfolioApi.getAccounts(false);
      const items = response.accounts || [];
      setAccounts(items);
      setSelectedAccount((prev) => {
        if (items.length === 0) return 'all';
        if (prev !== 'all' && !items.some((item) => item.id === prev)) return items[0].id;
        return prev;
      });
      if (items.length === 0) setShowCreateAccount(true);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  }, []);

  const loadBrokers = useCallback(async () => {
    try {
      const response = await portfolioApi.listImportBrokers();
      const brokerItems = response.brokers || [];
      if (brokerItems.length === 0) {
        setBrokers(FALLBACK_BROKERS);
        setBrokerLoadWarning('券商列表接口返回为空，已回退为内置券商列表（华泰/中信/招商）。');
        if (!FALLBACK_BROKERS.some((item) => item.broker === selectedBroker)) {
          setSelectedBroker(FALLBACK_BROKERS[0].broker);
        }
        return;
      }
      setBrokers(brokerItems);
      setBrokerLoadWarning(null);
      if (!brokerItems.some((item) => item.broker === selectedBroker)) {
        setSelectedBroker(brokerItems[0].broker);
      }
    } catch {
      setBrokers(FALLBACK_BROKERS);
      setBrokerLoadWarning('券商列表接口不可用，已回退为内置券商列表（华泰/中信/招商）。');
      if (!FALLBACK_BROKERS.some((item) => item.broker === selectedBroker)) {
        setSelectedBroker(FALLBACK_BROKERS[0].broker);
      }
    }
  }, [selectedBroker]);

  const loadSnapshotAndRisk = useCallback(async () => {
    const requestId = snapshotRequestRef.current + 1;
    snapshotRequestRef.current = requestId;
    const isCurrentRequest = () => snapshotRequestRef.current === requestId;
    setIsLoading(true);
    setRiskWarning(null);
    try {
      const [snapshotResult, riskResult] = await Promise.allSettled([
        portfolioApi.getSnapshot({
          accountId: queryAccountId,
          costMethod,
          includeRealtime: false,
        }),
        portfolioApi.getRisk({
          accountId: queryAccountId,
          costMethod,
          includeRealtime: false,
        }),
      ]);
      if (!isCurrentRequest()) {
        return;
      }
      if (snapshotResult.status === 'fulfilled') {
        setSnapshot(snapshotResult.value);
        setError(null);
      } else {
        setSnapshot(null);
        setRisk(null);
        setError(getParsedApiError(snapshotResult.reason));
      }
      if (riskResult.status === 'fulfilled') {
        setRisk(riskResult.value);
      } else {
        setRisk(null);
        const parsed = getParsedApiError(riskResult.reason);
        setRiskWarning(parsed.message || '风险数据获取失败，已降级为仅展示快照数据。');
      }
    } finally {
      if (isCurrentRequest()) {
        setIsLoading(false);
      }
    }
  }, [queryAccountId, costMethod]);

  const loadEventsPage = useCallback(async (page: number) => {
    const requestId = eventsRequestRef.current + 1;
    eventsRequestRef.current = requestId;
    const isCurrentRequest = () => eventsRequestRef.current === requestId;
    setEventLoading(true);
    try {
      if (eventType === 'trade') {
        const response = await portfolioApi.listTrades({
          accountId: queryAccountId,
          dateFrom: eventDateFrom || undefined,
          dateTo: eventDateTo || undefined,
          symbol: eventSymbol || undefined,
          side: eventSide || undefined,
          page,
          pageSize: DEFAULT_PAGE_SIZE,
        });
        if (!isCurrentRequest()) {
          return;
        }
        setTradeEvents(response.items || []);
        setEventTotal(response.total || 0);
      } else if (eventType === 'cash') {
        const response = await portfolioApi.listCashLedger({
          accountId: queryAccountId,
          dateFrom: eventDateFrom || undefined,
          dateTo: eventDateTo || undefined,
          direction: eventDirection || undefined,
          page,
          pageSize: DEFAULT_PAGE_SIZE,
        });
        if (!isCurrentRequest()) {
          return;
        }
        setCashEvents(response.items || []);
        setEventTotal(response.total || 0);
      } else {
        const response = await portfolioApi.listCorporateActions({
          accountId: queryAccountId,
          dateFrom: eventDateFrom || undefined,
          dateTo: eventDateTo || undefined,
          symbol: eventSymbol || undefined,
          actionType: eventActionType || undefined,
          page,
          pageSize: DEFAULT_PAGE_SIZE,
        });
        if (!isCurrentRequest()) {
          return;
        }
        setCorporateEvents(response.items || []);
        setEventTotal(response.total || 0);
      }
    } catch (err) {
      if (!isCurrentRequest()) {
        return;
      }
      setError(getParsedApiError(err));
    } finally {
      if (isCurrentRequest()) {
        setEventLoading(false);
      }
    }
  }, [
    eventActionType,
    eventDateFrom,
    eventDateTo,
    eventDirection,
    eventSide,
    eventSymbol,
    eventType,
    queryAccountId,
  ]);

  const loadEvents = useCallback(async () => {
    await loadEventsPage(eventPage);
  }, [eventPage, loadEventsPage]);

  const refreshPortfolioData = useCallback(async (page = eventPage) => {
    await Promise.all([loadSnapshotAndRisk(), loadEventsPage(page)]);
  }, [eventPage, loadEventsPage, loadSnapshotAndRisk]);

  useEffect(() => {
    void loadAccounts();
    void loadBrokers();
  }, [loadAccounts, loadBrokers]);

  useEffect(() => {
    void loadSnapshotAndRisk();
  }, [loadSnapshotAndRisk]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  // 卸载时作废在途的快照/事件请求，与组合信号 loader 的 cleanup 同一守卫思路。
  useEffect(() => {
    return () => {
      snapshotRequestRef.current += 1;
      eventsRequestRef.current += 1;
    };
  }, []);

  useEffect(() => {
    refreshContextRef.current = {
      viewKey: refreshViewKey,
      requestId: refreshContextRef.current.requestId + 1,
    };
    setFxRefreshing(false);
    setFxRefreshFeedback(null);
  }, [refreshViewKey]);

  useEffect(() => {
    setEventPage(1);
  }, [eventType, queryAccountId, eventDateFrom, eventDateTo, eventSymbol, eventSide, eventDirection, eventActionType]);

  useEffect(() => {
    if (!writeBlocked) {
      setWriteWarning(null);
    }
  }, [writeBlocked]);

  const positionRows: FlatPosition[] = useMemo(() => {
    if (!snapshot) return [];
    const rows: FlatPosition[] = [];
    for (const account of snapshot.accounts || []) {
      for (const position of account.positions || []) {
        rows.push({
          ...position,
          accountId: account.accountId,
          accountName: account.accountName,
        });
      }
    }
    rows.sort((a, b) => Number(b.marketValueBase || 0) - Number(a.marketValueBase || 0));
    return rows;
  }, [snapshot]);

  const snapshotMatchesAccountScope = useMemo(() => {
    if (!snapshot) return false;
    const snapshotAccountIds = new Set((snapshot.accounts || []).map((account) => account.accountId));
    if (queryAccountId !== undefined) {
      return snapshotAccountIds.size === 1 && snapshotAccountIds.has(queryAccountId);
    }
    return accounts.length === 0 || Number(snapshot.accountCount || 0) === accounts.length;
  }, [accounts.length, queryAccountId, snapshot]);

  const positionSignalLookups = useMemo(() => {
    const lookups = new Map<string, PortfolioSignalLookup>();
    for (const row of positionRows) {
      const stockCode = String(row.symbol || '').trim();
      if (!stockCode) continue;
      const market = toDecisionSignalMarket(row.market);
      const key = toPositionSignalLookupKey(stockCode, market);
      if (!lookups.has(key)) {
        lookups.set(key, { stockCode, market });
      }
    }
    return Array.from(lookups.values());
  }, [positionRows]);

  useEffect(() => {
    const requestId = portfolioSignalsRequestRef.current + 1;
    portfolioSignalsRequestRef.current = requestId;

    if (positionSignalLookups.length === 0 || !snapshotMatchesAccountScope) {
      setPortfolioSignals([]);
      setPortfolioSignalsWarning(null);
      setPortfolioSignalsLoading(false);
      return;
    }

    const isActiveRequest = () => portfolioSignalsRequestRef.current === requestId;

    const loadPortfolioSignals = async () => {
      setPortfolioSignalsLoading(true);
      setPortfolioSignalsWarning(null);
      const results = await mapWithConcurrency(
        positionSignalLookups,
        PORTFOLIO_SIGNAL_LOOKUP_CONCURRENCY,
        loadPortfolioSignalLookup,
      );
      if (!isActiveRequest()) return;
      const collected = results.flatMap((result) => result.items);
      const failures = results.flatMap((result) => (result.error ? [result.error] : []));
      setPortfolioSignals(collected);
      setPortfolioSignalsWarning(
        failures.length > 0
          ? (
              collected.length > 0
                ? formatUiText(t('decisionSignals.portfolioPartialWarning'), { message: failures[0] })
                : failures[0]
            )
          : null,
      );
      if (isActiveRequest()) {
        setPortfolioSignalsLoading(false);
      }
    };

    void loadPortfolioSignals();

    return () => {
      portfolioSignalsRequestRef.current += 1;
    };
  }, [portfolioSignalsRefreshKey, positionSignalLookups, snapshotMatchesAccountScope, t]);

  const signalByPositionKey = useMemo(() => {
    const mapped = new Map<string, DecisionSignalItem>();
    for (const row of positionRows) {
      const rowMarket = String(row.market || '').toLowerCase();
      for (const signal of portfolioSignals) {
        const signalMarket = String(signal.market || '').toLowerCase();
        if (rowMarket && signalMarket && rowMarket !== signalMarket) {
          continue;
        }
        if (!areStockCodesEquivalent(row.symbol, signal.stockCode)) {
          continue;
        }
        const key = `${row.accountId}-${row.symbol}-${row.market}`;
        const existing = mapped.get(key);
        if (isNewerSignal(existing, signal)) {
          mapped.set(key, signal);
        }
      }
    }
    return mapped;
  }, [portfolioSignals, positionRows]);

  const handleAnalyzePosition = async (row: FlatPosition) => {
    const key = `${row.accountId}-${row.symbol}-${row.market}`;
    setPositionAnalysisLoadingKey(key);
    setPositionAnalysisMessage(null);
    setError(null);
    try {
      const task = await portfolioApi.analyzePosition(row.symbol, {
        accountId: row.accountId,
        analysisPhase: 'auto',
        force: false,
      });
      setPositionAnalysisMessage(`已提交 ${row.symbol} 分析任务：${task.taskId}`);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setPositionAnalysisLoadingKey(null);
    }
  };

  const handleFocusPosition = (row: FlatPosition) => {
    // Trade is the primary event type that carries a symbol; cash has no symbol
    // attribution. Keep the current account scope (queryAccountId) intact.
    setEventType('trade');
    setEventSymbol(row.symbol);
    setEventDialogOpen(true);
  };

  const sectorPieData = useMemo(() => {
    const sectors = risk?.sectorConcentration?.topSectors || [];
    return sectors
      .slice(0, 6)
      .map((item) => ({
        name: item.sector,
        value: Number(item.weightPct || 0),
      }))
      .filter((item) => item.value > 0);
  }, [risk]);

  const positionFallbackPieData = useMemo(() => {
    if (!risk?.concentration?.topPositions?.length) {
      return [];
    }
    return risk.concentration.topPositions
      .slice(0, 6)
      .map((item) => ({
        name: item.symbol,
        value: Number(item.weightPct || 0),
      }))
      .filter((item) => item.value > 0);
  }, [risk]);

  const concentrationPieData = sectorPieData.length > 0 ? sectorPieData : positionFallbackPieData;
  const concentrationMode = sectorPieData.length > 0 ? 'sector' : 'position';

  const handleTradeStockSelect = async (code: string) => {
    // Prefill the symbol; then best-effort fetch the current price as a default (user can edit).
    setTradeForm((prev) => ({ ...prev, symbol: code, price: '' }));
    try {
      const quote = await stocksApi.getQuote(code);
      if (quote.currentPrice > 0) {
        setTradeForm((prev) =>
          prev.symbol === code ? { ...prev, price: String(quote.currentPrice) } : prev,
        );
      }
    } catch {
      // Price lookup is best-effort; leave price blank for manual entry.
    }
  };

  const handleTradeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!writableAccountId) {
      setWriteWarning('请先在右上角选择具体账户，再进行录入或导入提交。');
      return;
    }
    try {
      setWriteWarning(null);
      await portfolioApi.createTrade({
        accountId: writableAccountId,
        symbol: tradeForm.symbol,
        tradeDate: tradeForm.tradeDate,
        side: tradeForm.side,
        quantity: Number(tradeForm.quantity),
        price: Number(tradeForm.price),
        fee: Number(tradeForm.fee || 0),
        tax: Number(tradeForm.tax || 0),
        tradeUid: tradeForm.tradeUid || undefined,
        note: tradeForm.note || undefined,
      });
      setTradeForm((prev) => ({ ...prev, symbol: '', tradeUid: '', note: '' }));
      setTradeModalOpen(false);
      void refreshPortfolioData();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const handleCashSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!writableAccountId) {
      setWriteWarning('请先在右上角选择具体账户，再进行录入或导入提交。');
      return;
    }
    try {
      setWriteWarning(null);
      await portfolioApi.createCashLedger({
        accountId: writableAccountId,
        eventDate: cashForm.eventDate,
        direction: cashForm.direction,
        amount: Number(cashForm.amount),
        currency: cashForm.currency || undefined,
        note: cashForm.note || undefined,
      });
      setCashForm((prev) => ({ ...prev, note: '' }));
      setCashModalOpen(false);
      void refreshPortfolioData();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const handleCorporateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!writableAccountId) {
      setWriteWarning('请先在右上角选择具体账户，再进行录入或导入提交。');
      return;
    }
    try {
      setWriteWarning(null);
      await portfolioApi.createCorporateAction({
        accountId: writableAccountId,
        symbol: corpForm.symbol,
        effectiveDate: corpForm.effectiveDate,
        actionType: corpForm.actionType,
        cashDividendPerShare: corpForm.cashDividendPerShare ? Number(corpForm.cashDividendPerShare) : undefined,
        splitRatio: corpForm.splitRatio ? Number(corpForm.splitRatio) : undefined,
        note: corpForm.note || undefined,
      });
      setCorpForm((prev) => ({ ...prev, symbol: '', note: '' }));
      setCorpModalOpen(false);
      void refreshPortfolioData();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const handleParseCsv = async () => {
    if (!csvFile) return;
    try {
      setCsvParsing(true);
      const parsed = await portfolioApi.parseCsvImport(selectedBroker, csvFile);
      setCsvParseResult(parsed);
      setCsvCommitResult(null);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setCsvParsing(false);
    }
  };

  const handleCommitCsv = async () => {
    if (!csvFile) return;
    if (!writableAccountId) {
      setWriteWarning('请先在右上角选择具体账户，再进行录入或导入提交。');
      return;
    }
    try {
      setWriteWarning(null);
      setCsvCommitting(true);
      const committed = await portfolioApi.commitCsvImport(writableAccountId, selectedBroker, csvFile, csvDryRun);
      setCsvCommitResult(committed);
      if (!csvDryRun) {
        await refreshPortfolioData();
      }
      setCsvModalOpen(false);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setCsvCommitting(false);
    }
  };

  const openDeleteDialog = (item: PendingDelete) => {
    setPendingDelete(item);
  };

  const openAccountDeleteDialog = () => {
    if (!writableAccount) {
      setWriteWarning('请先选择具体账户，再删除持仓账户。');
      return;
    }
    setPendingAccountDelete({
      accountId: writableAccount.id,
      accountName: writableAccount.name,
    });
  };

  const handleConfirmAccountDelete = async () => {
    if (!pendingAccountDelete || accountDeleteLoading) return;

    try {
      setAccountDeleteLoading(true);
      setWriteWarning(null);
      await portfolioApi.deleteAccount(pendingAccountDelete.accountId);
      const nextAccount = accounts.find((item) => item.id !== pendingAccountDelete.accountId);
      setSelectedAccount(nextAccount?.id ?? 'all');
      setPendingAccountDelete(null);
      setShowCreateAccount(!nextAccount);
      await loadAccounts();
      setEventPage(1);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setAccountDeleteLoading(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete || deleteLoading) return;

    const nextPage = currentEventCount === 1 && eventPage > 1 ? eventPage - 1 : eventPage;
    try {
      setDeleteLoading(true);
      setWriteWarning(null);
      if (pendingDelete.eventType === 'trade') {
        await portfolioApi.deleteTrade(pendingDelete.id);
      } else if (pendingDelete.eventType === 'cash') {
        await portfolioApi.deleteCashLedger(pendingDelete.id);
      } else {
        await portfolioApi.deleteCorporateAction(pendingDelete.id);
      }
      setPendingDelete(null);
      if (nextPage !== eventPage) {
        setEventPage(nextPage);
      }
      await refreshPortfolioData(nextPage);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCreateAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = accountForm.name.trim();
    if (!name) {
      setAccountCreateError('账户名称不能为空。');
      setAccountCreateSuccess(null);
      return;
    }
    try {
      setAccountCreating(true);
      setAccountCreateError(null);
      setAccountCreateSuccess(null);
      const created = await portfolioApi.createAccount({
        name,
        broker: accountForm.broker.trim() || undefined,
        market: accountForm.market,
        baseCurrency: accountForm.baseCurrency.trim() || 'CNY',
      });
      await loadAccounts();
      setSelectedAccount(created.id);
      setShowCreateAccount(false);
      setWriteWarning(null);
      setAccountForm({
        name: '',
        broker: 'Demo',
        market: accountForm.market,
        baseCurrency: accountForm.baseCurrency,
      });
      setAccountCreateSuccess('账户创建成功，已自动切换到该账户。');
    } catch (err) {
      const parsed = getParsedApiError(err);
      setAccountCreateError(parsed.message || '创建账户失败，请稍后重试。');
      setAccountCreateSuccess(null);
    } finally {
      setAccountCreating(false);
    }
  };

  const handleRefresh = async () => {
    await Promise.all([loadAccounts(), loadSnapshotAndRisk(), loadEvents(), loadBrokers()]);
    setPortfolioSignalsRefreshKey((current) => current + 1);
  };

  const reloadSnapshotAndRiskForScope = useCallback(async (
    requestedViewKey: string,
    requestedRequestId: number,
    requestedAccountId: number | undefined,
    requestedCostMethod: PortfolioCostMethod,
  ): Promise<boolean> => {
    if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
      return false;
    }

    setRiskWarning(null);

    try {
      const snapshotData = await portfolioApi.getSnapshot({
        accountId: requestedAccountId,
        costMethod: requestedCostMethod,
        includeRealtime: false,
      });
      if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        return false;
      }
      setSnapshot(snapshotData);
      setError(null);

      try {
        const riskData = await portfolioApi.getRisk({
          accountId: requestedAccountId,
          costMethod: requestedCostMethod,
          includeRealtime: false,
        });
        if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
          return false;
        }
        setRisk(riskData);
        setRiskWarning(null);
      } catch (riskErr) {
        if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
          return false;
        }
        setRisk(null);
        const parsed = getParsedApiError(riskErr);
        setRiskWarning(parsed.message || '风险数据获取失败，已降级为仅展示快照数据。');
      }
      return true;
    } catch (err) {
      if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        return false;
      }
      setSnapshot(null);
      setRisk(null);
      setError(getParsedApiError(err));
      return false;
    }
  }, []);

  const handleRefreshFx = async () => {
    if (!hasAccounts || isLoading || fxRefreshing) {
      return;
    }

    const requestedViewKey = refreshViewKey;
    const requestedAccountId = queryAccountId;
    const requestedCostMethod = costMethod;
    const requestedRequestId = refreshContextRef.current.requestId + 1;
    refreshContextRef.current = {
      viewKey: requestedViewKey,
      requestId: requestedRequestId,
    };

    try {
      setFxRefreshing(true);
      setFxRefreshFeedback(null);
      const result = await portfolioApi.refreshFx({
        accountId: requestedAccountId,
      });
      if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        return;
      }
      const reloaded = await reloadSnapshotAndRiskForScope(
        requestedViewKey,
        requestedRequestId,
        requestedAccountId,
        requestedCostMethod,
      );
      if (!reloaded || !isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        return;
      }
      setFxRefreshFeedback(buildFxRefreshFeedback(result));
    } catch (err) {
      if (!isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        return;
      }
      setError(getParsedApiError(err));
    } finally {
      if (isActiveRefreshContext(requestedViewKey, requestedRequestId)) {
        setFxRefreshing(false);
      }
    }
  };

  const decisionSignalRiskPreviewItems = (risk?.decisionSignalRisk?.items ?? []).slice(0, 3);
  const formatDecisionSignalRiskAction = (signal: Partial<DecisionSignalItem>): string => (
    getDecisionActionLabel(
      signal.action,
      signal.actionLabel,
      null,
      text.alert,
      decisionActionLabels,
    ) ?? text.alert
  );
  const snapshotQualityMessage = snapshot?.dataQuality === 'partial' && snapshot.limitations?.length
    ? snapshot.limitations
      .map((limitation) => formatPortfolioLimitation(limitation, language))
      .join(language === 'en' ? '; ' : '；')
    : null;

  return (
    <div className="portfolio-page flex h-[calc(100vh-4rem)] w-full flex-col overflow-hidden sm:h-[calc(100vh-4.5rem)]">
      <header className="relative z-30 flex flex-shrink-0 items-center overflow-visible px-3 pb-3 md:px-4 md:pb-4">
        {hasAccounts ? (
          <div className="grid w-full grid-cols-1 xl:grid-cols-[minmax(0,1fr)_220px_minmax(0,1fr)] items-end gap-2">
            <div>
              <p className="text-xs text-muted-text mb-1">{text.accountView}</p>
              <select
                value={String(selectedAccount)}
                onChange={(e) => setSelectedAccount(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                className={PORTFOLIO_SELECT_CLASS}
              >
                <option value="all">{text.allAccounts}</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} (#{account.id})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <p className="text-xs text-muted-text mb-1">{text.costMethod}</p>
              <select
                value={costMethod}
                onChange={(e) => setCostMethod(e.target.value as PortfolioCostMethod)}
                className={PORTFOLIO_SELECT_CLASS}
              >
                <option value="fifo">{text.fifo}</option>
                <option value="avg">{text.avg}</option>
              </select>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant={showCreateAccount ? 'secondary' : 'primary'}
                size="lg"
                className="flex-1 whitespace-nowrap"
                onClick={() => {
                  setShowCreateAccount((prev) => !prev);
                  setAccountCreateError(null);
                  setAccountCreateSuccess(null);
                }}
              >
                {showCreateAccount ? text.collapseCreate : text.createAccount}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="lg"
                className="flex-1 whitespace-nowrap"
                onClick={() => void handleRefresh()}
                disabled={isLoading || fxRefreshing}
              >
                <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                <span className="sr-only">{isLoading ? text.refreshing : text.refreshData}</span>
              </Button>
              <Button
                type="button"
                variant="danger-subtle"
                size="lg"
                className="flex-1 whitespace-nowrap"
                onClick={openAccountDeleteDialog}
                disabled={!canDeleteSelectedAccount}
              >
                {accountDeleteLoading ? text.deletingAccount : text.deleteAccount}
              </Button>
            </div>
          </div>
        ) : (
          <InlineAlert
            variant="warning"
            className="flex-1 rounded-lg px-3 py-2 text-xs shadow-none"
            message={text.noAccounts}
          />
        )}
      </header>

      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto px-3 pb-4 md:px-4">

      {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
      {riskWarning ? (
        <InlineAlert
          variant="warning"
          title={text.riskDegraded}
          message={riskWarning}
        />
      ) : null}
      {writeWarning ? (
        <InlineAlert
          variant="warning"
          title={text.operationHint}
          message={writeWarning}
        />
      ) : null}
      {positionAnalysisMessage ? (
        <InlineAlert
          variant="success"
          title={text.analysisTask}
          message={positionAnalysisMessage}
        />
      ) : null}

      {(showCreateAccount || !hasAccounts) ? (
        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader
            className="mb-1"
            title="新建账户"
            titleClassName="text-base font-semibold"
            actions={hasAccounts ? (
              <Button type="button" variant="ghost" size="sm" onClick={() => {
                setShowCreateAccount(false);
                setAccountCreateError(null);
                setAccountCreateSuccess(null);
              }}>
                收起
              </Button>
            ) : (
              <span className="text-xs text-secondary-text">创建后自动切换到该账户</span>
            )}
          />
          {accountCreateError ? (
            <InlineAlert
              variant="danger"
              className="mt-2 rounded-lg px-2 py-1 text-xs shadow-none"
              title="创建账户失败"
              message={accountCreateError}
            />
          ) : null}
          {accountCreateSuccess ? (
            <InlineAlert
              variant="success"
              className="mt-2 rounded-lg px-2 py-1 text-xs shadow-none"
              title="创建账户成功"
              message={accountCreateSuccess}
            />
          ) : null}
          <form className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2" onSubmit={handleCreateAccount}>
            <input
              className={`${PORTFOLIO_INPUT_CLASS} md:col-span-2`}
              placeholder="账户名称（必填）"
              value={accountForm.name}
              onChange={(e) => setAccountForm((prev) => ({ ...prev, name: e.target.value }))}
            />
            <input
              className={PORTFOLIO_INPUT_CLASS}
              placeholder="券商（可选，如 Demo/华泰）"
              value={accountForm.broker}
              onChange={(e) => setAccountForm((prev) => ({ ...prev, broker: e.target.value }))}
            />
            <input
              className={PORTFOLIO_INPUT_CLASS}
              placeholder="基准币（如 CNY/USD/HKD）"
              value={accountForm.baseCurrency}
              onChange={(e) => setAccountForm((prev) => ({ ...prev, baseCurrency: e.target.value.toUpperCase() }))}
            />
            <select
              className={PORTFOLIO_SELECT_CLASS}
              value={accountForm.market}
              onChange={(e) => setAccountForm((prev) => ({ ...prev, market: e.target.value as PortfolioAccountMarket }))}
            >
              <option value="cn">市场：A 股（cn）</option>
              <option value="hk">市场：港股（hk）</option>
              <option value="us">市场：美股（us）</option>
              <option value="jp">市场：日股（jp）</option>
              <option value="kr">市场：韩股（kr）</option>
              <option value="tw">市场：台股（tw）</option>
            </select>
            <Button type="submit" variant="primary" size="lg" disabled={accountCreating}>
              {accountCreating ? '创建中...' : '创建账户'}
            </Button>
          </form>
        </div>
      ) : null}

      {snapshotQualityMessage ? (
        <InlineAlert
          variant="warning"
          title={text.snapshotPartialTitle}
          message={snapshotQualityMessage}
          className="rounded-xl px-3 py-2 text-xs shadow-none"
        />
      ) : null}

      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <StatCard label={text.totalEquity} value={formatMoney(snapshot?.totalEquity, snapshot?.currency || 'CNY')} />
        <StatCard label={text.totalMarketValue} value={formatMoney(snapshot?.totalMarketValue, snapshot?.currency || 'CNY')} />
        <StatCard label={text.totalCash} value={formatMoney(snapshot?.totalCash, snapshot?.currency || 'CNY')} />
        <div className="glass-card !border-transparent p-4">
          <div className="flex items-start justify-between gap-3">
            <p className="text-xs uppercase tracking-[0.22em] text-secondary-text">{text.fxStatus}</p>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="shrink-0"
              onClick={() => void handleRefreshFx()}
              disabled={!hasAccounts || isLoading || fxRefreshing}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${fxRefreshing ? 'animate-spin' : ''}`} />
              <span className="sr-only">{fxRefreshing ? text.refreshing : text.refreshFx}</span>
            </Button>
          </div>
          <div className="mt-2">{snapshot?.fxStale ? <Badge variant="warning">{text.stale}</Badge> : <Badge variant="success">{text.latest}</Badge>}</div>
          {fxRefreshFeedback ? (
            <InlineAlert
              variant={getFxRefreshFeedbackVariant(fxRefreshFeedback.tone)}
              title={text.fxRefreshResult}
              message={fxRefreshFeedback.text}
              className="mt-3 rounded-xl px-3 py-2 text-xs shadow-none"
            />
          ) : null}
        </div>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="glass-card !border-transparent p-4 md:p-5 xl:col-span-2">
          <DashboardPanelHeader
            className="mb-3"
            title={text.positionsTitle}
            titleClassName="text-base font-semibold"
            actions={(
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-secondary-text">{formatUiText(text.countItems, { count: positionRows.length })}</span>
                <span className="h-5 w-px bg-border/60 self-center" aria-hidden="true" />
                <Button type="button" variant="outline" size="sm" className="whitespace-nowrap" disabled={writeBlocked} onClick={() => setTradeModalOpen(true)}>录入交易</Button>
                <Button type="button" variant="outline" size="sm" className="whitespace-nowrap" disabled={writeBlocked} onClick={() => setCashModalOpen(true)}>录入资金</Button>
                <Button type="button" variant="outline" size="sm" className="whitespace-nowrap" disabled={writeBlocked} onClick={() => setCorpModalOpen(true)}>公司行为</Button>
                <Button type="button" variant="outline" size="sm" className="whitespace-nowrap" disabled={writeBlocked} onClick={() => setCsvModalOpen(true)}>导入CSV</Button>
                <Button type="button" variant="secondary" size="sm" className="whitespace-nowrap" onClick={() => setEventDialogOpen(true)}>事件记录</Button>
              </div>
            )}
          />
          {portfolioSignalsWarning ? (
            <InlineAlert
              variant="warning"
              title={t('decisionSignals.portfolioWarningTitle')}
              message={portfolioSignalsWarning}
              className="mb-3 rounded-xl px-3 py-2 text-xs shadow-none"
            />
          ) : null}
          {positionRows.length === 0 ? (
            <EmptyState
              title={text.noPositionsTitle}
              description={text.noPositionsDescription}
              className="border-none bg-transparent px-4 py-8 shadow-none"
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-[860px] w-full text-sm">
                <thead className="text-xs text-muted-text border-b border-border/60">
                  <tr>
                    <th className="text-left py-2 pr-2">{text.account}</th>
                    <th className="text-left py-2 pr-2">{text.code}</th>
                    <th className="text-right py-2 pr-2">{text.quantity}</th>
                    <th className="text-right py-2 pr-2">{text.avgCost}</th>
                    <th className="text-right py-2 pr-2">{text.lastPrice}</th>
                    <th className="text-right py-2 pr-2">{text.marketValue}</th>
                    <th className="text-right py-2 pr-3">{text.unrealizedPnl}</th>
                    <th className="text-right py-2 pr-3">{text.returnPct}</th>
                    <th className="min-w-[8rem] text-right py-2 pr-3">{t('decisionSignals.portfolioColumn')}</th>
                    <th className="w-20 text-right py-2">{text.action}</th>
                  </tr>
                </thead>
                <tbody>
                  {positionRows.map((row) => {
                    const rowKey = `${row.accountId}-${row.symbol}-${row.market}`;
                    const analyzing = positionAnalysisLoadingKey === rowKey;
                    const signal = signalByPositionKey.get(rowKey);
                    const stockName = getStockName(row.symbol);
                    return (
                    <tr
                      key={rowKey}
                      className="cursor-pointer border-b border-border/40 transition-colors hover:bg-elevated/40"
                      onClick={() => handleFocusPosition(row)}
                    >
                      <td className="py-2 pr-2 text-secondary-text">{row.accountName}</td>
                      <td className="py-2 pr-2">
                        <div className="font-mono text-foreground">{row.symbol}</div>
                        {stockName ? <div className="text-[11px] text-secondary-text">{stockName}</div> : null}
                      </td>
                      <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(row.quantity, 2)}</td>
                      <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(row.avgCost, 4)}</td>
                      <td className="py-2 pr-2 text-right">
                        <div>{formatPositionPrice(row)}</div>
                        <div className={`text-[11px] ${hasPositionPrice(row) ? 'text-secondary-text' : 'text-warning'}`}>
                          {getPositionPriceLabel(row)}
                        </div>
                      </td>
                      <td className="py-2 pr-2 text-right">{formatPositionMoney(row.marketValueBase, row)}</td>
                      <td
                        className={`py-2 pr-3 text-right ${
                          hasPositionPrice(row)
                            ? row.unrealizedPnlBase >= 0
                              ? 'text-danger'
                              : 'text-success'
                            : 'text-secondary-text'
                        }`}
                      >
                        {formatPositionMoney(row.unrealizedPnlBase, row)}
                      </td>
                      <td
                        className={`py-2 pr-3 text-right ${
                          hasPositionPrice(row) && row.unrealizedPnlPct !== null && row.unrealizedPnlPct !== undefined
                            ? row.unrealizedPnlPct >= 0
                              ? 'text-danger'
                              : 'text-success'
                            : 'text-secondary-text'
                        }`}
                      >
                        {formatSignedPct(row.unrealizedPnlPct)}
                      </td>
                      <td className="py-2 pr-3 text-right align-top">
                        <PortfolioSignalSummary item={signal} loading={portfolioSignalsLoading} />
                      </td>
                      <td className="py-2 text-right">
                        <Button
                          type="button"
                          variant="secondary"
                          size="xsm"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleAnalyzePosition(row);
                          }}
                          disabled={analyzing}
                        >
                          {analyzing ? text.submitting : text.analyze}
                        </Button>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader
            className="mb-3"
            title={concentrationMode === 'sector' ? text.sectorConcentration : text.positionConcentrationFallback}
            titleClassName="text-base font-semibold"
          />
          {isLoading ? (
            <Loading className="h-64" />
          ) : concentrationPieData.length > 0 ? (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={concentrationPieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} isAnimationActive={false}>
                    {concentrationPieData.map((entry, index) => (
                      <Cell key={`cell-${entry.name}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState
              title={text.noConcentrationTitle}
              description={text.noConcentrationDescription}
              className="border-none bg-transparent px-4 py-10 shadow-none"
            />
          )}
          <div className="mt-3 text-xs text-secondary-text space-y-1">
            <div>{text.displayScope}: {concentrationMode === 'sector' ? text.sectorDimension : text.positionDimensionFallback}</div>
            <div>{text.sectorAlert}: {risk?.sectorConcentration?.alert ? text.yes : text.no}</div>
            <div>{text.topWeight}: {formatPct(risk?.sectorConcentration?.topWeightPct ?? risk?.concentration?.topWeightPct)}</div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader className="mb-2" title={text.drawdownMonitor} titleClassName="text-sm font-semibold" />
          <div className="text-xs text-secondary-text space-y-1">
            <div>{text.maxDrawdown}: {formatPct(risk?.drawdown?.maxDrawdownPct)}</div>
            <div>{text.currentDrawdown}: {formatPct(risk?.drawdown?.currentDrawdownPct)}</div>
            <div>{text.alert}: {risk?.drawdown?.alert ? text.yes : text.no}</div>
          </div>
        </div>
        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader className="mb-2" title={text.stopLossWarning} titleClassName="text-sm font-semibold" />
          <div className="text-xs text-secondary-text space-y-1">
            <div>{text.triggeredCount}: {risk?.stopLoss?.triggeredCount ?? 0}</div>
            <div>{text.nearCount}: {risk?.stopLoss?.nearCount ?? 0}</div>
            <div>{text.alert}: {risk?.stopLoss?.nearAlert ? text.yes : text.no}</div>
            {risk?.stopLoss?.items && risk.stopLoss.items.length > 0 ? (
              <div className="mt-2 max-h-36 space-y-1 overflow-y-auto border-t border-border/50 pt-2">
                {risk.stopLoss.items.map((item) => (
                  <div key={`${item.accountId}-${item.symbol}`} className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.isTriggered ? 'bg-danger' : 'bg-warning'}`}
                        aria-hidden="true"
                      />
                      <span className="min-w-0 truncate font-medium text-foreground">{item.symbol}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-secondary-text">{formatPct(item.lossPct)}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader className="mb-2" title={text.scope} titleClassName="text-sm font-semibold" />
          <div className="text-xs text-secondary-text space-y-1">
            <div>{text.accountCount}: {snapshot?.accountCount ?? 0}</div>
            <div>{text.currency}: {snapshot?.currency || 'CNY'}</div>
            <div>{text.costMethodShort}: {(snapshot?.costMethod || costMethod).toUpperCase()}</div>
          </div>
        </div>
        <div className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader className="mb-2" title={text.aiRiskSignals} titleClassName="text-sm font-semibold" />
          <div className="text-xs text-secondary-text space-y-1">
            {risk?.decisionSignalRisk?.available === false ? (
              <div className="text-warning">{text.aiRiskUnavailable}</div>
            ) : (
              <>
                <div>{text.aiRiskTotal}: {risk?.decisionSignalRisk?.total ?? 0}</div>
                <div>
                  {text.sellSignals}: {risk?.decisionSignalRisk?.actions?.sell ?? 0} · {text.reduceSignals}: {risk?.decisionSignalRisk?.actions?.reduce ?? 0} · {text.alertSignals}: {risk?.decisionSignalRisk?.actions?.alert ?? 0}
                </div>
                {decisionSignalRiskPreviewItems.length > 0 ? (
                  <div className="space-y-1 pt-1">
                    {decisionSignalRiskPreviewItems.map((item) => (
                      <div key={`${item.accountId ?? 'all'}-${item.market}-${item.symbol}-${item.signal.id ?? item.signal.action}`} className="truncate text-foreground">
                        {item.symbol} · {formatDecisionSignalRiskAction(item.signal)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div>{text.noAiRiskSignals}</div>
                )}
              </>
            )}
          </div>
        </div>
      </section>


      <Dialog
        isOpen={eventDialogOpen}
        onClose={() => setEventDialogOpen(false)}
        title="事件记录"
        ariaLabel="事件记录"
        widthClassName="sm:max-w-4xl"
        maxHeightClassName="max-h-[92vh]"
      >
        <div className="space-y-3">
            {/* Toolbar: type toggle + inline focus chip + primary filters */}
            <div className="flex flex-wrap items-center gap-2">
              <select
                className={`${PORTFOLIO_SELECT_CLASS} w-32`}
                value={eventType}
                onChange={(e) => setEventType(e.target.value as EventType)}
                aria-label="事件类型"
              >
                <option value="trade">交易流水</option>
                <option value="cash">资金流水</option>
                <option value="corporate">公司行为</option>
              </select>

              {eventSymbol && (eventType === 'trade' || eventType === 'corporate') ? (
                <Badge variant="info" className="gap-1 pr-1">
                  只看 {eventSymbol}
                  <button
                    type="button"
                    aria-label="清除股票筛选"
                    className="ml-1 rounded-full p-0.5 hover:bg-white/10"
                    onClick={() => setEventSymbol('')}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </Badge>
              ) : null}

              <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
                {(eventType === 'trade' || eventType === 'corporate') ? (
                  <input className={`${PORTFOLIO_INPUT_CLASS} w-32`} placeholder="股票代码" value={eventSymbol}
                    onChange={(e) => setEventSymbol(e.target.value)} aria-label="股票代码筛选" />
                ) : null}
                <input className={`${PORTFOLIO_INPUT_CLASS} w-32`} type="date" value={eventDateFrom}
                  onChange={(e) => setEventDateFrom(e.target.value)} aria-label="起始日期" />
                <input className={`${PORTFOLIO_INPUT_CLASS} w-32`} type="date" value={eventDateTo}
                  onChange={(e) => setEventDateTo(e.target.value)} aria-label="结束日期" />
                {eventType === 'trade' ? (
                  <select className={`${PORTFOLIO_SELECT_CLASS} w-28`} value={eventSide}
                    onChange={(e) => setEventSide(e.target.value as '' | PortfolioSide)} aria-label="买卖方向">
                    <option value="">全部方向</option>
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                  </select>
                ) : null}
                {eventType === 'cash' ? (
                  <select className={`${PORTFOLIO_SELECT_CLASS} w-28`} value={eventDirection}
                    onChange={(e) => setEventDirection(e.target.value as '' | PortfolioCashDirection)} aria-label="资金方向">
                    <option value="">全部方向</option>
                    <option value="in">流入</option>
                    <option value="out">流出</option>
                  </select>
                ) : null}
                {eventType === 'corporate' ? (
                  <select className={`${PORTFOLIO_SELECT_CLASS} w-28`} value={eventActionType}
                    onChange={(e) => setEventActionType(e.target.value as '' | PortfolioCorporateActionType)} aria-label="公司行为类型">
                    <option value="">全部公司行为</option>
                    <option value="cash_dividend">现金分红</option>
                    <option value="split_adjustment">拆并股调整</option>
                  </select>
                ) : null}
                <Button type="button" variant="secondary" size="sm" onClick={() => void loadEvents()} disabled={eventLoading}>
                  <RefreshCw className={`h-4 w-4 ${eventLoading ? 'animate-spin' : ''}`} />
                  <span className="sr-only">{eventLoading ? '加载中...' : '刷新流水'}</span>
                </Button>
              </div>
            </div>

            <div className="overflow-x-auto rounded-lg border border-border/60">
              {eventType === 'trade' ? (
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-text border-b border-border/60">
                    <tr>
                      <th className="text-left py-2 pl-3 pr-2">账号</th>
                      <th className="text-left py-2 pr-2">日期</th>
                      <th className="text-left py-2 pr-2">方向</th>
                      <th className="text-left py-2 pr-2">代码</th>
                      <th className="text-right py-2 pr-2">数量</th>
                      <th className="text-right py-2 pr-2">价格</th>
                      <th className="text-right py-2 pr-2">手续费</th>
                      <th className="text-right py-2 pr-2">税费</th>
                      <th className="w-20 text-right py-2 pr-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradeEvents.map((item) => (
                      <tr key={`t-${item.id}`} className="border-b border-border/40 text-xs text-secondary-text">
                        <td className="py-2 pl-3 pr-2 whitespace-nowrap">{accountNameOf(item.accountId)}</td>
                        <td className="py-2 pr-2 whitespace-nowrap">{item.tradeDate}</td>
                        <td className="py-2 pr-2">{formatSideLabel(item.side)}</td>
                        <td className="py-2 pr-2 font-mono">{item.symbol}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(item.quantity, 2)}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(item.price, 4)}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(item.fee, 2)}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(item.tax, 2)}</td>
                        <td className="py-2 pr-3 text-right">
                          <Button
                            type="button"
                            variant="secondary"
                            size="xsm"
                            className="shrink-0"
                            onClick={() => openDeleteDialog({
                              eventType: 'trade',
                              id: item.id,
                              message: `确认删除 ${item.tradeDate} 的${formatSideLabel(item.side)}流水 ${item.symbol}（数量 ${item.quantity}，价格 ${item.price}）吗？`,
                            })}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}

              {eventType === 'cash' ? (
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-text border-b border-border/60">
                    <tr>
                      <th className="text-left py-2 pl-3 pr-2">账号</th>
                      <th className="text-left py-2 pr-2">日期</th>
                      <th className="text-left py-2 pr-2">方向</th>
                      <th className="text-right py-2 pr-2">金额</th>
                      <th className="text-left py-2 pr-2">币种</th>
                      <th className="w-20 text-right py-2 pr-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cashEvents.map((item) => (
                      <tr key={`c-${item.id}`} className="border-b border-border/40 text-xs text-secondary-text">
                        <td className="py-2 pl-3 pr-2 whitespace-nowrap">{accountNameOf(item.accountId)}</td>
                        <td className="py-2 pr-2 whitespace-nowrap">{item.eventDate}</td>
                        <td className="py-2 pr-2">{formatCashDirectionLabel(item.direction)}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">{formatPriceDecimal(item.amount, 2)}</td>
                        <td className="py-2 pr-2">{item.currency}</td>
                        <td className="py-2 pr-3 text-right">
                          <Button
                            type="button"
                            variant="secondary"
                            size="xsm"
                            className="shrink-0"
                            onClick={() => openDeleteDialog({
                              eventType: 'cash',
                              id: item.id,
                              message: `确认删除 ${item.eventDate} 的资金流水（${formatCashDirectionLabel(item.direction)} ${item.amount} ${item.currency}）吗？`,
                            })}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}

              {eventType === 'corporate' ? (
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-text border-b border-border/60">
                    <tr>
                      <th className="text-left py-2 pl-3 pr-2">账号</th>
                      <th className="text-left py-2 pr-2">日期</th>
                      <th className="text-left py-2 pr-2">类型</th>
                      <th className="text-left py-2 pr-2">代码</th>
                      <th className="text-right py-2 pr-2">每股分红</th>
                      <th className="text-right py-2 pr-2">拆分比</th>
                      <th className="w-20 text-right py-2 pr-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {corporateEvents.map((item) => (
                      <tr key={`ca-${item.id}`} className="border-b border-border/40 text-xs text-secondary-text">
                        <td className="py-2 pl-3 pr-2 whitespace-nowrap">{accountNameOf(item.accountId)}</td>
                        <td className="py-2 pr-2 whitespace-nowrap">{item.effectiveDate}</td>
                        <td className="py-2 pr-2">{formatCorporateActionLabel(item.actionType)}</td>
                        <td className="py-2 pr-2 font-mono">{item.symbol}</td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">
                          {item.cashDividendPerShare != null ? formatPriceDecimal(item.cashDividendPerShare, 4) : '--'}
                        </td>
                        <td className="py-2 pr-2 text-right whitespace-nowrap">
                          {item.splitRatio != null ? formatPriceDecimal(item.splitRatio, 4) : '--'}
                        </td>
                        <td className="py-2 pr-3 text-right">
                          <Button
                            type="button"
                            variant="secondary"
                            size="xsm"
                            className="shrink-0"
                            onClick={() => openDeleteDialog({
                              eventType: 'corporate',
                              id: item.id,
                              message: `确认删除 ${item.effectiveDate} 的公司行为 ${formatCorporateActionLabel(item.actionType)}（${item.symbol}）吗？`,
                            })}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}

              {!eventLoading
                && ((eventType === 'trade' && tradeEvents.length === 0)
                  || (eventType === 'cash' && cashEvents.length === 0)
                  || (eventType === 'corporate' && corporateEvents.length === 0)) ? (
                    <EmptyState
                      title="暂无流水"
                      description="调整筛选条件或先录入一笔交易、资金流水或公司行为。"
                      className="border-none bg-transparent px-3 py-6 shadow-none"
                    />
                  ) : null}
            </div>
            <div className="flex items-center justify-between text-xs text-secondary-text">
              <span>第 {eventPage} / {totalEventPages} 页</span>
              <div className="flex gap-2">
                <Button type="button" variant="outline" size="sm" disabled={eventPage <= 1}
                  onClick={() => setEventPage((prev) => Math.max(1, prev - 1))}>
                  上一页
                </Button>
                <Button type="button" variant="outline" size="sm" disabled={eventPage >= totalEventPages}
                  onClick={() => setEventPage((prev) => Math.min(totalEventPages, prev + 1))}>
                  下一页
                </Button>
              </div>
            </div>
          </div>
        </Dialog>
      </div>
      <ConfirmDialog
        isOpen={Boolean(pendingDelete)}
        title="删除错误流水"
        message={pendingDelete?.message || '确认删除这条流水吗？'}
        confirmText={deleteLoading ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger
        onConfirm={() => void handleConfirmDelete()}
        onCancel={() => {
          if (!deleteLoading) {
            setPendingDelete(null);
          }
        }}
      />
      <ConfirmDialog
        isOpen={Boolean(pendingAccountDelete)}
        title={text.deleteAccountTitle}
        message={
          pendingAccountDelete
            ? formatUiText(text.deleteAccountMessage, {
              name: pendingAccountDelete.accountName,
              id: pendingAccountDelete.accountId,
            })
            : ''
        }
        confirmText={accountDeleteLoading ? text.deletingAccount : text.deleteAccountConfirm}
        isDanger
        onConfirm={() => void handleConfirmAccountDelete()}
        onCancel={() => {
          if (!accountDeleteLoading) {
            setPendingAccountDelete(null);
          }
        }}
      />
      <Dialog
        isOpen={tradeModalOpen}
        onClose={() => setTradeModalOpen(false)}
        title="录入交易"
        ariaLabel="录入交易"
        widthClassName="sm:max-w-xl"
      >
        {writeBlocked ? <p className="mb-3 text-xs text-secondary-text">请先在右上角选择具体账户后再提交。</p> : null}
        <form ref={tradeFormRef} className="space-y-2" onSubmit={handleTradeSubmit}>
          <StockAutocomplete
            value={tradeForm.symbol}
            onChange={(v) => setTradeForm((prev) => ({ ...prev, symbol: v }))}
            onSubmit={handleTradeStockSelect}
            onEnterSubmit={() => tradeFormRef.current?.requestSubmit()}
            keepClosedAfterSelect
            placeholder="股票代码或名称（例如 600519 / 贵州茅台）"
            ariaLabel="股票代码或名称"
          />
          <div className="grid grid-cols-2 gap-2">
            <input className={PORTFOLIO_INPUT_CLASS} type="date" value={tradeForm.tradeDate}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, tradeDate: e.target.value }))} required />
            <select className={PORTFOLIO_SELECT_CLASS} value={tradeForm.side}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, side: e.target.value as PortfolioSide }))}>
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.0001" placeholder="数量（必填）" value={tradeForm.quantity}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, quantity: e.target.value }))} required />
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.0001" placeholder="成交价（必填）" value={tradeForm.price}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, price: e.target.value }))} required />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.0001" placeholder="手续费（可选）" value={tradeForm.fee}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, fee: e.target.value }))} />
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.0001" placeholder="税费（可选）" value={tradeForm.tax}
              onChange={(e) => setTradeForm((prev) => ({ ...prev, tax: e.target.value }))} />
          </div>
          <p className="text-xs text-secondary-text">手续费和税费可留空，系统将按 0 处理。</p>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="lg" className="flex-1" disabled={!writableAccountId}>提交交易</Button>
            <Button type="button" variant="outline" size="lg" onClick={() => setTradeModalOpen(false)}>关闭</Button>
          </div>
        </form>
      </Dialog>
      <Dialog
        isOpen={cashModalOpen}
        onClose={() => setCashModalOpen(false)}
        title="录入资金流水"
        ariaLabel="录入资金流水"
        widthClassName="sm:max-w-xl"
      >
        {writeBlocked ? <p className="mb-3 text-xs text-secondary-text">请先在右上角选择具体账户后再提交。</p> : null}
        <form className="space-y-2" onSubmit={handleCashSubmit}>
          <div className="grid grid-cols-2 gap-2">
            <input className={PORTFOLIO_INPUT_CLASS} type="date" value={cashForm.eventDate}
              onChange={(e) => setCashForm((prev) => ({ ...prev, eventDate: e.target.value }))} required />
            <select className={PORTFOLIO_SELECT_CLASS} value={cashForm.direction}
              onChange={(e) => setCashForm((prev) => ({ ...prev, direction: e.target.value as PortfolioCashDirection }))}>
              <option value="in">流入</option>
              <option value="out">流出</option>
            </select>
          </div>
          <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.0001" placeholder="金额"
            value={cashForm.amount} onChange={(e) => setCashForm((prev) => ({ ...prev, amount: e.target.value }))} required />
          <input className={PORTFOLIO_INPUT_CLASS} placeholder={`币种（可选，默认 ${writableAccount?.baseCurrency || '账户基准币'}）`} value={cashForm.currency}
            onChange={(e) => setCashForm((prev) => ({ ...prev, currency: e.target.value }))} />
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="lg" className="flex-1" disabled={!writableAccountId}>提交资金流水</Button>
            <Button type="button" variant="outline" size="lg" onClick={() => setCashModalOpen(false)}>关闭</Button>
          </div>
        </form>
      </Dialog>
      <Dialog
        isOpen={corpModalOpen}
        onClose={() => setCorpModalOpen(false)}
        title="录入公司行为"
        ariaLabel="录入公司行为"
        widthClassName="sm:max-w-xl"
      >
        {writeBlocked ? <p className="mb-3 text-xs text-secondary-text">请先在右上角选择具体账户后再提交。</p> : null}
        <form className="space-y-2" onSubmit={handleCorporateSubmit}>
          <input className={PORTFOLIO_INPUT_CLASS} placeholder="股票代码" value={corpForm.symbol}
            onChange={(e) => setCorpForm((prev) => ({ ...prev, symbol: e.target.value }))} required />
          <div className="grid grid-cols-2 gap-2">
            <input className={PORTFOLIO_INPUT_CLASS} type="date" value={corpForm.effectiveDate}
              onChange={(e) => setCorpForm((prev) => ({ ...prev, effectiveDate: e.target.value }))} required />
            <select className={PORTFOLIO_SELECT_CLASS} value={corpForm.actionType}
              onChange={(e) => setCorpForm((prev) => ({ ...prev, actionType: e.target.value as PortfolioCorporateActionType }))}>
              <option value="cash_dividend">现金分红</option>
              <option value="split_adjustment">拆并股调整</option>
            </select>
          </div>
          {corpForm.actionType === 'cash_dividend' ? (
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.000001" placeholder="每股分红"
              value={corpForm.cashDividendPerShare}
              onChange={(e) => setCorpForm((prev) => ({ ...prev, cashDividendPerShare: e.target.value, splitRatio: '' }))} required />
          ) : (
            <input className={PORTFOLIO_INPUT_CLASS} type="number" min="0" step="0.000001" placeholder="拆并股比例"
              value={corpForm.splitRatio}
              onChange={(e) => setCorpForm((prev) => ({ ...prev, splitRatio: e.target.value, cashDividendPerShare: '' }))} required />
          )}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="lg" className="flex-1" disabled={!writableAccountId}>提交企业行为</Button>
            <Button type="button" variant="outline" size="lg" onClick={() => setCorpModalOpen(false)}>关闭</Button>
          </div>
        </form>
      </Dialog>
      <Dialog
        isOpen={csvModalOpen}
        onClose={() => setCsvModalOpen(false)}
        title="导入 CSV"
        ariaLabel="导入 CSV"
        widthClassName="sm:max-w-xl"
      >
        <div className="space-y-2">
          {brokerLoadWarning ? (
            <InlineAlert
              variant="warning"
              className="rounded-lg px-2 py-1 text-xs shadow-none"
              message={brokerLoadWarning}
            />
          ) : null}
          {writeBlocked ? <p className="text-xs text-secondary-text">请先在右上角选择具体账户后再提交导入。</p> : null}
          <div className="grid grid-cols-2 gap-2">
            <select className={PORTFOLIO_SELECT_CLASS} value={selectedBroker} onChange={(e) => setSelectedBroker(e.target.value)}>
              {brokers.length > 0 ? (
                brokers.map((item) => <option key={item.broker} value={item.broker}>{formatBrokerLabel(item.broker, item.displayName)}</option>)
              ) : (
                <option value="huatai">huatai（华泰）</option>
              )}
            </select>
            <label className={PORTFOLIO_FILE_PICKER_CLASS}>
              选择 CSV
              <input type="file" accept=".csv" className="hidden"
                onChange={(e) => setCsvFile(e.target.files && e.target.files[0] ? e.target.files[0] : null)} />
            </label>
          </div>
          <div className="flex items-center gap-2 text-xs text-secondary-text">
            <input id="csv-dry-run" type="checkbox" checked={csvDryRun} onChange={(e) => setCsvDryRun(e.target.checked)} />
            <label htmlFor="csv-dry-run">仅预演（不写入）</label>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" size="lg" className="flex-1" disabled={!csvFile || csvParsing} onClick={() => void handleParseCsv()}>
              {csvParsing ? '解析中...' : '解析文件'}
            </Button>
            <Button type="button" variant="secondary" size="lg" className="flex-1"
              disabled={!csvFile || !writableAccountId || csvCommitting} onClick={() => void handleCommitCsv()}>
              {csvCommitting ? '提交中...' : '提交导入'}
            </Button>
          </div>
          {csvParseResult ? (
            <InlineAlert
              variant={getCsvParseVariant(csvParseResult)}
              title="CSV 解析结果"
              message={`有效 ${csvParseResult.recordCount} 条，跳过 ${csvParseResult.skippedCount} 条，错误 ${csvParseResult.errorCount} 条。`}
              className="rounded-lg px-3 py-2 text-xs shadow-none"
            />
          ) : null}
          {csvCommitResult ? (
            <InlineAlert
              variant={getCsvCommitVariant(csvCommitResult, csvDryRun)}
              title={csvDryRun ? 'CSV 预演结果' : 'CSV 提交结果'}
              message={`${csvDryRun ? '预演检查' : '实际写入'}：写入 ${csvCommitResult.insertedCount} 条，重复 ${csvCommitResult.duplicateCount} 条，失败 ${csvCommitResult.failedCount} 条。`}
              className="rounded-lg px-3 py-2 text-xs shadow-none"
            />
          ) : null}
        </div>
      </Dialog>
    </div>
  );
};

export default PortfolioPage;
