import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, BarChart3, RefreshCw, Search, ShieldCheck, X } from 'lucide-react';
import {
  decisionSignalsApi,
  getDecisionSignalReassessBlockedError,
} from '../api/decisionSignals';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { historyApi } from '../api/history';
import {
  ApiErrorAlert,
  AppPage,
  Card,
  Checkbox,
  Collapsible,
  ConfirmDialog,
  Drawer,
  EmptyState,
  InlineAlert,
  Pagination,
  Select,
} from '../components/common';
import { DecisionSignalActionQueue } from '../components/decision-signals/DecisionSignalActionQueue';
import {
  DecisionSignalCard,
  DecisionSignalDetails,
} from '../components/decision-signals/DecisionSignalDisplay';
import { DecisionSignalProfileCalibration } from '../components/decision-signals/DecisionSignalProfileCalibration';
import { DecisionSignalTimeline } from '../components/decision-signals/DecisionSignalTimeline';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { ToastPortal } from '../contexts/ToastHostContext';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { useStockIndex } from '../hooks/useStockIndex';
import type { UiTextKey } from '../i18n/uiText';
import type { DecisionAction, MarketPhaseValue, StockBarItem } from '../types/analysis';
import type {
  DecisionSignalItem,
  DecisionSignalFeedbackItem,
  DecisionSignalFeedbackValue,
  DecisionSignalListParams,
  DecisionSignalMarket,
  DecisionSignalOutcomeItem,
  DecisionSignalOutcomeStatsResponse,
  DecisionSignalReassessResponse,
  DecisionSignalReassessBlockedError,
  DecisionSignalHorizon,
  DecisionSignalSourceType,
  DecisionSignalStatus,
  DecisionProfile,
  DecisionProfileDisplay,
  SkillOpinionPerformanceBucket,
  SkillOpinionPerformanceStatsResponse,
} from '../types/decisionSignals';
import type { Market, StockIndexItem } from '../types/stockIndex';
import { cn } from '../utils/cn';
import { SELECT_INPUT_CLASS } from '../utils/formClasses';
import { buildDecisionActionLabelMap } from '../utils/decisionAction';
import { isStockCodeRedundantWithName } from '../utils/stockName';
import {
  getDecisionSignalHorizonLabel,
  getDecisionSignalMarketLabel,
  getDecisionSignalMarketPhaseLabel,
  getDecisionSignalSourceTypeLabel,
  getSkillLabel,
  getSkillOpinionSampleStatusLabel,
} from '../utils/decisionSignalLabels';
import { getDecisionProfile } from '../utils/decisionSignalProfile';
import { parseDecisionSignalDate } from '../utils/decisionSignalTime';
import { areStockCodesEquivalent } from '../utils/stockCode';

const PAGE_SIZE = 20;
const DEDUPE_PAGE_SIZE = 100;
const QUEUE_PAGE_SIZE = 100;
const TIMELINE_PAGE_SIZE = 100;
const STOCK_CANDIDATE_LIMIT = 8;
const DAY_MS = 86400_000;

type ListFilters = {
  market: '' | DecisionSignalMarket;
  stockCode: string;
  action: '' | DecisionAction;
  marketPhase: '' | MarketPhaseValue;
  sourceType: '' | DecisionSignalSourceType;
  sourceReportId: string;
  status: '' | DecisionSignalStatus;
};

type TimelineRange = '30d' | '90d' | '180d';
type TimelineStatusFilter = 'all' | 'active';

type TimelineFilters = {
  market: '' | DecisionSignalMarket;
  range: TimelineRange;
  status: TimelineStatusFilter;
  decisionProfile: '' | DecisionProfileDisplay;
};

type TimelineMarketSource = 'context' | 'user' | null;

type TimelineFilterUpdate = {
  filters: TimelineFilters;
  marketSource: TimelineMarketSource;
};

type AppliedTimelineContext = TimelineFilters & {
  stockCode: string;
};

type StockContext = {
  code: string;
  displayCode?: string;
  name?: string;
  market?: DecisionSignalMarket;
};

type StockCandidate = StockContext & {
  source: 'history' | 'popular';
};

type PendingStatusChange = {
  item: DecisionSignalItem;
  status: Extract<DecisionSignalStatus, 'closed' | 'invalidated' | 'archived'>;
  message: string;
};

type SelectedSignal = {
  item: DecisionSignalItem;
  source: 'list' | 'latest' | 'timeline' | 'persisted' | 'queue';
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const MARKET_OPTIONS: DecisionSignalMarket[] = ['cn', 'hk', 'us', 'jp', 'kr', 'tw'];
const ACTION_OPTIONS: DecisionAction[] = ['buy', 'add', 'hold', 'reduce', 'sell', 'watch', 'avoid', 'alert'];
const PHASE_OPTIONS: MarketPhaseValue[] = ['premarket', 'intraday', 'lunch_break', 'closing_auction', 'postmarket', 'non_trading', 'unknown'];
const SOURCE_OPTIONS: DecisionSignalSourceType[] = ['analysis', 'agent', 'alert', 'market_review', 'manual'];
const STATUS_OPTIONS: DecisionSignalStatus[] = ['active', 'expired', 'invalidated', 'closed', 'archived'];

const STATUS_ACTIONS: Array<PendingStatusChange['status']> = ['closed', 'invalidated', 'archived'];
const REASSESS_PROFILES: DecisionProfile[] = ['conservative', 'balanced', 'aggressive'];

const STATUS_LABEL_KEYS: Record<DecisionSignalStatus, UiTextKey> = {
  active: 'decisionSignals.active',
  expired: 'decisionSignals.expired',
  invalidated: 'decisionSignals.invalidated',
  closed: 'decisionSignals.closed',
  archived: 'decisionSignals.archived',
};

const STATUS_ACTION_LABEL_KEYS: Record<PendingStatusChange['status'], UiTextKey> = {
  closed: 'decisionSignals.close',
  invalidated: 'decisionSignals.invalidate',
  archived: 'decisionSignals.archive',
};

const STATUS_ACTION_CONFIRM_KEYS: Record<PendingStatusChange['status'], UiTextKey> = {
  closed: 'decisionSignals.closeConfirm',
  invalidated: 'decisionSignals.invalidateConfirm',
  archived: 'decisionSignals.archiveConfirm',
};

const DEFAULT_LIST_FILTERS: ListFilters = {
  market: '',
  stockCode: '',
  action: '',
  marketPhase: '',
  sourceType: '',
  sourceReportId: '',
  status: 'active',
};

/**
 * 判定列表筛选表单是否处于「有活跃筛选」状态。
 *
 * 必须与 DEFAULT_LIST_FILTERS 逐字段比较：DEFAULT_LIST_FILTERS.status 本身就是
 * 'active'，按真值判断会导致恒为 true。也不能复用 buildActiveFilterChips —— 它
 * 同样用真值判断，因此默认状态下就恒非空。
 */
// eslint-disable-next-line react-refresh/only-export-components -- 纯判定函数，与 DEFAULT_LIST_FILTERS 同源，就近导出便于复用与单测
export function hasActiveListFilters(filters: ListFilters): boolean {
  return (Object.keys(DEFAULT_LIST_FILTERS) as Array<keyof ListFilters>).some(
    (key) => filters[key] !== DEFAULT_LIST_FILTERS[key],
  );
}

const DEFAULT_TIMELINE_FILTERS: TimelineFilters = {
  market: '',
  range: '90d',
  status: 'all',
  decisionProfile: '',
};

const TIMELINE_RANGE_DAYS: Record<TimelineRange, number> = {
  '30d': 30,
  '90d': 90,
  '180d': 180,
};

function parseSourceReportId(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function getInitialFilters(search = typeof window === 'undefined' ? '' : window.location.search): ListFilters {
  const params = new URLSearchParams(search);
  const sourceReportId = parseSourceReportId(params.get('sourceReportId') ?? params.get('source_report_id') ?? '');
  if (sourceReportId === undefined) return DEFAULT_LIST_FILTERS;
  return {
    ...DEFAULT_LIST_FILTERS,
    sourceReportId: String(sourceReportId),
  };
}

/**
 * 摘掉 URL 上的深链筛选参数。
 *
 * `?sourceReportId=`（以及 `source_report_id`）只在首次进入时用于预填筛选，见
 * ``getInitialFilters``。用户重置或移除该筛选后，URL 上如果还留着它，下次
 * ``resetFilters`` 或重新挂载就会把它原样注入回来——输入框、chip、以及
 * ``toListParams`` 的短路分支（忽略其它筛选、强制 ``sourceType: 'analysis'``）
 * 全都会复活，「重置」对这个筛选就完全无效。本页直接读 ``window.location.search``、
 * 不走 router，所以也用 ``replaceState`` 就地改写（不新增历史记录）。
 */
function clearSourceReportIdParam() {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete('sourceReportId');
    url.searchParams.delete('source_report_id');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // URL 不可解析时忽略：深链预填只是便利功能，不影响页面其余部分。
  }
}

function toListParams(
  filters: ListFilters,
  page: number,
  pageSize: number = PAGE_SIZE,
  holdingOnly: boolean = false,
): DecisionSignalListParams {
  const holding = holdingOnly ? { holdingOnly: true } : {};
  const sourceReportId = parseSourceReportId(filters.sourceReportId);
  if (sourceReportId !== undefined) {
    return {
      sourceReportId,
      sourceType: 'analysis',
      page,
      pageSize,
      ...holding,
    };
  }

  return {
    market: filters.market || undefined,
    stockCode: filters.stockCode.trim() || undefined,
    action: filters.action || undefined,
    marketPhase: filters.marketPhase || undefined,
    sourceType: filters.sourceType || undefined,
    status: filters.status || undefined,
    page,
    pageSize,
    ...holding,
  };
}

function dedupeByLatest(items: DecisionSignalItem[]): DecisionSignalItem[] {
  const map = new Map<string, DecisionSignalItem>();
  for (const item of items) {
    const key = `${item.market}:${item.stockCode}`;
    const prev = map.get(key);
    const itemTs = item.createdAt ?? '';
    const prevTs = prev?.createdAt ?? '';
    if (!prev || itemTs > prevTs || (itemTs === prevTs && item.id > prev.id)) {
      map.set(key, item);
    }
  }
  return [...map.values()].sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? '') || b.id - a.id);
}

function refreshLatestSelection(
  current: SelectedSignal | null,
  latestItems: DecisionSignalItem[],
): SelectedSignal | null {
  if (!current || current.source !== 'latest') return current;
  const refreshed = latestItems.find((item) => item.id === current.item.id);
  return refreshed ? { source: 'latest', item: refreshed } : null;
}

function refreshTimelineSelection(
  current: SelectedSignal | null,
  timelineItems: DecisionSignalItem[],
): SelectedSignal | null {
  if (!current || current.source !== 'timeline') return current;
  const refreshed = timelineItems.find((item) => item.id === current.item.id);
  return refreshed ? { source: 'timeline', item: refreshed } : null;
}

function normalizeDecisionSignalMarket(value: unknown): DecisionSignalMarket | undefined {
  const market = String(value ?? '').trim().toUpperCase();
  if (!market || market === 'INDEX' || market === 'ETF' || market === 'UNKNOWN') return undefined;
  if (market === 'CN' || market === 'BSE') return 'cn';
  if (market === 'HK') return 'hk';
  if (market === 'US') return 'us';
  if (market === 'JP') return 'jp';
  if (market === 'KR') return 'kr';
  if (market === 'TW') return 'tw';
  if (MARKET_OPTIONS.includes(market.toLowerCase() as DecisionSignalMarket)) {
    return market.toLowerCase() as DecisionSignalMarket;
  }
  return undefined;
}

function getCandidateKey(candidate: Pick<StockCandidate, 'code' | 'market'>): string {
  const code = candidate.code.trim().toUpperCase();
  return candidate.market ? `${candidate.market}:${code}` : code;
}

function toHistoryCandidate(item: StockBarItem): StockCandidate | null {
  const code = String(item.stockCode || '').trim();
  if (!code || code.toUpperCase() === 'MARKET') return null;
  return {
    code,
    displayCode: code,
    name: item.stockName || undefined,
    market: normalizeDecisionSignalMarket(item.marketPhaseSummary?.market),
    source: 'history',
  };
}

function toPopularCandidates(index: StockIndexItem[], limit = STOCK_CANDIDATE_LIMIT): StockCandidate[] {
  const candidates: StockCandidate[] = [];
  const seen = new Set<string>();
  const sorted = [...index]
    .filter((item) => item.active && item.assetType === 'stock')
    .sort((left, right) => (right.popularity ?? 0) - (left.popularity ?? 0));

  for (const item of sorted) {
    const market = normalizeDecisionSignalMarket(item.market);
    const candidate: StockCandidate = {
      code: item.canonicalCode,
      displayCode: item.displayCode,
      name: item.nameZh,
      market,
      source: 'popular',
    };
    const key = getCandidateKey(candidate);
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push(candidate);
    if (candidates.length >= limit) break;
  }

  return candidates;
}

function toTimelineParams(filters: TimelineFilters, stockCode: string): DecisionSignalListParams {
  const days = TIMELINE_RANGE_DAYS[filters.range];
  const createdTo = new Date();
  const createdFrom = new Date(createdTo.getTime() - days * DAY_MS);
  return {
    market: filters.market || undefined,
    stockCode,
    createdFrom: createdFrom.toISOString(),
    createdTo: createdTo.toISOString(),
    status: filters.status === 'active' ? 'active' : undefined,
    decisionProfile: filters.decisionProfile || undefined,
    page: 1,
    pageSize: TIMELINE_PAGE_SIZE,
  };
}

function upsertDecisionSignal(
  current: DecisionSignalItem[],
  item: DecisionSignalItem,
  limit?: number,
): DecisionSignalItem[] {
  const next = [item, ...current.filter((candidate) => candidate.id !== item.id)];
  next.sort((left, right) => {
    const leftTime = parseDecisionSignalDate(left.createdAt)?.getTime() ?? Number.NEGATIVE_INFINITY;
    const rightTime = parseDecisionSignalDate(right.createdAt)?.getTime() ?? Number.NEGATIVE_INFINITY;
    return rightTime - leftTime || right.id - left.id;
  });
  return limit ? next.slice(0, limit) : next;
}

function itemMatchesStockContext(item: DecisionSignalItem, context: StockContext): boolean {
  return areStockCodesEquivalent(item.stockCode, context.code)
    && (!context.market || item.market === context.market);
}

function itemMatchesAppliedTimeline(
  item: DecisionSignalItem,
  context: AppliedTimelineContext,
  now = Date.now(),
): boolean {
  if (!areStockCodesEquivalent(item.stockCode, context.stockCode)) return false;
  if (context.market && item.market !== context.market) return false;
  if (context.status === 'active' && item.status !== 'active') return false;
  if (context.decisionProfile && getDecisionProfile(item) !== context.decisionProfile) return false;
  const createdAt = parseDecisionSignalDate(item.createdAt)?.getTime();
  if (createdAt === undefined) return false;
  return createdAt >= now - TIMELINE_RANGE_DAYS[context.range] * DAY_MS && createdAt <= now;
}

function isSameStockContext(
  previousContext: StockContext | null,
  nextContext: StockContext,
): boolean {
  return previousContext?.code.trim().toUpperCase() === nextContext.code.trim().toUpperCase()
    && previousContext?.market === nextContext.market;
}

function buildNextTimelineFilters(
  currentFilters: TimelineFilters,
  previousContext: StockContext | null,
  nextContext: StockContext,
  marketSource: TimelineMarketSource,
): TimelineFilterUpdate {
  if (isSameStockContext(previousContext, nextContext)) {
    return { filters: currentFilters, marketSource };
  }
  if (nextContext.market) {
    return {
      filters: { ...currentFilters, market: nextContext.market },
      marketSource: 'context',
    };
  }
  if (marketSource === 'context') {
    return {
      filters: { ...currentFilters, market: '' },
      marketSource: null,
    };
  }
  return { filters: currentFilters, marketSource };
}

function buildActiveFilterChips(
  filters: ListFilters,
  actionLabels: Record<string, string>,
  t: (key: UiTextKey) => string,
): Array<{ key: keyof ListFilters; label: string }> {
  const chips: Array<{ key: keyof ListFilters; label: string }> = [];
  if (filters.market) chips.push({ key: 'market', label: `${t('decisionSignals.market')}：${getDecisionSignalMarketLabel(filters.market, t)}` });
  if (filters.stockCode) chips.push({ key: 'stockCode', label: `${t('decisionSignals.stockCode')}：${filters.stockCode}` });
  if (filters.action) chips.push({ key: 'action', label: `${t('decisionSignals.action')}：${actionLabels[filters.action] ?? filters.action}` });
  if (filters.marketPhase) chips.push({ key: 'marketPhase', label: `${t('decisionSignals.marketPhase')}：${getDecisionSignalMarketPhaseLabel(filters.marketPhase, t)}` });
  if (filters.sourceType) chips.push({ key: 'sourceType', label: `${t('decisionSignals.source')}：${getDecisionSignalSourceTypeLabel(filters.sourceType, t)}` });
  if (filters.sourceReportId) chips.push({ key: 'sourceReportId', label: `${t('decisionSignals.sourceReportId')}：#${filters.sourceReportId}` });
  if (filters.status) chips.push({ key: 'status', label: `${t('decisionSignals.status')}：${t(STATUS_LABEL_KEYS[filters.status])}` });
  return chips;
}

function draftMatchesStockContext(draft: string, context: StockContext | null): context is StockContext {
  if (!context) return false;
  const normalizedDraft = draft.trim().toUpperCase();
  if (!normalizedDraft) return false;
  return normalizedDraft === context.code.trim().toUpperCase()
    || normalizedDraft === String(context.displayCode ?? '').trim().toUpperCase();
}

function formatStatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return Number(value).toFixed(2).replace(/\.?0+$/, '');
}

function formatStatPercent(value: number | null | undefined): string {
  const formatted = formatStatNumber(value);
  return formatted === '-' ? formatted : `${formatted}%`;
}

interface SkillStatsProgress {
  buckets: number;
  pending: number;
  evaluated: number;
  observational: number;
  unable: number;
  maxEvaluated: number;
}

/** 汇总 Skill 表现各 bucket 的样本进展：未达标时表格不渲染，改为展示这份进度。 */
function summarizeSkillStatsProgress(buckets: SkillOpinionPerformanceBucket[]): SkillStatsProgress {
  return buckets.reduce<SkillStatsProgress>(
    (progress, bucket) => ({
      buckets: progress.buckets + 1,
      pending: progress.pending + bucket.pending,
      evaluated: progress.evaluated + bucket.evaluated,
      observational: progress.observational + bucket.observational,
      unable: progress.unable + bucket.unable,
      maxEvaluated: Math.max(progress.maxEvaluated, bucket.evaluated),
    }),
    { buckets: 0, pending: 0, evaluated: 0, observational: 0, unable: 0, maxEvaluated: 0 },
  );
}

const DecisionSignalsPage: React.FC = () => {
  const { t } = useUiLanguage();
  const actionLabels = useMemo(() => buildDecisionActionLabelMap(t), [t]);
  const { index: stockIndex } = useStockIndex();
  const [filters, setFilters] = useState<ListFilters>(() => getInitialFilters());
  const [appliedFilters, setAppliedFilters] = useState<ListFilters>(() => getInitialFilters());
  const [page, setPage] = useState(1);
  const [dedupeLatest, setDedupeLatest] = useState(false);
  const [holdingOnly, setHoldingOnly] = useState(false);
  // 折叠区状态：筛选表单与统计区默认收起，收起时不挂载内容（见各自的 render）。
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [statsExpanded, setStatsExpanded] = useState(false);
  const [rawQueueItems, setRawQueueItems] = useState<DecisionSignalItem[]>([]);
  const [rawQueueLoading, setRawQueueLoading] = useState(true);
  const [rawQueueError, setRawQueueError] = useState<ParsedApiError | null>(null);
  const queueRequestIdRef = useRef(0);
  const [items, setItems] = useState<DecisionSignalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [selected, setSelected] = useState<SelectedSignal | null>(null);
  const [pendingStatus, setPendingStatus] = useState<PendingStatusChange | null>(null);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [outcomeStats, setOutcomeStats] = useState<DecisionSignalOutcomeStatsResponse | null>(null);
  // 统计区默认收起且不请求：两个 loading 的初值必须是 false，否则未展开时
  // 页面刷新按钮会被永久禁用。
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<ParsedApiError | null>(null);
  const [skillStats, setSkillStats] = useState<SkillOpinionPerformanceStatsResponse | null>(null);
  const [skillStatsLoading, setSkillStatsLoading] = useState(false);
  const [skillStatsError, setSkillStatsError] = useState<ParsedApiError | null>(null);
  const [skillStatsRunning, setSkillStatsRunning] = useState(false);
  const [stockDraft, setStockDraft] = useState('');
  const [activeStockContext, setActiveStockContext] = useState<StockContext | null>(null);
  const [historyCandidates, setHistoryCandidates] = useState<StockCandidate[]>([]);
  const [historyCandidatesLoaded, setHistoryCandidatesLoaded] = useState(false);
  const [latestItems, setLatestItems] = useState<DecisionSignalItem[]>([]);
  const [latestSearched, setLatestSearched] = useState(false);
  const [latestLoading, setLatestLoading] = useState(false);
  const [latestError, setLatestError] = useState<ParsedApiError | null>(null);
  const [timelineFilters, setTimelineFilters] = useState<TimelineFilters>(DEFAULT_TIMELINE_FILTERS);
  const [appliedTimelineContext, setAppliedTimelineContext] = useState<AppliedTimelineContext | null>(null);
  const [timelineItems, setTimelineItems] = useState<DecisionSignalItem[]>([]);
  const [timelineSearched, setTimelineSearched] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<ParsedApiError | null>(null);
  const [timelineTruncated, setTimelineTruncated] = useState(false);
  const [selectedOutcomes, setSelectedOutcomes] = useState<DecisionSignalOutcomeItem[]>([]);
  const [selectedOutcomesLoading, setSelectedOutcomesLoading] = useState(false);
  const [selectedOutcomesError, setSelectedOutcomesError] = useState<ParsedApiError | null>(null);
  const [selectedFeedback, setSelectedFeedback] = useState<DecisionSignalFeedbackItem | null>(null);
  const [selectedFeedbackLoading, setSelectedFeedbackLoading] = useState(false);
  const [selectedFeedbackError, setSelectedFeedbackError] = useState<ParsedApiError | null>(null);
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [reassessProfile, setReassessProfile] = useState<DecisionProfile>('balanced');
  const [reassessResponse, setReassessResponse] = useState<DecisionSignalReassessResponse | null>(null);
  const [reassessLoading, setReassessLoading] = useState(false);
  const [reassessPersisting, setReassessPersisting] = useState(false);
  const [reassessPersistConfirm, setReassessPersistConfirm] = useState(false);
  const [reassessPersistBlocked, setReassessPersistBlocked] = useState<DecisionSignalReassessBlockedError | null>(null);
  const [reassessError, setReassessError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);
  const statsRequestIdRef = useRef(0);
  const skillStatsRequestIdRef = useRef(0);
  const latestRequestIdRef = useRef(0);
  const timelineRequestIdRef = useRef(0);
  const detailRequestIdRef = useRef(0);
  const reassessRequestIdRef = useRef(0);
  const selectedSignalIdRef = useRef<number | null>(null);
  const statusUpdateInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const timelineMarketSourceRef = useRef<TimelineMarketSource>(null);

  // 「去重最新」打开、且列表没有任何活跃筛选时，主列表的查询（status=active、
  // pageSize=DEDUPE_PAGE_SIZE、holdingOnly）才与待处理区自己的宽查询等价
  // （DEDUPE_PAGE_SIZE 与 QUEUE_PAGE_SIZE 同为 100，条数上限也一致），
  // 此时复用主列表结果以避免对同一份数据发两次请求。
  // 只要存在活跃筛选（含 status='' 的「全部状态」与 sourceReportId 深链），主列表就会
  // 带上这些筛选而待处理区不会，两者不再等价：待处理区必须发自己的宽查询，否则
  // 「可以动手」会拿到被筛掉的、甚至已失效的信号。
  const reuseListForQueue = dedupeLatest && !hasActiveListFilters(appliedFilters);
  const queueItems = reuseListForQueue ? items : rawQueueItems;
  const queueLoading = reuseListForQueue ? loading : rawQueueLoading;
  const queueError = reuseListForQueue ? error : rawQueueError;

  const popularCandidates = useMemo(
    () => toPopularCandidates(stockIndex, STOCK_CANDIDATE_LIMIT),
    [stockIndex],
  );
  const stockCandidates = historyCandidates.length > 0 ? historyCandidates : popularCandidates;
  const stockCandidateMode: 'history' | 'popular' | 'empty' = historyCandidates.length > 0
    ? 'history'
    : stockCandidates.length > 0
      ? 'popular'
      : 'empty';

  useEffect(() => {
    document.title = t('decisionSignals.pageTitle');
  }, [t]);

  useEffect(() => {
    let mounted = true;
    void historyApi.getStockBarList({ limit: STOCK_CANDIDATE_LIMIT })
      .then((response) => {
        if (!mounted) return;
        const nextCandidates: StockCandidate[] = [];
        const seen = new Set<string>();
        for (const item of response.items) {
          const candidate = toHistoryCandidate(item);
          if (!candidate) continue;
          const key = getCandidateKey(candidate);
          if (seen.has(key)) continue;
          seen.add(key);
          nextCandidates.push(candidate);
          if (nextCandidates.length >= STOCK_CANDIDATE_LIMIT) break;
        }
        setHistoryCandidates(nextCandidates);
      })
      .catch(() => {
        if (mounted) setHistoryCandidates([]);
      })
      .finally(() => {
        if (mounted) setHistoryCandidatesLoaded(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const loadSignalsForPage = useCallback(async (nextPage: number) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    const effectivePageSize = dedupeLatest ? DEDUPE_PAGE_SIZE : PAGE_SIZE;
    const effectivePage = dedupeLatest ? 1 : nextPage;
    try {
      const response = await decisionSignalsApi.list(
        toListParams(appliedFilters, effectivePage, effectivePageSize, holdingOnly),
      );
      if (requestIdRef.current !== requestId) return;
      const lastPage = Math.max(1, Math.ceil(response.total / effectivePageSize));
      if (response.total > 0 && effectivePage > lastPage) {
        setPage(lastPage);
        return;
      }
      setItems(response.items);
      setTotal(response.total);
      setError(null);
      setSelected((current) => {
        if (!current) return current;
        if (current.source !== 'list') return current;
        const refreshed = response.items.find((item) => item.id === current.item.id);
        return refreshed ? { source: 'list', item: refreshed } : null;
      });
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setError(getParsedApiError(err));
      setItems([]);
      setTotal(0);
      setSelected((current) => (current?.source === 'list' ? null : current));
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [appliedFilters, dedupeLatest, holdingOnly]);

  const loadSignals = useCallback(async () => {
    await loadSignalsForPage(page);
  }, [loadSignalsForPage, page]);

  const loadOutcomeStats = useCallback(async () => {
    const requestId = statsRequestIdRef.current + 1;
    statsRequestIdRef.current = requestId;
    setStatsLoading(true);
    try {
      const response = await decisionSignalsApi.getOutcomeStats();
      if (statsRequestIdRef.current !== requestId) return;
      setOutcomeStats(response);
      setStatsError(null);
    } catch (err) {
      if (statsRequestIdRef.current !== requestId) return;
      setOutcomeStats(null);
      setStatsError(getParsedApiError(err));
    } finally {
      if (statsRequestIdRef.current === requestId) {
        setStatsLoading(false);
      }
    }
  }, []);

  const loadSkillOutcomeStats = useCallback(async () => {
    const requestId = skillStatsRequestIdRef.current + 1;
    skillStatsRequestIdRef.current = requestId;
    setSkillStatsLoading(true);
    try {
      const response = await decisionSignalsApi.getSkillOutcomeStats();
      if (skillStatsRequestIdRef.current !== requestId) return;
      setSkillStats(response);
      setSkillStatsError(null);
    } catch (err) {
      if (skillStatsRequestIdRef.current !== requestId) return;
      setSkillStats(null);
      setSkillStatsError(getParsedApiError(err));
    } finally {
      if (skillStatsRequestIdRef.current === requestId) {
        setSkillStatsLoading(false);
      }
    }
  }, []);

  // 待处理区要的是全貌，不能受主列表分页与筛选影响，因此单独拉最近 QUEUE_PAGE_SIZE 条
  // 启用中的信号在前端分档。只有主列表那次查询与这里等价（reuseListForQueue）时才跳过
  // 自己的请求去复用它；存在活跃筛选时这里必须照常发宽查询。
  const loadQueue = useCallback(async () => {
    if (reuseListForQueue) return;

    const requestId = queueRequestIdRef.current + 1;
    queueRequestIdRef.current = requestId;
    setRawQueueLoading(true);
    try {
      const response = await decisionSignalsApi.list({
        status: 'active',
        page: 1,
        pageSize: QUEUE_PAGE_SIZE,
        ...(holdingOnly ? { holdingOnly: true } : {}),
      });
      if (queueRequestIdRef.current !== requestId) return;
      setRawQueueItems(response.items);
      setRawQueueError(null);
    } catch (err) {
      if (queueRequestIdRef.current !== requestId) return;
      setRawQueueItems([]);
      setRawQueueError(getParsedApiError(err));
    } finally {
      if (queueRequestIdRef.current === requestId) {
        setRawQueueLoading(false);
      }
    }
  }, [holdingOnly, reuseListForQueue]);

  // 复用主列表时待处理区没有自己的请求（loadQueue 直接 return），刷新 / 重试入口必须
  // 落到主列表上，否则按钮保持可点击却没有反馈、也没有任何请求；其余情况照常刷新
  // 待处理区自己那次宽查询。
  const refreshQueue = useCallback(() => {
    if (reuseListForQueue) {
      void loadSignals();
      return;
    }
    void loadQueue();
  }, [loadQueue, loadSignals, reuseListForQueue]);

  const handleRunSkillOutcomes = useCallback(async () => {
    if (skillStatsRunning) return;
    setSkillStatsRunning(true);
    try {
      await decisionSignalsApi.runSkillOutcomes({});
      await loadSkillOutcomeStats();
    } catch (err) {
      const parsed = getParsedApiError(err);
      setSkillStatsError((current) => parsed ?? current);
    } finally {
      setSkillStatsRunning(false);
    }
  }, [loadSkillOutcomeStats, skillStatsRunning]);

  useEffect(() => {
    void loadSignals();
    return () => {
      requestIdRef.current += 1;
    };
  }, [loadSignals]);

  useEffect(() => {
    void loadQueue();
    return () => {
      queueRequestIdRef.current += 1;
    };
  }, [loadQueue]);

  // 统计区收起时不挂载也不请求，展开后才加载。
  useEffect(() => {
    if (!statsExpanded) return undefined;
    void loadOutcomeStats();
    return () => {
      statsRequestIdRef.current += 1;
    };
  }, [loadOutcomeStats, statsExpanded]);

  useEffect(() => {
    if (!statsExpanded) return undefined;
    void loadSkillOutcomeStats();
    return () => {
      skillStatsRequestIdRef.current += 1;
    };
  }, [loadSkillOutcomeStats, statsExpanded]);

  // spec §4.2：存在活跃筛选时自动展开。用 effect 而不是派生值
  // （如 `filtersExpanded || hasActiveListFilters(...)`），是因为派生值会让
  // Collapsible 的按钮在活跃筛选下永远无法收起——标题写着「收起筛选」却点不动。
  // 用 effect 只在活跃筛选「出现时」自动展开一次，之后用户可自由收起。
  useEffect(() => {
    if (hasActiveListFilters(appliedFilters)) {
      setFiltersExpanded(true);
    }
  }, [appliedFilters]);

  useEffect(() => () => {
    latestRequestIdRef.current += 1;
  }, []);

  useEffect(() => () => {
    timelineRequestIdRef.current += 1;
  }, []);

  // 卸载时作废 mutation 类在途请求（reassess/persist 共用 reassessRequestIdRef），
  // 避免 resolve 后执行卸载后 setState；handleStatusUpdate / handleFeedbackSubmit 再叠加 mounted 守卫。
  useEffect(() => {
    // Reset in setup: under React <StrictMode> dev double-mounting the cleanup runs
    // once and useRef(true) never re-initializes, which would otherwise leave
    // mountedRef.current === false and make handleStatusUpdate / handleFeedbackSubmit
    // silently skip their setState via the mounted guard.
    mountedRef.current = true;
    const cleanup = () => {
      mountedRef.current = false;
      reassessRequestIdRef.current += 1;
    };
    return cleanup;
  }, []);

  useEffect(() => {
    selectedSignalIdRef.current = selected?.item.id ?? null;
    if (!selected) {
      detailRequestIdRef.current += 1;
      setSelectedOutcomes([]);
      setSelectedOutcomesError(null);
      setSelectedFeedback(null);
      setSelectedFeedbackError(null);
      setSelectedOutcomesLoading(false);
      setSelectedFeedbackLoading(false);
      return;
    }

    const requestId = detailRequestIdRef.current + 1;
    detailRequestIdRef.current = requestId;
    // 切到另一个非空信号时，先清掉上一信号的 outcomes/feedback，避免
    // 新信号头部下闪一帧旧数据（如 persist-reassess 原地切换 id 的路径）。
    setSelectedOutcomes([]);
    setSelectedFeedback(null);
    setSelectedOutcomesLoading(true);
    setSelectedFeedbackLoading(true);
    setSelectedOutcomesError(null);
    setSelectedFeedbackError(null);

    void decisionSignalsApi.getSignalOutcomes(selected.item.id)
      .then((response) => {
        if (detailRequestIdRef.current !== requestId) return;
        setSelectedOutcomes(response.items);
      })
      .catch((err) => {
        if (detailRequestIdRef.current !== requestId) return;
        setSelectedOutcomes([]);
        setSelectedOutcomesError(getParsedApiError(err));
      })
      .finally(() => {
        if (detailRequestIdRef.current === requestId) {
          setSelectedOutcomesLoading(false);
        }
      });

    void decisionSignalsApi.getFeedback(selected.item.id)
      .then((response) => {
        if (detailRequestIdRef.current !== requestId) return;
        setSelectedFeedback(response);
      })
      .catch((err) => {
        if (detailRequestIdRef.current !== requestId) return;
        setSelectedFeedback(null);
        setSelectedFeedbackError(getParsedApiError(err));
      })
      .finally(() => {
        if (detailRequestIdRef.current === requestId) {
          setSelectedFeedbackLoading(false);
        }
      });
  }, [selected]);

  const appliedSourceReportId = parseSourceReportId(appliedFilters.sourceReportId);
  const selectedSourceReportId = selected?.item.sourceReportId ?? undefined;
  const reassessSourceReportId = selected ? selectedSourceReportId : appliedSourceReportId;
  const reassessContextKey = [
    reassessSourceReportId ?? '',
    reassessProfile,
  ].join(':');

  useEffect(() => {
    reassessRequestIdRef.current += 1;
    setReassessResponse(null);
    setReassessError(null);
    setReassessLoading(false);
    setReassessPersisting(false);
    setReassessPersistConfirm(false);
    setReassessPersistBlocked(null);
  }, [reassessContextKey]);

  const handleReassess = useCallback(async () => {
    if (!reassessSourceReportId) return;
    const requestId = reassessRequestIdRef.current + 1;
    reassessRequestIdRef.current = requestId;
    setReassessLoading(true);
    setReassessError(null);
    setReassessPersistBlocked(null);
    try {
      const response = await decisionSignalsApi.reassess({
        sourceReportId: reassessSourceReportId,
        decisionProfile: reassessProfile,
        persist: false,
      });
      if (reassessRequestIdRef.current !== requestId) return;
      setReassessResponse(response);
    } catch (err) {
      if (reassessRequestIdRef.current !== requestId) return;
      setReassessResponse(null);
      setReassessError(getParsedApiError(err));
    } finally {
      if (reassessRequestIdRef.current === requestId) {
        setReassessLoading(false);
      }
    }
  }, [reassessProfile, reassessSourceReportId]);

  const handleApplyFilters = (event: React.FormEvent) => {
    event.preventDefault();
    setAppliedFilters(filters);
    setPage(1);
  };

  const resetFilters = useCallback(() => {
    // 「重置」的语义是回到全量列表，因此不能再走 getInitialFilters()——那会连 URL 上的
    // ?sourceReportId= 一起读回来，让重置对该筛选无效。同时把深链参数摘掉，
    // 否则重新挂载（离开再返回、刷新）又会把它注入回来。
    clearSourceReportIdParam();
    setFilters(DEFAULT_LIST_FILTERS);
    setAppliedFilters(DEFAULT_LIST_FILTERS);
    setPage(1);
  }, []);

  const removeFilter = useCallback((key: keyof ListFilters) => {
    if (key === 'sourceReportId') {
      clearSourceReportIdParam();
    }
    setFilters((current) => ({ ...current, [key]: '' }));
    setAppliedFilters((current) => ({ ...current, [key]: '' }));
    setPage(1);
  }, []);

  const resetLatestView = useCallback(() => {
    latestRequestIdRef.current += 1;
    setLatestItems([]);
    setLatestSearched(false);
    setLatestLoading(false);
    setLatestError(null);
    setSelected((current) => (current?.source === 'latest' ? null : current));
  }, []);

  const loadLatestForContext = useCallback(async (context: StockContext) => {
    const stockCode = context.code.trim();
    if (!stockCode) return;
    const requestId = latestRequestIdRef.current + 1;
    latestRequestIdRef.current = requestId;
    setLatestLoading(true);
    setLatestError(null);
    setLatestSearched(true);
    setLatestItems([]);
    setSelected((current) => (current?.source === 'latest' ? null : current));
    try {
      const response = await decisionSignalsApi.getLatest(stockCode, {
        market: context.market,
        limit: 5,
      });
      if (latestRequestIdRef.current !== requestId) return;
      setLatestItems(response.items);
      setSelected((current) => refreshLatestSelection(current, response.items));
    } catch (err) {
      if (latestRequestIdRef.current !== requestId) return;
      setLatestItems([]);
      setSelected((current) => refreshLatestSelection(current, []));
      setLatestError(getParsedApiError(err));
    } finally {
      if (latestRequestIdRef.current === requestId) {
        setLatestLoading(false);
      }
    }
  }, []);

  const resetTimelineView = useCallback(() => {
    timelineRequestIdRef.current += 1;
    setTimelineItems([]);
    setTimelineSearched(false);
    setTimelineLoading(false);
    setTimelineError(null);
    setTimelineTruncated(false);
    setAppliedTimelineContext(null);
    setSelected((current) => (current?.source === 'timeline' ? null : current));
  }, []);

  const loadTimelineForContext = useCallback(async (
    context: StockContext,
    filtersSnapshot: TimelineFilters,
  ) => {
    const stockCode = context.code.trim();
    if (!stockCode) return;
    const requestId = timelineRequestIdRef.current + 1;
    timelineRequestIdRef.current = requestId;
    setTimelineLoading(true);
    setTimelineError(null);
    setTimelineSearched(true);
    setTimelineItems([]);
    setTimelineTruncated(false);
    setAppliedTimelineContext(null);
    setSelected((current) => (current?.source === 'timeline' ? null : current));
    const nextAppliedContext: AppliedTimelineContext = {
      ...filtersSnapshot,
      stockCode,
    };
    try {
      const response = await decisionSignalsApi.list(toTimelineParams(filtersSnapshot, stockCode));
      if (timelineRequestIdRef.current !== requestId) return;
      setAppliedTimelineContext(nextAppliedContext);
      setTimelineItems(response.items);
      setTimelineTruncated(response.total > response.items.length);
      setSelected((current) => refreshTimelineSelection(current, response.items));
    } catch (err) {
      if (timelineRequestIdRef.current !== requestId) return;
      setTimelineItems([]);
      setTimelineTruncated(false);
      setSelected((current) => refreshTimelineSelection(current, []));
      setTimelineError(getParsedApiError(err));
    } finally {
      if (timelineRequestIdRef.current === requestId) {
        setTimelineLoading(false);
      }
    }
  }, []);

  const handlePersistReassess = useCallback(async () => {
    const preview = reassessResponse?.preview;
    const guardrail = preview && isRecord(preview.metadata.guardrail_result)
      ? preview.metadata.guardrail_result
      : null;
    if (!reassessSourceReportId || !preview || guardrail?.passed !== true) return;

    const requestId = reassessRequestIdRef.current + 1;
    reassessRequestIdRef.current = requestId;
    setReassessPersistConfirm(false);
    setReassessPersisting(true);
    setReassessError(null);
    setReassessPersistBlocked(null);
    try {
      const response = await decisionSignalsApi.reassess({
        sourceReportId: reassessSourceReportId,
        decisionProfile: reassessProfile,
        persist: true,
      });
      if (reassessRequestIdRef.current !== requestId) return;
      if (!response.item || !response.persistStatus) {
        throw new Error('DecisionSignal reassess persist response item and persist_status are required');
      }
      const authoritativeItem = response.item;
      const shouldOptimisticallyUpsert = response.persistStatus !== 'existing';
      setReassessResponse(response);
      setSelected((current) => (
        current
          ? { source: 'persisted', item: authoritativeItem }
          : null
      ));
      if (
        shouldOptimisticallyUpsert
        &&
        activeStockContext
        && authoritativeItem.status === 'active'
        && itemMatchesStockContext(authoritativeItem, activeStockContext)
      ) {
        setLatestItems((current) => upsertDecisionSignal(current, authoritativeItem, 5));
        void loadLatestForContext(activeStockContext);
      }
      if (
        shouldOptimisticallyUpsert
        &&
        appliedTimelineContext
        && itemMatchesAppliedTimeline(authoritativeItem, appliedTimelineContext)
      ) {
        setTimelineItems((current) => upsertDecisionSignal(current, authoritativeItem));
        void loadTimelineForContext(
          {
            code: appliedTimelineContext.stockCode,
            market: appliedTimelineContext.market || undefined,
          },
          appliedTimelineContext,
        );
      }
      void loadSignalsForPage(page);
    } catch (err) {
      if (reassessRequestIdRef.current !== requestId) return;
      const blocked = getDecisionSignalReassessBlockedError(err);
      if (blocked) {
        setReassessPersistBlocked(blocked);
        setReassessError(null);
      } else {
        setReassessError(getParsedApiError(err));
      }
    } finally {
      if (reassessRequestIdRef.current === requestId) {
        setReassessPersisting(false);
      }
    }
  }, [
    activeStockContext,
    appliedTimelineContext,
    loadLatestForContext,
    loadSignalsForPage,
    loadTimelineForContext,
    page,
    reassessProfile,
    reassessResponse,
    reassessSourceReportId,
  ]);

  const latestSectionRef = useRef<HTMLDivElement | null>(null);

  const applyStockContext = useCallback((nextContext: StockContext) => {
    const nextTimeline = buildNextTimelineFilters(
      timelineFilters,
      activeStockContext,
      nextContext,
      timelineMarketSourceRef.current,
    );
    timelineMarketSourceRef.current = nextTimeline.marketSource;
    setActiveStockContext(nextContext);
    setStockDraft(nextContext.displayCode ?? nextContext.code);
    setTimelineFilters(nextTimeline.filters);
    void loadLatestForContext(nextContext);
    void loadTimelineForContext(nextContext, nextTimeline.filters);
    requestAnimationFrame(() => {
      latestSectionRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    });
  }, [activeStockContext, loadLatestForContext, loadTimelineForContext, timelineFilters]);

  const handleStockSubmit = useCallback((
    code: string,
    name?: string,
    _source?: 'manual' | 'autocomplete',
    metadata?: { market?: Market; displayCode?: string },
  ) => {
    const trimmedCode = code.trim();
    if (!trimmedCode) return;
    applyStockContext({
      code: trimmedCode,
      displayCode: metadata?.displayCode,
      name,
      market: normalizeDecisionSignalMarket(metadata?.market),
    });
  }, [applyStockContext]);

  const handleCandidateSelect = useCallback((candidate: StockCandidate) => {
    applyStockContext(candidate);
  }, [applyStockContext]);

  const handleStockFormSubmit = useCallback((code: string) => {
    if (draftMatchesStockContext(code, activeStockContext)) {
      applyStockContext(activeStockContext);
      return;
    }
    handleStockSubmit(code);
  }, [activeStockContext, applyStockContext, handleStockSubmit]);

  const handleClearStockContext = useCallback(() => {
    setStockDraft('');
    setActiveStockContext(null);
    timelineMarketSourceRef.current = null;
    setTimelineFilters((current) => ({ ...current, market: '' }));
    resetLatestView();
    resetTimelineView();
  }, [resetLatestView, resetTimelineView]);

  const handleTimelineSearch = useCallback((event: React.FormEvent) => {
    event.preventDefault();
    if (!activeStockContext) return;
    void loadTimelineForContext(activeStockContext, timelineFilters);
  }, [activeStockContext, loadTimelineForContext, timelineFilters]);

  const handleStatusUpdate = async () => {
    if (!pendingStatus || statusUpdateInFlightRef.current) return;
    statusUpdateInFlightRef.current = true;
    setStatusUpdating(true);
    try {
      const updated = await decisionSignalsApi.updateStatus(pendingStatus.item.id, {
        status: pendingStatus.status,
      });
      if (!mountedRef.current) return;
      setPendingStatus(null);
      setLatestItems((current) => current.flatMap((item) => {
        if (item.id !== updated.id) return [item];
        return updated.status === 'active' ? [updated] : [];
      }));
      setTimelineItems((current) => current.flatMap((item) => {
        if (item.id !== updated.id) return [item];
        return appliedTimelineContext?.status === 'active' && updated.status !== 'active' ? [] : [updated];
      }));
      setSelected((current) => {
        if (!current || current.item.id !== updated.id) return current;
        if (current.source === 'latest') {
          return updated.status === 'active' ? { source: 'latest', item: updated } : null;
        }
        if (current.source === 'timeline') {
          return appliedTimelineContext?.status === 'active' && updated.status !== 'active'
            ? null
            : { source: 'timeline', item: updated };
        }
        if (current.source === 'persisted') {
          return { source: 'persisted', item: updated };
        }
        if (current.source === 'queue') {
          return { source: 'queue', item: updated };
        }
        if (!parseSourceReportId(appliedFilters.sourceReportId) && appliedFilters.status && updated.status !== appliedFilters.status) return null;
        return { source: 'list', item: updated };
      });
      setError(null);
      await loadQueue();
      await loadSignalsForPage(page);
      // 统计区收起时不请求，与挂载、页面刷新按钮保持同一契约（spec §4.3）。
      if (statsExpanded) {
        await loadOutcomeStats();
      }
    } catch (err) {
      setError(getParsedApiError(err));
      setPendingStatus(null);
    } finally {
      setStatusUpdating(false);
      statusUpdateInFlightRef.current = false;
    }
  };

  const handleFeedbackSubmit = useCallback(async (feedbackValue: DecisionSignalFeedbackValue) => {
    if (!selected || feedbackSaving) return;
    const signalId = selected.item.id;
    setFeedbackSaving(true);
    try {
      const updated = await decisionSignalsApi.putFeedback(signalId, {
        feedbackValue,
        source: 'web',
      });
      if (!mountedRef.current || selectedSignalIdRef.current !== signalId) return;
      setSelectedFeedback(updated);
      setSelectedFeedbackError(null);
    } catch (err) {
      if (!mountedRef.current || selectedSignalIdRef.current !== signalId) return;
      setSelectedFeedbackError(getParsedApiError(err));
    } finally {
      setFeedbackSaving(false);
    }
  }, [feedbackSaving, selected]);

  const renderReassessPanel = () => {
    const preview = reassessResponse?.preview ?? null;
    const persistedItem = reassessResponse?.item ?? null;
    const persistStatus = reassessResponse?.persistStatus ?? null;
    const terminalExisting = persistStatus === 'existing' && persistedItem?.status !== 'active';
    const persistedAlertVariant = terminalExisting
      ? 'warning'
      : persistStatus === 'existing'
        ? 'info'
        : 'success';
    const persistedTitleKey: UiTextKey = terminalExisting
      ? 'decisionSignals.reassessPersistedTerminalTitle'
      : persistStatus === 'existing'
        ? 'decisionSignals.reassessPersistedExistingTitle'
        : persistStatus === 'refreshed'
          ? 'decisionSignals.reassessPersistedRefreshedTitle'
          : 'decisionSignals.reassessPersistedCreatedTitle';
    const persistedMessageKey: UiTextKey = terminalExisting
      ? 'decisionSignals.reassessPersistedTerminalExisting'
      : persistStatus === 'existing'
        ? 'decisionSignals.reassessPersistedExisting'
        : persistStatus === 'refreshed'
          ? 'decisionSignals.reassessPersistedRefreshed'
          : 'decisionSignals.reassessPersistedCreated';
    const metadata = preview?.metadata ?? {};
    const guardrail = isRecord(metadata.guardrail_result) ? metadata.guardrail_result : null;
    const rawAction = typeof guardrail?.raw_action === 'string' ? guardrail.raw_action : null;
    const finalAction = typeof guardrail?.final_action === 'string' ? guardrail.final_action : null;
    const passed = typeof guardrail?.passed === 'boolean' ? guardrail.passed : null;
    return (
      <div className="rounded-xl border border-border/60 bg-elevated/30 p-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">{t('decisionSignals.reassessTitle')}</h3>
            </div>
            <p className="mt-1 text-xs text-secondary-text">
              {reassessSourceReportId
                ? t('decisionSignals.reassessSource', { id: reassessSourceReportId })
                : t('decisionSignals.reassessUnsupported')}
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Select
              value={reassessProfile}
              onChange={(value) => setReassessProfile(value as DecisionProfile)}
              options={REASSESS_PROFILES.map((profile) => ({
                value: profile,
                label: t(`decisionSignals.profile.${profile}` as UiTextKey),
              }))}
              placeholder={t('decisionSignals.reassessProfile')}
              disabled={!reassessSourceReportId || reassessLoading || reassessPersisting}
            />
            <button
              type="button"
              className="btn-secondary inline-flex h-10 items-center justify-center gap-2"
              onClick={() => void handleReassess()}
              disabled={!reassessSourceReportId || reassessLoading || reassessPersisting}
            >
              <RefreshCw className={cn('h-4 w-4', reassessLoading ? 'animate-spin' : '')} />
              {t('decisionSignals.reassessPreview')}
            </button>
          </div>
        </div>

        {!reassessSourceReportId ? (
          <InlineAlert
            className="mt-3"
            variant="warning"
            title={t('decisionSignals.reassessUnsupportedTitle')}
            message={t('decisionSignals.reassessUnsupported')}
          />
        ) : null}
        {reassessError ? <ApiErrorAlert className="mt-3" error={reassessError} /> : null}
        {reassessPersistBlocked ? (
          <div className="mt-3 space-y-2">
            <InlineAlert
              variant="danger"
              title={t('decisionSignals.reassessPersistBlockedTitle')}
              message={reassessPersistBlocked.blockedReason}
            />
            {reassessPersistBlocked.warnings.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm text-secondary-text">
                {reassessPersistBlocked.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>{warning.message || warning.code}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {persistedItem ? (
          <InlineAlert
            className="mt-3"
            variant={persistedAlertVariant}
            title={t(persistedTitleKey)}
            message={t(
              persistedMessageKey,
              {
                id: persistedItem.id,
                status: t(STATUS_LABEL_KEYS[persistedItem.status]),
              },
            )}
          />
        ) : null}
        {preview ? (
          <div className="mt-4 space-y-3">
            {reassessResponse?.blockedReason ? (
              <InlineAlert
                variant="warning"
                title={t('decisionSignals.reassessBlockedTitle')}
                message={reassessResponse.blockedReason}
              />
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.action')}</p>
                <p className="mt-1 text-sm font-semibold text-foreground">{actionLabels[preview.action]}</p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.score')}</p>
                <p className="mt-1 text-sm font-semibold text-foreground">{preview.score ?? '-'}</p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.confidence')}</p>
                <p className="mt-1 text-sm font-semibold text-foreground">{preview.confidence ?? '-'}</p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.horizon')}</p>
                <p className="mt-1 text-sm font-semibold text-foreground">{preview.horizon ?? '-'}</p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.entryRange')}</p>
                <p className="mt-1 text-sm text-foreground">
                  {preview.entryLow || preview.entryHigh
                    ? `${preview.entryLow ?? '-'} ~ ${preview.entryHigh ?? '-'}`
                    : '-'}
                </p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.stopLoss')}</p>
                <p className="mt-1 text-sm text-foreground">{preview.stopLoss ?? '-'}</p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.targetPrice')}</p>
                <p className="mt-1 text-sm text-foreground">{preview.targetPrice ?? '-'}</p>
              </div>
              <div className="rounded-lg border border-border/50 bg-background/40 p-3">
                <p className="text-xs text-secondary-text">{t('decisionSignals.reassessRawFinal')}</p>
                <p className="mt-1 text-sm text-foreground">{rawAction ?? '-'} {'->'} {finalAction ?? '-'}</p>
              </div>
            </div>
            <div className="space-y-2 text-sm text-secondary-text">
              {passed === false ? (
                <p className="font-medium text-warning">{t('decisionSignals.reassessBlockedNote')}</p>
              ) : null}
              {preview.invalidation ? <p><span className="text-foreground">{t('decisionSignals.invalidation')}:</span> {preview.invalidation}</p> : null}
              {preview.reason ? <p><span className="text-foreground">{t('decisionSignals.reason')}:</span> {preview.reason}</p> : null}
              {preview.riskSummary ? <p><span className="text-foreground">{t('decisionSignals.riskSummary')}:</span> {preview.riskSummary}</p> : null}
              {preview.watchConditions ? <p><span className="text-foreground">{t('decisionSignals.watchConditions')}:</span> {preview.watchConditions}</p> : null}
            </div>
            {reassessResponse?.warnings.length ? (
              <div className="rounded-lg border border-warning/30 bg-warning/10 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-warning">{t('decisionSignals.reassessWarnings')}</p>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-secondary-text">
                  {reassessResponse.warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>{warning.message || warning.code}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {passed === true ? (
              <div className="flex justify-end">
                <button
                  type="button"
                  className="btn-primary inline-flex h-10 items-center justify-center gap-2"
                  onClick={() => setReassessPersistConfirm(true)}
                  disabled={reassessLoading || reassessPersisting}
                >
                  <ShieldCheck className="h-4 w-4" />
                  {reassessPersisting
                    ? t('decisionSignals.reassessPersisting')
                    : t('decisionSignals.reassessPersist')}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
        {persistedItem && reassessResponse?.warnings.length ? (
          <div className="mt-3 rounded-lg border border-warning/30 bg-warning/10 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-warning">{t('decisionSignals.reassessWarnings')}</p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-secondary-text">
              {reassessResponse.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>{warning.message || warning.code}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    );
  };

  const activeStockLabel = activeStockContext
    ? [
      activeStockContext.displayCode ?? activeStockContext.code,
      // 名称与代码指向同一标的时不再重复（否则会拼出「MARKET / MARKET / 大盘」）。
      isStockCodeRedundantWithName(activeStockContext.name, activeStockContext.code)
        ? null
        : activeStockContext.name,
      activeStockContext.market,
    ].filter(Boolean).join(' / ')
    : null;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const visibleItems = useMemo(() => (dedupeLatest ? dedupeByLatest(items) : items), [dedupeLatest, items]);
  const activeFilterChips = buildActiveFilterChips(appliedFilters, actionLabels, t);
  // 任何一个 bucket 达标就渲染表格；全是未达标样本时表格每行指标都是 `-`，改为展示进度。
  const hasSufficientSkillBucket = skillStats?.buckets.some((bucket) => bucket.sampleSufficient) ?? false;
  const skillStatsProgress = summarizeSkillStatsProgress(skillStats?.buckets ?? []);
  // 页面刷新按钮的禁用与转圈必须同源：disabled 覆盖主列表、待处理区、统计与单股追踪的
  // 在途状态，icon 若只看 loading，就会出现「按钮已灰却不转」的假死观感。
  const pageRefreshing = loading || queueLoading || statsLoading || skillStatsLoading || latestLoading || timelineLoading;

  return (
    <AppPage>
      <div className="space-y-5">
        <div className="flex items-start justify-between gap-3">
          <p className="max-w-2xl text-sm text-secondary-text">{t('decisionSignals.pagePurpose')}</p>
          <div className="flex shrink-0 justify-end">
            <button
              type="button"
              className="btn-secondary inline-flex items-center justify-center gap-2"
              onClick={() => {
                void loadSignals();
                void loadQueue();
                // 统计区收起时不请求，避免收起状态下的无谓请求与按钮自锁。
                if (statsExpanded) {
                  void loadOutcomeStats();
                  void loadSkillOutcomeStats();
                }
                // 单股追踪只在用户已经应用了当前股票后才刷新，避免凭空发起
                // latest / 时间线查询；时间线额外要求已经查询过一次，保持
                // 「未选股不查询」的既有契约。
                if (activeStockContext) {
                  void loadLatestForContext(activeStockContext);
                  if (appliedTimelineContext) {
                    void loadTimelineForContext(
                      {
                        code: appliedTimelineContext.stockCode,
                        market: appliedTimelineContext.market || undefined,
                      },
                      appliedTimelineContext,
                    );
                  }
                }
              }}
              disabled={pageRefreshing}
            >
              <RefreshCw className={cn('h-4 w-4', pageRefreshing ? 'animate-spin' : '')} />
              <span>{t('decisionSignals.refresh')}</span>
            </button>
          </div>
        </div>

        <Card title={t('decisionSignals.queueTitle')} subtitle={t('decisionSignals.queueDescription')} padding="md">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <Checkbox
              label={t('decisionSignals.holdingOnlyLabel')}
              checked={holdingOnly}
              onChange={(event) => setHoldingOnly(event.target.checked)}
            />
            <button
              type="button"
              className="btn-secondary inline-flex h-9 items-center justify-center gap-2"
              onClick={refreshQueue}
              disabled={queueLoading}
              aria-label={t('decisionSignals.queueRefreshAria')}
            >
              <RefreshCw className={cn('h-4 w-4', queueLoading ? 'animate-spin' : '')} />
              {t('decisionSignals.queueRefresh')}
            </button>
          </div>
          <DecisionSignalActionQueue
            items={queueItems}
            loading={queueLoading}
            error={queueError}
            onRetry={refreshQueue}
            onSelect={(item) => setSelected({ source: 'queue', item })}
            selectedId={selected?.item.id ?? null}
          />
          {queueItems.length >= QUEUE_PAGE_SIZE ? (
            <p className="pt-3 text-xs text-secondary-text">
              {t('decisionSignals.queueTruncatedNote', { fetched: QUEUE_PAGE_SIZE })}
            </p>
          ) : null}
        </Card>

        <p className="text-xs font-semibold uppercase tracking-wide text-muted-text">{t('decisionSignals.sectionAll')}</p>

        <Collapsible
          title={t(filtersExpanded ? 'decisionSignals.filterToggleHide' : 'decisionSignals.filterToggleShow')}
          open={filtersExpanded}
          onOpenChange={setFiltersExpanded}
        >
          {filtersExpanded ? (
            <Card title={t('decisionSignals.filter')} padding="md">
              <form className="grid gap-3 md:grid-cols-3 xl:grid-cols-7" onSubmit={handleApplyFilters}>
                <Select
                  value={filters.market}
                  onChange={(value) => setFilters((current) => ({ ...current, market: value as ListFilters['market'] }))}
                  options={[
                    { value: '', label: t('decisionSignals.allMarkets') },
                    ...MARKET_OPTIONS.map((market) => ({ value: market, label: getDecisionSignalMarketLabel(market, t) })),
                  ]}
                  placeholder={t('decisionSignals.market')}
                  className="w-full"
                />
                <input
                  className={SELECT_INPUT_CLASS}
                  value={filters.stockCode}
                  onChange={(event) => setFilters((current) => ({ ...current, stockCode: event.target.value }))}
                  placeholder={t('decisionSignals.stockCode')}
                  aria-label={t('decisionSignals.stockCode')}
                />
                <Select
                  value={filters.action}
                  onChange={(value) => setFilters((current) => ({ ...current, action: value as ListFilters['action'] }))}
                  options={[
                    { value: '', label: t('decisionSignals.allActions') },
                    ...ACTION_OPTIONS.map((action) => ({ value: action, label: actionLabels[action] })),
                  ]}
                  placeholder={t('decisionSignals.action')}
                  className="w-full"
                />
                <Select
                  value={filters.marketPhase}
                  onChange={(value) => setFilters((current) => ({ ...current, marketPhase: value as ListFilters['marketPhase'] }))}
                  options={[
                    { value: '', label: t('decisionSignals.allPhases') },
                    ...PHASE_OPTIONS.map((phase) => ({ value: phase, label: getDecisionSignalMarketPhaseLabel(phase, t) })),
                  ]}
                  placeholder={t('decisionSignals.marketPhase')}
                  className="w-full"
                />
                <Select
                  value={filters.sourceType}
                  onChange={(value) => setFilters((current) => ({ ...current, sourceType: value as ListFilters['sourceType'] }))}
                  options={[
                    { value: '', label: t('decisionSignals.allSources') },
                    ...SOURCE_OPTIONS.map((source) => ({ value: source, label: getDecisionSignalSourceTypeLabel(source, t) })),
                  ]}
                  placeholder={t('decisionSignals.source')}
                  className="w-full"
                />
                <input
                  className={SELECT_INPUT_CLASS}
                  value={filters.sourceReportId}
                  onChange={(event) => setFilters((current) => ({ ...current, sourceReportId: event.target.value }))}
                  placeholder={t('decisionSignals.sourceReportId')}
                  aria-label={t('decisionSignals.sourceReportId')}
                  inputMode="numeric"
                  min={1}
                  step={1}
                  type="number"
                />
                <Select
                  value={filters.status}
                  onChange={(value) => setFilters((current) => ({ ...current, status: value as ListFilters['status'] }))}
                  options={[
                    { value: '', label: t('decisionSignals.allStatuses') },
                    ...STATUS_OPTIONS.map((status) => ({ value: status, label: t(STATUS_LABEL_KEYS[status]) })),
                  ]}
                  placeholder={t('decisionSignals.status')}
                  className="w-full"
                />
                <button type="submit" className="btn-primary inline-flex h-11 items-center justify-center gap-2">
                  <Search className="h-4 w-4" />
                  {t('decisionSignals.filter')}
                </button>
                <button
                  type="button"
                  className="btn-secondary inline-flex h-11 items-center justify-center gap-2"
                  onClick={resetFilters}
                >
                  {t('decisionSignals.resetFilter')}
                </button>
              </form>
              <label className="mt-3 inline-flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={dedupeLatest}
                  onChange={(event) => {
                    setDedupeLatest(event.target.checked);
                    setPage(1);
                  }}
                  className="h-4 w-4 accent-primary"
                />
                <span className="text-sm text-foreground">{t('decisionSignals.dedupeLabel')}</span>
              </label>
            </Card>
          ) : null}
        </Collapsible>

        {!selected && appliedSourceReportId ? (
          <Card padding="md">
            {renderReassessPanel()}
          </Card>
        ) : null}

        {/* 列表加载失败是页面级提示，送全局右上角容器。 */}
        {error ? (
          <ToastPortal>
            <ApiErrorAlert
              elevated
              error={{ ...error, title: t('decisionSignals.errorTitle') }}
              actionLabel={t('common.retry')}
              onAction={() => void loadSignals()}
              className="pointer-events-auto"
            />
          </ToastPortal>
        ) : null}

        <div className="flex items-center justify-between gap-3">
          {dedupeLatest ? (
            <p className="text-sm text-secondary-text">{t('decisionSignals.dedupeCount', { count: visibleItems.length })}</p>
          ) : (
            <p className="text-sm text-secondary-text">{t('decisionSignals.total', { total })}</p>
          )}
          {loading ? <span className="text-xs text-secondary-text">{t('common.loading')}...</span> : null}
        </div>

        {activeFilterChips.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            {activeFilterChips.map((chip) => (
              <span key={chip.key} className="inline-flex items-center gap-1 rounded-full border border-border/70 bg-elevated/40 px-2.5 py-1 text-xs text-secondary-text">
                {chip.label}
                <button
                  type="button"
                  onClick={() => removeFilter(chip.key)}
                  className="text-secondary-text transition-colors hover:text-danger"
                  aria-label={`${t('decisionSignals.resetFilter')} ${chip.label}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            <button type="button" onClick={resetFilters} className="text-xs text-secondary-text underline hover:text-foreground">
              {t('decisionSignals.resetFilter')}
            </button>
          </div>
        ) : null}

        {!loading && visibleItems.length === 0 ? (
          <EmptyState
            title={t('decisionSignals.emptyTitle')}
            description={t('decisionSignals.emptyDescription')}
            icon={<Activity className="h-7 w-7" />}
          />
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {visibleItems.map((item) => (
              <DecisionSignalCard
                key={item.id}
                item={item}
                onSelect={(selectedItem) => setSelected({ source: 'list', item: selectedItem })}
                selected={selected?.item.id === item.id}
              />
            ))}
          </div>
        )}

        {dedupeLatest ? (
          <p className="text-xs text-secondary-text">{t('decisionSignals.dedupeNote', { fetched: items.length })}</p>
        ) : (
          <Pagination currentPage={page} totalPages={totalPages} onPageChange={setPage} />
        )}

        <p className="text-xs font-semibold uppercase tracking-wide text-muted-text">{t('decisionSignals.sectionStock')}</p>

        <Card title={t('decisionSignals.stockContextTitle')} subtitle={t('decisionSignals.stockContextDescription')} padding="md">
          <form
            className="flex flex-col gap-3 md:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              handleStockFormSubmit(stockDraft);
            }}
          >
            <div className="min-w-0 flex-1">
              <StockAutocomplete
                value={stockDraft}
                onChange={setStockDraft}
                onSubmit={handleStockSubmit}
                placeholder={t('decisionSignals.stockContextPlaceholder')}
                ariaLabel={t('decisionSignals.stockContextInput')}
              />
            </div>
            <button
              type="submit"
              className="btn-primary inline-flex h-11 items-center justify-center gap-2"
              disabled={!stockDraft.trim()}
            >
              <Search className="h-4 w-4" />
              {t('decisionSignals.stockContextApply')}
            </button>
            <button
              type="button"
              className="btn-secondary inline-flex h-11 items-center justify-center gap-2"
              onClick={handleClearStockContext}
              disabled={!activeStockContext && !stockDraft}
            >
              {t('decisionSignals.stockContextClear')}
            </button>
          </form>

          {activeStockLabel ? (
            <p className="mt-3 text-sm text-secondary-text">
              {t('decisionSignals.stockContextCurrent', { stock: activeStockLabel })}
            </p>
          ) : (
            <p className="mt-3 text-sm text-secondary-text">{t('decisionSignals.stockContextEmpty')}</p>
          )}

          {historyCandidatesLoaded && stockCandidates.length > 0 ? (
            <div className="mt-4">
              <p className="text-xs font-medium uppercase text-muted-text">
                {stockCandidateMode === 'history'
                  ? t('decisionSignals.stockContextRecent')
                  : t('decisionSignals.stockContextPopular')}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {stockCandidates.map((candidate) => (
                  <button
                    key={`${candidate.source}:${getCandidateKey(candidate)}`}
                    type="button"
                    className="rounded-full border border-border/70 bg-elevated/40 px-3 py-1.5 text-sm text-foreground transition-colors hover:border-primary/60 hover:text-primary"
                    onClick={() => handleCandidateSelect(candidate)}
                  >
                    <span className="font-mono">{candidate.displayCode ?? candidate.code}</span>
                    {candidate.name ? <span className="ml-1 text-secondary-text">{candidate.name}</span> : null}
                    {candidate.market ? <span className="ml-1 text-muted-text">/ {candidate.market}</span> : null}
                  </button>
                ))}
              </div>
            </div>
          ) : historyCandidatesLoaded ? (
            <p className="mt-4 text-sm text-secondary-text">{t('decisionSignals.stockContextNoCandidates')}</p>
          ) : null}
        </Card>

        <div ref={latestSectionRef}>
        <Card title={t('decisionSignals.latestTitle')} subtitle={t('decisionSignals.latestDescription')} padding="md">
          {!activeStockContext ? (
            <EmptyState
              className="border-none bg-transparent py-6 shadow-none"
              title={t('decisionSignals.stockContextGuideTitle')}
              description={t('decisionSignals.stockContextGuideDescription')}
              icon={<Activity className="h-6 w-6" />}
            />
          ) : null}
          {latestError ? <ApiErrorAlert className="mt-3" error={latestError} /> : null}
          {latestSearched && !latestLoading && !latestError && latestItems.length === 0 ? (
            <EmptyState
              className="mt-4 border-none bg-transparent py-6 shadow-none"
              title={t('decisionSignals.noLatestTitle')}
              description={t('decisionSignals.noLatestDescription')}
              icon={<Activity className="h-6 w-6" />}
            />
          ) : null}
          {latestLoading ? <p className="mt-3 text-sm text-secondary-text">{t('common.loading')}...</p> : null}
          {latestItems.length > 0 ? (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {latestItems.map((item) => (
                <DecisionSignalCard
                  key={item.id}
                  item={item}
                  onSelect={(selectedItem) => setSelected({ source: 'latest', item: selectedItem })}
                  selected={selected?.item.id === item.id}
                />
              ))}
            </div>
          ) : null}
        </Card>
        </div>

        <Card title={t('decisionSignals.timelineTitle')} subtitle={t('decisionSignals.timelineDescription')} padding="md">
          <form className="grid gap-3 md:grid-cols-5" onSubmit={handleTimelineSearch}>
            <Select
              value={timelineFilters.market}
              onChange={(value) => {
                const market = value as TimelineFilters['market'];
                timelineMarketSourceRef.current = market ? 'user' : null;
                setTimelineFilters((current) => ({ ...current, market }));
              }}
              options={[
                { value: '', label: t('decisionSignals.allMarkets') },
                ...MARKET_OPTIONS.map((market) => ({ value: market, label: getDecisionSignalMarketLabel(market, t) })),
              ]}
              placeholder={t('decisionSignals.timelineMarket')}
              className="w-full"
            />
            <Select
              value={timelineFilters.range}
              onChange={(value) => setTimelineFilters((current) => ({ ...current, range: value as TimelineRange }))}
              options={[
                { value: '30d', label: t('decisionSignals.timelineRange.30d') },
                { value: '90d', label: t('decisionSignals.timelineRange.90d') },
                { value: '180d', label: t('decisionSignals.timelineRange.180d') },
              ]}
              placeholder={t('decisionSignals.timelineRange')}
              className="w-full"
            />
            <Select
              value={timelineFilters.status}
              onChange={(value) => setTimelineFilters((current) => ({ ...current, status: value as TimelineStatusFilter }))}
              options={[
                { value: 'all', label: t('decisionSignals.timelineStatus.all') },
                { value: 'active', label: t('decisionSignals.timelineStatus.active') },
              ]}
              placeholder={t('decisionSignals.timelineStatus')}
              className="w-full"
            />
            <Select
              value={timelineFilters.decisionProfile}
              onChange={(value) => setTimelineFilters((current) => ({
                ...current,
                decisionProfile: value as TimelineFilters['decisionProfile'],
              }))}
              options={[
                { value: '', label: t('decisionSignals.allProfiles') },
                ...REASSESS_PROFILES.map((profile) => ({
                  value: profile,
                  label: t(`decisionSignals.profile.${profile}` as UiTextKey),
                })),
                { value: 'unknown', label: t('decisionSignals.profile.unknown') },
              ]}
              placeholder={t('decisionSignals.timelineProfile')}
              className="w-full"
            />
            <button
              type="submit"
              className="btn-secondary inline-flex h-11 items-center justify-center gap-2"
              disabled={timelineLoading || !activeStockContext?.code}
            >
              <Search className="h-4 w-4" />
              {t('decisionSignals.timelineSearch')}
            </button>
          </form>
          <div className="mt-4">
            {!activeStockContext ? (
              <p className="text-sm text-secondary-text">{t('decisionSignals.timelineNoStockHint')}</p>
            ) : !timelineSearched ? (
              <EmptyState
                className="border-none bg-transparent py-6 shadow-none"
                title={t('decisionSignals.timelineGuideTitle')}
                description={t('decisionSignals.timelineGuideDescription')}
                icon={<Activity className="h-6 w-6" />}
              />
            ) : (
              <DecisionSignalTimeline
                items={timelineItems}
                selectedId={selected?.item.id ?? null}
                loading={timelineLoading}
                error={timelineError?.message ?? null}
                truncated={timelineTruncated}
                onSelect={(selectedItem) => setSelected({ source: 'timeline', item: selectedItem })}
              />
            )}
          </div>
        </Card>

        <Collapsible
          title={t('decisionSignals.statsToggleTitle')}
          open={statsExpanded}
          onOpenChange={setStatsExpanded}
          // 统计区含 skill × 窗口表，桶数多时行数会超过 Collapsible 默认的 2000px
          // 上限（默认上限只在展开态生效，且没有滚动容器），因此这里显式改成滚动面板。
          scrollable
        >
          <div className="space-y-4">
            {statsExpanded ? (
              <>
                <Card title={t('decisionSignals.statsTitle')} subtitle={t('decisionSignals.statsDescription')} padding="md">
                  <p className="mb-3 text-sm text-secondary-text">{t('decisionSignals.statsGlobalScope')}</p>
                  {statsError ? (
                    <ApiErrorAlert
                      error={{ ...statsError, title: t('decisionSignals.statsErrorTitle') }}
                      actionLabel={t('common.retry')}
                      onAction={() => void loadOutcomeStats()}
                    />
                  ) : statsLoading ? (
                    <p className="text-sm text-secondary-text">{t('common.loading')}...</p>
                  ) : outcomeStats && outcomeStats.total > 0 ? (
                    <div>
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                        <div className="rounded-xl border border-border/60 bg-elevated/40 px-3 py-3">
                          <p className="text-xs text-secondary-text">{t('decisionSignals.statsTotal')}</p>
                          <p className="mt-1 text-2xl font-semibold text-foreground">{outcomeStats.total}</p>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-elevated/40 px-3 py-3">
                          <p className="text-xs text-secondary-text">{t('decisionSignals.statsHitRate')}</p>
                          <p className="mt-1 text-2xl font-semibold text-success">{formatStatPercent(outcomeStats.hitRatePct)}</p>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-elevated/40 px-3 py-3">
                          <p className="text-xs text-secondary-text">{t('decisionSignals.outcome.hit')}</p>
                          <p className="mt-1 text-2xl font-semibold text-success">{outcomeStats.hit}</p>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-elevated/40 px-3 py-3">
                          <p className="text-xs text-secondary-text">{t('decisionSignals.outcome.miss')}</p>
                          <p className="mt-1 text-2xl font-semibold text-danger">{outcomeStats.miss}</p>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-elevated/40 px-3 py-3">
                          <p className="text-xs text-secondary-text">{t('decisionSignals.outcome.unable')}</p>
                          <p className="mt-1 text-2xl font-semibold text-warning">{outcomeStats.unable}</p>
                        </div>
                      </div>
                      <p className="mt-3 text-xs text-secondary-text">{t('decisionSignals.statsLegend')}</p>
                      {outcomeStats.profileCalibration ? (
                        <DecisionSignalProfileCalibration calibration={outcomeStats.profileCalibration} />
                      ) : null}

                      {outcomeStats.breakdowns && Object.keys(outcomeStats.breakdowns).length > 0 ? (
                        <div className="mt-4 space-y-4">
                          {Object.entries(outcomeStats.breakdowns).map(([dimension, buckets]) => (
                            <div key={dimension}>
                              <p className="mb-1.5 text-xs font-semibold text-secondary-text">
                                {dimension === 'horizon'
                                  ? t('decisionSignals.statsByHorizon')
                                  : dimension === 'status'
                                    ? t('decisionSignals.statsByStatus')
                                    : dimension}
                              </p>
                              <div className="space-y-1">
                                {buckets.map((bucket) => (
                                  <div
                                    key={`${dimension}-${bucket.value}`}
                                    className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto_auto] items-center gap-x-4 rounded-lg border border-border/50 bg-elevated/25 px-3 py-1.5 text-xs"
                                  >
                                    <span className="min-w-0 truncate font-medium text-foreground">
                                      {dimension === 'horizon'
                                        ? getDecisionSignalHorizonLabel(bucket.value as DecisionSignalHorizon, t)
                                        : dimension === 'status'
                                          ? t(STATUS_LABEL_KEYS[bucket.value as DecisionSignalStatus])
                                          : bucket.value}
                                    </span>
                                    <span className="text-secondary-text">{bucket.total}</span>
                                    <span className="text-success">{bucket.hit}</span>
                                    <span className="text-danger">{bucket.miss}</span>
                                    <span className="text-warning">{bucket.unable}</span>
                                    <span className="tabular-nums text-secondary-text">
                                      {bucket.hitRatePct != null ? `${bucket.hitRatePct.toFixed(1)}%` : '-'}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <EmptyState
                      className="border-none bg-transparent py-6 shadow-none"
                      title={t('decisionSignals.noReviewedStatsTitle')}
                      description={t('decisionSignals.noReviewedStatsDescription')}
                      icon={<BarChart3 className="h-6 w-6" />}
                    />
                  )}
                </Card>

                <Card
                  title={t('decisionSignals.skillStatsTitle')}
                  subtitle={t('decisionSignals.skillStatsDescription')}
                  padding="md"
                >
                  <div className="mb-3 flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                    <p className="text-sm text-secondary-text">
                      {t('decisionSignals.skillStatsSampleSize')}: {skillStats?.minimumEvaluatedSampleSize ?? '-'}
                    </p>
                    <div className="flex shrink-0 gap-2">
                      <button
                        type="button"
                        className="btn-secondary inline-flex h-10 items-center justify-center gap-2"
                        onClick={() => void loadSkillOutcomeStats()}
                        disabled={skillStatsLoading}
                        aria-label={t('decisionSignals.skillStatsRefreshAria')}
                      >
                        <RefreshCw className={cn('h-4 w-4', skillStatsLoading ? 'animate-spin' : '')} />
                        {t('decisionSignals.skillStatsRefresh')}
                      </button>
                      <button
                        type="button"
                        className="btn-primary inline-flex h-10 items-center justify-center gap-2"
                        onClick={() => void handleRunSkillOutcomes()}
                        disabled={skillStatsLoading || skillStatsRunning}
                      >
                        <Activity className={cn('h-4 w-4', skillStatsRunning ? 'animate-spin' : '')} />
                        {skillStatsRunning
                          ? t('common.loading')
                          : t('decisionSignals.skillStatsRun')}
                      </button>
                    </div>
                  </div>

                  {skillStatsError ? (
                    <ApiErrorAlert
                      error={{ ...skillStatsError, title: t('decisionSignals.skillStatsErrorTitle') }}
                      actionLabel={t('common.retry')}
                      onAction={() => void loadSkillOutcomeStats()}
                    />
                  ) : skillStatsLoading ? (
                    <p className="text-sm text-secondary-text">{t('common.loading')}...</p>
                  ) : skillStats && hasSufficientSkillBucket ? (
                    <div className="space-y-1">
                      <div className="grid grid-cols-[minmax(0,1.6fr)_auto_auto_auto_auto_auto_auto_auto_auto_auto] items-center gap-x-4 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-text">
                        <span>{t('decisionSignals.skillStatsTitle')}</span>
                        <span>{t('decisionSignals.skillStatsPending')}</span>
                        <span>{t('decisionSignals.skillStatsEvaluated')}</span>
                        <span>{t('decisionSignals.skillStatsTotal')}</span>
                        <span>{t('decisionSignals.outcome.hit')}</span>
                        <span>{t('decisionSignals.outcome.miss')}</span>
                        <span>{t('decisionSignals.skillStatsHitRate')}</span>
                        <span>{t('decisionSignals.skillStatsAvgReturn')}</span>
                        <span>{t('decisionSignals.skillStatsSampleStatus')}</span>
                      </div>
                      {skillStats.buckets.map((bucket) => (
                        <div
                          key={`${bucket.skillId}-${bucket.horizon}`}
                          className="grid grid-cols-[minmax(0,1.6fr)_auto_auto_auto_auto_auto_auto_auto_auto_auto] items-center gap-x-4 rounded-lg border border-border/50 bg-elevated/25 px-3 py-1.5 text-xs"
                        >
                          <span className="min-w-0 truncate font-medium text-foreground">
                            {getSkillLabel(bucket.skillId, t)}
                            <span className="ml-2 text-secondary-text">{bucket.horizon}</span>
                          </span>
                          {/* 单元格列含义由上方表头承载；不要给这些 span 补原生 title，
                              `tests/ui_governance.test.ts` 禁止在 span/div 等元素上用原生 title。 */}
                          <span className="text-secondary-text">
                            {bucket.pending}
                          </span>
                          <span className="text-secondary-text">
                            {bucket.evaluated}
                          </span>
                          <span className="text-secondary-text">
                            {bucket.total}
                          </span>
                          <span className="text-success">{bucket.hit}</span>
                          <span className="text-danger">{bucket.miss}</span>
                          <span
                            className={`tabular-nums text-secondary-text ${bucket.sampleSufficient ? '' : 'text-warning'}`}
                          >
                            {bucket.hitRatePct != null ? `${bucket.hitRatePct.toFixed(1)}%` : '-'}
                          </span>
                          <span className="tabular-nums text-secondary-text">
                            {bucket.avgDirectionalReturnPct != null ? `${bucket.avgDirectionalReturnPct.toFixed(1)}%` : '-'}
                          </span>
                          <span
                            className={bucket.sampleSufficient ? 'text-secondary-text' : 'text-warning'}
                          >
                            {getSkillOpinionSampleStatusLabel(bucket.sampleStatus, t)}
                          </span>
                        </div>
                      ))}
                      <p className="pt-1 text-xs text-secondary-text">{t('decisionSignals.statsLegend')}</p>
                    </div>
                  ) : skillStats && skillStats.buckets.length > 0 ? (
                    // 已有后验记录但没有任何 bucket 达标：此时每一行的命中率与平均收益都是 `-`，
                    // 表格不再承载信息，改为如实展示样本进展，并保留刷新 / 手动评估两个入口。
                    <EmptyState
                      className="border-none bg-transparent py-6 shadow-none"
                      title={t('decisionSignals.skillStatsInsufficientTitle')}
                      description={t('decisionSignals.skillStatsInsufficientDescription', {
                        buckets: skillStatsProgress.buckets,
                        evaluated: skillStatsProgress.evaluated,
                        pending: skillStatsProgress.pending,
                        observational: skillStatsProgress.observational,
                        unable: skillStatsProgress.unable,
                        threshold: skillStats.minimumEvaluatedSampleSize,
                        maxEvaluated: skillStatsProgress.maxEvaluated,
                      })}
                      icon={<BarChart3 className="h-6 w-6" />}
                    />
                  ) : (
                    <EmptyState
                      className="border-none bg-transparent py-6 shadow-none"
                      title={t('decisionSignals.skillStatsEmptyTitle')}
                      description={t('decisionSignals.skillStatsEmptyDescription')}
                      icon={<BarChart3 className="h-6 w-6" />}
                    />
                  )}
                </Card>
              </>
            ) : null}
          </div>
        </Collapsible>
      </div>

      <Drawer
        isOpen={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={t('decisionSignals.detailTitle')}
        width="max-w-4xl"
      >
        {selected ? (
          <div className="space-y-4">
            {renderReassessPanel()}
            <DecisionSignalDetails
              item={selected.item}
              outcomes={selectedOutcomes}
              outcomesLoading={selectedOutcomesLoading}
              outcomesError={selectedOutcomesError?.message ?? null}
              feedback={selectedFeedback}
              feedbackLoading={selectedFeedbackLoading}
              feedbackSaving={feedbackSaving}
              feedbackError={selectedFeedbackError?.message ?? null}
              onFeedbackSubmit={handleFeedbackSubmit}
              actions={STATUS_ACTIONS.map((status) => (
                <button
                  key={status}
                  type="button"
                  className="btn-secondary !px-3 !py-1.5 !text-xs"
                  onClick={() => setPendingStatus({
                    item: selected.item,
                    status,
                    message: t(STATUS_ACTION_CONFIRM_KEYS[status]),
                  })}
                  disabled={statusUpdating || selected.item.status === status}
                >
                  {t(STATUS_ACTION_LABEL_KEYS[status])}
                </button>
              ))}
            />
          </div>
        ) : null}
      </Drawer>

      {/* 处理中提示原先钉在右下角，现在跟随全站约定走右上角容器。 */}
      {statusUpdating ? (
        <ToastPortal>
          <InlineAlert
            elevated
            className="pointer-events-auto"
            variant="info"
            title={t('common.processing')}
            message={t('decisionSignals.confirmStatusTitle')}
          />
        </ToastPortal>
      ) : null}

      <ConfirmDialog
        isOpen={reassessPersistConfirm}
        title={t('decisionSignals.reassessPersistConfirmTitle')}
        message={t('decisionSignals.reassessPersistConfirmMessage')}
        confirmText={t('decisionSignals.reassessPersist')}
        confirmDisabled={reassessPersisting}
        cancelDisabled={reassessPersisting}
        onConfirm={() => void handlePersistReassess()}
        onCancel={() => setReassessPersistConfirm(false)}
      />

      <ConfirmDialog
        isOpen={Boolean(pendingStatus)}
        title={t('decisionSignals.confirmStatusTitle')}
        message={pendingStatus?.message ?? ''}
        confirmText={t('common.confirm')}
        confirmDisabled={statusUpdating}
        cancelDisabled={statusUpdating}
        onConfirm={() => void handleStatusUpdate()}
        onCancel={() => setPendingStatus(null)}
      />
    </AppPage>
  );
};

export default DecisionSignalsPage;
