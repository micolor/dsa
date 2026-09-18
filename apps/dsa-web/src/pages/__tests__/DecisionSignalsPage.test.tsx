import type React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  decisionSignalsApi,
  getDecisionSignalReassessBlockedError,
} from '../../api/decisionSignals';
import { historyApi } from '../../api/history';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import type { StockBarResponse } from '../../types/analysis';
import type {
  DecisionSignalFeedbackItem,
  DecisionSignalItem,
  DecisionSignalListResponse,
  DecisionSignalOutcomeListResponse,
  DecisionSignalOutcomeStatsResponse,
  DecisionSignalReassessResponse,
  SkillOpinionPerformanceStatsResponse,
} from '../../types/decisionSignals';
import type { StockIndexItem } from '../../types/stockIndex';
import DecisionSignalsPage from '../DecisionSignalsPage';

let stockIndexState: {
  index: StockIndexItem[];
  loading: boolean;
  error: Error | null;
  fallback: boolean;
  loaded: boolean;
};

vi.mock('../../api/decisionSignals', () => ({
  getDecisionSignalReassessBlockedError: vi.fn(),
  decisionSignalsApi: {
    list: vi.fn(),
    getLatest: vi.fn(),
    getOutcomeStats: vi.fn(),
    getSignalOutcomes: vi.fn(),
    // 页面挂载时会拉 Skill 表现统计；工厂漏掉这两个方法会让 api 调用同步抛 TypeError，
    // 渲染出一个常驻的「加载 Skill 表现失败」alert，抢在测试真正断言的 alert 前面。
    getSkillOutcomeStats: vi.fn(),
    runSkillOutcomes: vi.fn(),
    getFeedback: vi.fn(),
    putFeedback: vi.fn(),
    updateStatus: vi.fn(),
    reassess: vi.fn(),
  },
}));

vi.mock('../../api/history', () => ({
  historyApi: {
    getStockBarList: vi.fn(),
  },
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => stockIndexState,
}));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ScatterChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  Scatter: ({
    data,
    onClick,
    shape,
  }: {
    data: Array<{ item: DecisionSignalItem }>;
    onClick: (datum: { item: DecisionSignalItem }) => void;
    shape: (props: unknown) => React.ReactNode;
  }) => (
    <div>
      {data.map((datum, index) => (
        <button
          key={datum.item.id}
          type="button"
          data-testid={`timeline-click-${datum.item.id}`}
          onClick={() => onClick(datum)}
        >
          {shape({ cx: 20 + index * 20, cy: 20, payload: datum })}
          {datum.item.stockCode}
        </button>
      ))}
    </div>
  ),
}));

const signal: DecisionSignalItem = {
  id: 7,
  stockCode: '600519',
  stockName: '贵州茅台',
  market: 'cn',
  sourceType: 'analysis',
  sourceReportId: 3001,
  marketPhase: 'intraday',
  triggerSource: 'web',
  action: 'hold',
  actionLabel: null,
  confidence: 0.72,
  score: 82,
  horizon: '3d',
  entryLow: 1600,
  entryHigh: 1620,
  stopLoss: 1550,
  targetPrice: 1700,
  invalidation: '跌破 1550',
  watchConditions: '观察成交量',
  reason: '趋势保持',
  riskSummary: '放量下跌风险',
  catalystSummary: '业绩窗口',
  evidence: { technical: 'ma' },
  dataQualitySummary: { freshness: 'ok' },
  planQuality: 'complete',
  status: 'active',
  expiresAt: '2026-06-18T09:30:00',
  createdAt: '2026-06-17T09:30:00',
  updatedAt: '2026-06-17T09:30:00',
  metadata: { source: 'test' },
};

const stockIndexItems: StockIndexItem[] = [
  {
    canonicalCode: '600519.SH',
    displayCode: '600519',
    nameZh: '贵州茅台',
    pinyinFull: 'guizhoumaotai',
    pinyinAbbr: 'gzmt',
    aliases: ['茅台'],
    market: 'CN',
    assetType: 'stock',
    active: true,
    popularity: 100,
  },
  {
    canonicalCode: 'AAPL',
    displayCode: 'AAPL',
    nameZh: 'Apple',
    market: 'US',
    assetType: 'stock',
    active: true,
    popularity: 90,
  },
  {
    canonicalCode: '00700.HK',
    displayCode: '00700',
    nameZh: '腾讯控股',
    market: 'HK',
    assetType: 'stock',
    active: true,
    popularity: 80,
  },
];

const stockBarResponse: StockBarResponse = {
  total: 1,
  items: [
    {
      id: 1,
      stockCode: '600519',
      analysisCount: 2,
      marketPhaseSummary: { market: 'CN', phase: 'unknown', warnings: [] },
    },
  ],
};

function makeSignal(overrides: Partial<DecisionSignalItem> = {}): DecisionSignalItem {
  return {
    ...signal,
    ...overrides,
  };
}

const formattedCreatedAt = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
}).format(new Date('2026-06-17T09:30:00Z'));

function listResponse(items: DecisionSignalItem[] = [signal], total = items.length): DecisionSignalListResponse {
  return {
    items,
    total,
    page: 1,
    pageSize: 20,
  };
}

/**
 * 待处理区（status=active、pageSize=100、无 stockCode）的默认夹具。
 *
 * 页面渲染时，「待处理」与「全部信号」会各自渲染同一批 active 信号，同一张卡片
 * 因此会出现两次。这里给待处理区一条与主列表完全错开的信号（股票、文案、数字、时间
 * 都与主夹具不同），使既有用例里 `getByText('贵州茅台')`、「查看 贵州茅台 AI 建议详情」
 * 之类的断言仍然唯一命中主列表，不必逐个改成 AllBy 变体。
 *
 * 注意数值 / 枚举不能与主夹具相同：既有用例断言的 `10d`、`closing_auction`、`0%`
 * 等文案必须保持唯一。
 */
const queueOnlySignal: DecisionSignalItem = makeSignal({
  id: 9001,
  stockCode: '300750',
  stockName: '宁德时代',
  market: 'cn',
  action: 'buy',
  actionLabel: null,
  confidence: 0.11,
  score: 11,
  horizon: '5d',
  entryLow: 11,
  entryHigh: 12,
  stopLoss: 10,
  targetPrice: 14,
  reason: '',
  catalystSummary: '',
  watchConditions: '',
  riskSummary: '',
  invalidation: '',
  planQuality: 'complete',
  marketPhase: 'unknown',
  sourceReportId: null,
  createdAt: '2026-06-17T08:00:00',
  updatedAt: '2026-06-17T08:00:00',
  expiresAt: '2026-06-20T08:00:00',
});

/**
 * 待处理区的独立宽查询：`pageSize === 100` 且没有 `stockCode`。
 *
 * 「去重最新」打开时主列表自身也会用 `pageSize = 100`；只有列表没有任何活跃筛选时页面才
 * 跳过待处理区的独立请求（`loadQueue` 直接 return），所以这个形状只在一部分用例里能唯一
 * 指向待处理区。加了活跃筛选的用例不能靠它区分两边，需要按调用次数或 mock 分派来判断。
 */
function isQueueRequest(params?: { pageSize?: number; stockCode?: string }): boolean {
  return params?.pageSize === 100 && params?.stockCode === undefined;
}

/**
 * `list` 的调用次数，排除待处理区的独立宽查询。
 *
 * 既有用例里的计数断言只针对主列表与时间线；待处理区的挂载请求会额外加一，
 * 直接用 `toHaveBeenCalledTimes` 会被这个与用例无关的请求带偏。
 * 仅适用于未打开「去重最新」的用例——该开关打开后主列表也用同样的请求形状，
 * 无法据此区分一次宽调用属于谁。
 */
function listCallsExcludingQueue(): number {
  return vi.mocked(decisionSignalsApi.list).mock.calls.filter(([params]) => !isQueueRequest(params)).length;
}

/**
 * 按调用顺序返回主列表响应，替代既有的 `mockResolvedValueOnce` 链。
 *
 * 待处理区的挂载请求会插进 list 的调用序列（排在主列表之后），直接沿用 once 链会让
 * 时间线、刷新等后续请求拿到错位的响应。这里按顺序只消费「非待处理区」的调用，
 * 待处理区固定拿 queueOnlySignal；序列用尽后回落到 listResponse()，与 beforeEach 一致。
 */
function mockListResponses(...responses: DecisionSignalListResponse[]): void {
  let index = 0;
  vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
    if (isQueueRequest(params)) return listResponse([queueOnlySignal]);
    const response = index < responses.length ? responses[index] : listResponse();
    index += 1;
    return response;
  });
}

const skillStatsResponse: SkillOpinionPerformanceStatsResponse = {
  engineVersion: 'decision-signal-v1',
  minimumEvaluatedSampleSize: 5,
  // 默认给一条已达标 bucket + 一条未达标 bucket：是否「存在已达标 bucket」决定表格渲不渲染
  // （全部未达标时走进度空态），未达标行覆盖「样本不足」文案分支。
  // 无样本空态与全未达标空态分别由用例用 mockResolvedValueOnce 构造。
  buckets: [
    {
      skillId: 'technical-trend',
      horizon: '3d',
      engineVersion: 'decision-signal-v1',
      total: 33,
      pending: 2,
      evaluated: 30,
      observational: 1,
      unable: 0,
      pendingReasons: { insufficient_future_data: 2 },
      unableReasons: {},
      hit: 20,
      miss: 10,
      sampleSufficient: true,
      sampleStatus: 'sufficient',
      hitRatePct: 66.67,
      missRatePct: 33.33,
      avgDirectionalReturnPct: 1.8,
      unableRatePct: 0,
    },
    {
      skillId: 'box_oscillation',
      horizon: '1d',
      engineVersion: 'decision-signal-v1',
      total: 13,
      pending: 5,
      evaluated: 1,
      observational: 7,
      unable: 0,
      pendingReasons: { missing_start_bar: 3, insufficient_future_data: 2 },
      unableReasons: {},
      hit: 1,
      miss: 0,
      sampleSufficient: false,
      sampleStatus: 'observational',
      hitRatePct: null,
      missRatePct: null,
      avgDirectionalReturnPct: null,
      unableRatePct: null,
    },
  ],
};

/** 全部 bucket 都未达标：表格里每一行的指标都会是 `-`，页面应改为进度空态。 */
const insufficientSkillStats: SkillOpinionPerformanceStatsResponse = {
  engineVersion: 'decision-signal-v1',
  minimumEvaluatedSampleSize: 5,
  buckets: [
    {
      skillId: 'box_oscillation',
      horizon: '1d',
      engineVersion: 'decision-signal-v1',
      total: 13,
      pending: 5,
      evaluated: 1,
      observational: 7,
      unable: 0,
      pendingReasons: { missing_start_bar: 3, insufficient_future_data: 2 },
      unableReasons: {},
      hit: 1,
      miss: 0,
      sampleSufficient: false,
      sampleStatus: 'observational',
      hitRatePct: null,
      missRatePct: null,
      avgDirectionalReturnPct: null,
      unableRatePct: null,
    },
    {
      skillId: 'shrink_pullback',
      horizon: '3d',
      engineVersion: 'decision-signal-v1',
      total: 13,
      pending: 3,
      evaluated: 2,
      observational: 7,
      unable: 1,
      pendingReasons: { missing_start_bar: 3 },
      unableReasons: { invalid_market_phase_context: 1 },
      hit: 1,
      miss: 1,
      sampleSufficient: false,
      sampleStatus: 'observational',
      hitRatePct: null,
      missRatePct: null,
      avgDirectionalReturnPct: null,
      unableRatePct: null,
    },
  ],
};

const outcomeStats: DecisionSignalOutcomeStatsResponse = {
  engineVersion: 'decision-signal-v1',
  horizons: null,
  statuses: ['active', 'expired', 'invalidated', 'closed'],
  total: 3,
  completed: 2,
  unable: 1,
  hit: 1,
  miss: 1,
  neutral: 0,
  hitRatePct: 50,
  avgStockReturnPct: 2.5,
  unableReasons: { missing_anchor_price: 1 },
  breakdowns: {},
  profileCalibration: {
    minimumCompletedSampleSize: 30,
    breakdowns: {
      decisionProfile: [
        {
          dimensions: { decisionProfile: 'balanced' },
          total: 2,
          completed: 2,
          unable: 0,
          hit: 1,
          miss: 1,
          neutral: 0,
          sampleSufficient: false,
          hitRatePct: null,
          avgStockReturnPct: null,
          missRatePct: null,
          unableRatePct: null,
          maxAdverseExcursionPct: null,
        },
        {
          dimensions: { decisionProfile: 'unknown' },
          total: 1,
          completed: 0,
          unable: 1,
          hit: 0,
          miss: 0,
          neutral: 0,
          sampleSufficient: false,
          hitRatePct: null,
          avgStockReturnPct: null,
          missRatePct: null,
          unableRatePct: null,
          maxAdverseExcursionPct: null,
        },
      ],
      decisionProfileAction: [],
      decisionProfileHorizon: [],
      decisionProfileMarketPhase: [],
      decisionProfileDataQualityLevel: [],
      profileSource: [],
    },
  },
};

const outcomeList: DecisionSignalOutcomeListResponse = {
  items: [
    {
      id: 31,
      signalId: 7,
      horizon: '3d',
      engineVersion: 'decision-signal-v1',
      evalStatus: 'completed',
      outcome: 'hit',
      directionExpected: 'not_down',
      directionCorrect: true,
      anchorDate: '2024-01-02',
      evalWindowDays: 3,
      startPrice: 100,
      endClose: 105,
      stockReturnPct: 5,
      action: 'hold',
      market: 'cn',
      planQuality: 'complete',
      dataQualityLevel: 'good',
      holdingState: 'holding',
    },
  ],
  total: 1,
  page: 1,
  pageSize: 100,
};

const emptyFeedback: DecisionSignalFeedbackItem = {
  signalId: 7,
  feedbackValue: null,
  reasonCode: null,
  note: null,
  source: null,
};

const reassessResponse: DecisionSignalReassessResponse = {
  preview: {
    action: 'watch',
    score: 72,
    confidence: null,
    horizon: '3d',
    entryLow: 1680,
    stopLoss: 1600,
    reason: 'preview reason',
    metadata: {
      decision_profile: 'balanced',
      data_quality_level: 'medium',
      scoring_breakdown: { raw_action: 'buy' },
      guardrail_result: {
        raw_action: 'buy',
        final_action: 'watch',
        passed: false,
        violations: ['missing_confidence'],
        adjustments: ['action_downgraded_by_guardrail'],
        adjusted: true,
      },
    },
  },
  item: null,
  created: false,
  warnings: [{ code: 'action_blocked_by_guardrail' }],
  blockedReason: 'actionable_signal_blocked_by_guardrail',
};

const persistableReassessResponse: DecisionSignalReassessResponse = {
  preview: {
    action: 'watch',
    score: 72,
    confidence: null,
    horizon: '3d',
    entryLow: 1680,
    stopLoss: 1600,
    reason: 'persistable preview reason',
    metadata: {
      decision_profile: 'balanced',
      guardrail_result: {
        raw_action: 'buy',
        final_action: 'watch',
        passed: true,
        violations: ['missing_confidence'],
        adjustments: ['action_downgraded_by_guardrail'],
        adjusted: true,
      },
    },
  },
  item: null,
  created: false,
  warnings: [{ code: 'action_adjusted_by_guardrail', message: '已由风控调整为 watch。' }],
  blockedReason: null,
};

const persistedReassessItem = makeSignal({
  id: 88,
  decisionProfile: 'balanced',
  sourceAgent: 'decision_profile_reassess',
  triggerSource: 'web:decision_profile_reassess',
  action: 'watch',
  actionLabel: '观望',
  confidence: null,
  createdAt: new Date(Date.now() - 1000).toISOString(),
  updatedAt: new Date(Date.now() - 1000).toISOString(),
  metadata: {
    decision_profile: 'balanced',
    guardrail_result: {
      raw_action: 'buy',
      final_action: 'watch',
      passed: true,
      violations: ['missing_confidence'],
      adjustments: ['action_downgraded_by_guardrail'],
      adjusted: true,
    },
  },
});

const persistedReassessResponse: DecisionSignalReassessResponse = {
  preview: null,
  item: persistedReassessItem,
  created: true,
  persistStatus: 'created',
  warnings: [{ code: 'action_adjusted_by_guardrail', message: '已由风控调整为 watch。' }],
  blockedReason: null,
};

function renderPage() {
  return render(
    <UiLanguageProvider>
      <DecisionSignalsPage />
    </UiLanguageProvider>,
  );
}

function deferredPromise<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function submitCurrentStock(value: string) {
  const input = screen.getByLabelText('当前股票');
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: '查看股票' }));
}

async function persistReassessFromFirstSignal() {
  await screen.findByText('贵州茅台');
  fireEvent.click(screen.getAllByRole('button', { name: '查看 贵州茅台 AI 建议详情' })[0]);
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '生成预览' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认保存' }));
  const confirmButtons = screen.getAllByRole('button', { name: '确认保存' });
  fireEvent.click(confirmButtons[confirmButtons.length - 1]);
}

beforeEach(() => {
  window.history.pushState({}, '', '/');
  window.localStorage.clear();
  window.localStorage.setItem('dsa.uiLanguage', 'zh');
  // 必须用 resetAllMocks：clearAllMocks 只清调用记录，残留的 mockResolvedValueOnce 队列会
  // 泄漏到下一个用例，让后续渲染拿到上一个用例的返回值。
  vi.resetAllMocks();
  stockIndexState = {
    index: stockIndexItems,
    loading: false,
    error: null,
    fallback: false,
    loaded: true,
  };
  vi.mocked(historyApi.getStockBarList).mockResolvedValue(stockBarResponse);
  vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
    isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse()
  ));
  vi.mocked(decisionSignalsApi.getLatest).mockResolvedValue(listResponse([signal]));
  vi.mocked(decisionSignalsApi.getOutcomeStats).mockResolvedValue(outcomeStats);
  vi.mocked(decisionSignalsApi.getSignalOutcomes).mockResolvedValue(outcomeList);
  vi.mocked(decisionSignalsApi.getSkillOutcomeStats).mockResolvedValue(skillStatsResponse);
  vi.mocked(decisionSignalsApi.getFeedback).mockResolvedValue(emptyFeedback);
  vi.mocked(decisionSignalsApi.putFeedback).mockResolvedValue({
    ...emptyFeedback,
    feedbackValue: 'useful',
    source: 'web',
  });
  vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValue({ ...signal, status: 'invalidated' });
  vi.mocked(decisionSignalsApi.reassess).mockResolvedValue(reassessResponse);
  vi.mocked(getDecisionSignalReassessBlockedError).mockReturnValue(null);
});

/** 自定义 Select 组件交互：点击触发按钮展开，等待选项经 requestAnimationFrame 异步渲染后，再点击 data-value 匹配的选项 */
async function selectByValue(label: string, value: string) {
  fireEvent.click(screen.getByLabelText(label));
  const option = await waitFor(() => {
    const el = screen.getAllByRole('option').find((o) => o.getAttribute('data-value') === value);
    if (!el) {
      throw new Error(`Select "${label}" has no option with value "${value}"`);
    }
    return el;
  });
  fireEvent.click(option);
}

/** 自定义 Select 组件交互：断言当前选中值（读取触发按钮的 data-value） */
function expectSelectValue(label: string, value: string) {
  expect(screen.getByLabelText(label)).toHaveAttribute('data-value', value);
}

describe('DecisionSignalsPage', () => {
  const expandStatsSection = async () => {
    fireEvent.click(screen.getByRole('button', { name: '统计数据' }));
    await screen.findByText('信号表现统计');
  };

  /** 展开「高级筛选」：筛选表单与「去重最新」开关默认收起且不挂载，收起时查不到它们的控件。 */
  const expandFiltersSection = async () => {
    fireEvent.click(screen.getByRole('button', { name: '高级筛选' }));
    await screen.findByLabelText('股票代码');
  };

  /**
   * 待处理区的某个分档 section（`queue-group-<group>`）。断言必须限定在档内：
   * 主列表会渲染同名股票卡片，不限定就会把主列表的卡片当成待处理区的结果。
   */
  const queueGroupSection = (name: string) => screen.getByRole('heading', { name }).closest('section') as HTMLElement;

  it('loads active signals by default', async () => {
    renderPage();

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenCalledWith(expect.objectContaining({
        status: 'active',
        page: 1,
        pageSize: 20,
      }));
    });
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).toHaveTextContent('贵州茅台');
    expect(screen.getByText('放量下跌风险')).toBeInTheDocument();
    expect(screen.getByText(formattedCreatedAt)).toBeInTheDocument();

    // 统计区默认收起且不请求，展开后才挂载并加载。
    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();
    await expandStatsSection();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('当前统计为全局已复盘 outcome 口径，不等于当前可见信号数量，也不随当前股票过滤。')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '决策风格历史表现' })).toBeInTheDocument();
    expect(decisionSignalsApi.getOutcomeStats).toHaveBeenCalledTimes(1);
  });

  it('refreshes the list, both stats cards, latest signals and timeline from the page refresh button', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandStatsSection();

    submitCurrentStock('600519');
    await waitFor(() => expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(1));

    // 主列表与时间线共用 list API，用请求形状区分：时间线带 stockCode 且 pageSize 为 100。
    const timelineCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.stockCode === '600519' && params?.pageSize === 100).length;
    // 待处理区的宽查询同样没有 stockCode，因此主列表要再按 pageSize 区分：
    // 待处理区固定 pageSize 100，主列表（未打开「去重最新」时）固定 pageSize 20。
    const pageListCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.stockCode === undefined && params?.pageSize === 20).length;
    const queueCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 100 && params?.stockCode === undefined).length;
    const statsCalls = vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length;
    const skillStatsCalls = vi.mocked(decisionSignalsApi.getSkillOutcomeStats).mock.calls.length;
    const latestCalls = vi.mocked(decisionSignalsApi.getLatest).mock.calls.length;
    const timelineCallsBefore = timelineCalls();
    const pageListCallsBefore = pageListCalls();
    const queueCallsBefore = queueCalls();
    expect(timelineCallsBefore).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.getSkillOutcomeStats).mock.calls.length)
        .toBe(skillStatsCalls + 1);
    });
    expect(pageListCalls()).toBe(pageListCallsBefore + 1);
    expect(timelineCalls()).toBe(timelineCallsBefore + 1);
    // 页面刷新按钮同时刷新待处理区。
    expect(vi.mocked(decisionSignalsApi.list).mock.calls.filter(
      ([params]) => params?.pageSize === 100 && params?.stockCode === undefined,
    ).length).toBe(queueCallsBefore + 1);
    expect(vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length).toBe(statsCalls + 1);
    expect(vi.mocked(decisionSignalsApi.getLatest).mock.calls.length).toBe(latestCalls + 1);
  });

  it('does not request latest signals or the timeline from refresh before a stock is applied', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandStatsSection();

    // 刷新同时驱动主列表与待处理区，按 pageSize 只统计主列表请求。
    const listCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 20).length;
    const listCallsBefore = listCalls();
    const statsCalls = vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length).toBe(statsCalls + 1);
    });
    expect(listCalls()).toBe(listCallsBefore + 1);
    // 刷新不得凭空发起「当前股票」查询：没有已应用股票上下文时保持引导态。
    expect(decisionSignalsApi.getLatest).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '查询时间线' })).toBeDisabled();
  });

  it('keeps the existing stats card usable when the backend omits profile calibration', async () => {
    vi.mocked(decisionSignalsApi.getOutcomeStats).mockResolvedValueOnce({
      ...outcomeStats,
      profileCalibration: undefined,
    });

    renderPage();
    await expandStatsSection();

    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '决策风格历史表现' })).not.toBeInTheDocument();
  });

  it('shows a zero-sample outcome stats state instead of misleading zero metrics', async () => {
    vi.mocked(decisionSignalsApi.getOutcomeStats).mockResolvedValueOnce({
      ...outcomeStats,
      total: 0,
      completed: 0,
      unable: 0,
      hit: 0,
      miss: 0,
      neutral: 0,
      hitRatePct: null,
      avgStockReturnPct: null,
    });

    renderPage();
    await expandStatsSection();

    // 复盘统计卡与 Skill 表现卡各有独立空态文案，这里同时断言只出现前者，
    // 避免「两张卡共用同一句话」再次把断言变成多元素命中。
    expect(await screen.findByText('暂无已复盘样本')).toBeInTheDocument();
    expect(screen.queryByText('暂无 Skill 表现样本')).not.toBeInTheDocument();
    expect(screen.getByText('当前统计为全局已复盘 outcome 口径，不等于当前可见信号数量，也不随当前股票过滤。')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('shows a skill-specific empty state when no skill performance sample is available', async () => {
    vi.mocked(decisionSignalsApi.getSkillOutcomeStats).mockResolvedValueOnce({
      ...skillStatsResponse,
      buckets: [],
    });

    renderPage();
    await expandStatsSection();

    expect(await screen.findByText('暂无 Skill 表现样本')).toBeInTheDocument();
    expect(screen.getByText('Skill 意见已产生时，也可能还没有形成可统计的后验评估结果。')).toBeInTheDocument();
    // 复盘统计卡此刻有数据，不应连带渲染它自己的空态。
    expect(screen.queryByText('暂无已复盘样本')).not.toBeInTheDocument();
  });

  it('replaces the skill performance table with a progress summary while no bucket has enough samples', async () => {
    vi.mocked(decisionSignalsApi.getSkillOutcomeStats).mockResolvedValueOnce(insufficientSkillStats);

    renderPage();
    await expandStatsSection();

    // 未达标时每一行的指标都是 `-`，表格不再承载信息，整块换成进度概述。
    expect(await screen.findByText('样本尚未达标')).toBeInTheDocument();
    expect(
      screen.getByText(
        '共 2 个「skill × 窗口」组合：已评估 3 · 待评估 8 · 观察 14 · 无法评估 1。单个组合累计 5 条已评估的方向性样本后才会展示命中率与平均收益，目前最高 2 条。',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('样本充分性')).not.toBeInTheDocument();
    expect(screen.queryByText('总评估')).not.toBeInTheDocument();
    // 空态仍保留两个操作入口：刷新与手动评估是推进待评估样本的唯一手段。
    expect(screen.getByRole('button', { name: '刷新 Skill 表现数据' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '手动评估' })).toBeInTheDocument();
  });

  it('renders the skill performance table with localized sample sufficiency labels', async () => {
    renderPage();
    await expandStatsSection();

    expect(await screen.findByText('样本充分')).toBeInTheDocument();
    expect(screen.getByText('样本不足')).toBeInTheDocument();
    expect(screen.getByText('总评估')).toBeInTheDocument();
    // 后端枚举值不得原样漏进中文界面。
    expect(screen.queryByText('sufficient')).not.toBeInTheDocument();
    expect(screen.queryByText('observational')).not.toBeInTheDocument();
    // 命中映射的 skill 显示中文名；不在映射中的 id 回退显示原始 id（不得变成 '-'）。
    expect(screen.getByText('箱体震荡')).toBeInTheDocument();
    expect(screen.getByText('technical-trend')).toBeInTheDocument();
    expect(screen.queryByText('box_oscillation')).not.toBeInTheDocument();
  });

  it('uses a source report id query parameter as an exact analysis lookup on load', async () => {
    window.history.pushState({}, '', '/decision-signals?sourceReportId=3001&status=closed&market=cn');

    renderPage();

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenCalledWith({
        sourceReportId: 3001,
        sourceType: 'analysis',
        page: 1,
        pageSize: 20,
      });
    });
    expect(screen.getByLabelText('来源报告 ID')).toHaveValue(3001);
  });

  it('renders decision signal enum filter labels in Chinese', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandFiltersSection();

    fireEvent.click(screen.getByLabelText('市场'));
    expect(await screen.findByRole('option', { name: '日股' })).toHaveAttribute('data-value', 'jp');
    expect(await screen.findByRole('option', { name: '韩股' })).toHaveAttribute('data-value', 'kr');
    fireEvent.click(screen.getByLabelText('阶段'));
    expect(await screen.findByRole('option', { name: '午间休市' })).toHaveAttribute('data-value', 'lunch_break');
    expect(await screen.findByRole('option', { name: '集合竞价' })).toHaveAttribute('data-value', 'closing_auction');
    fireEvent.click(screen.getByLabelText('来源'));
    expect(await screen.findByRole('option', { name: '大盘复盘' })).toHaveAttribute('data-value', 'market_review');
    expect(screen.getByLabelText('来源报告 ID')).toBeInTheDocument();
  });

  it('renders decision signal filters and card value labels in English', async () => {
    window.localStorage.setItem('dsa.uiLanguage', 'en');
    mockListResponses(listResponse([
      makeSignal({
        market: 'jp',
        marketPhase: 'closing_auction',
        horizon: '10d',
        planQuality: 'partial',
      }),
    ]));

    renderPage();

    // 待处理区的卡片与主列表卡片共用 DecisionSignalCard，指标标签（含 Horizon）各出现一次。
    await screen.findAllByText('Horizon');
    // 英文界面下折叠按钮文案也是英文，这里不能复用 expandFiltersSection。
    fireEvent.click(screen.getByRole('button', { name: 'Advanced filters' }));
    await screen.findByLabelText('Stock code');
    fireEvent.click(screen.getByLabelText('Market'));
    expect(await screen.findByRole('option', { name: 'Japan' })).toHaveAttribute('data-value', 'jp');
    expect(await screen.findByRole('option', { name: 'Korea' })).toHaveAttribute('data-value', 'kr');
    fireEvent.click(screen.getByLabelText('Phase'));
    expect(await screen.findByRole('option', { name: 'Closing auction' })).toHaveAttribute('data-value', 'closing_auction');
    fireEvent.click(screen.getByLabelText('Source'));
    expect(await screen.findByRole('option', { name: 'Market review' })).toHaveAttribute('data-value', 'market_review');
    expect(screen.getByLabelText('Source report ID')).toBeInTheDocument();
    expect(screen.getAllByText('Japan').length).toBeGreaterThan(1);
    expect(screen.getByText('10 days')).toBeInTheDocument();
    expect(screen.getByText('Plan quality: Partial')).toBeInTheDocument();
    expect(screen.getByText('Phase: Closing auction')).toBeInTheDocument();
    expect(screen.queryByText('10d')).not.toBeInTheDocument();
    expect(screen.queryByText('closing_auction')).not.toBeInTheDocument();
  });

  it('passes filter parameters when applying filters', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandFiltersSection();

    await selectByValue('市场', 'cn');
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } });
    await selectByValue('动作', 'hold');
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        market: 'cn',
        stockCode: '600519',
        action: 'hold',
        status: 'active',
        page: 1,
        pageSize: 20,
      }));
    });
  });

  it('deduplicates the list to the latest signal per stock and widens the fetch when toggled on', async () => {
    const olderMoutai = makeSignal({ id: 1, createdAt: '2026-06-16T09:30:00', score: 60 });
    const latestMoutai = makeSignal({ id: 7, createdAt: '2026-06-18T09:30:00' });
    const tencent = makeSignal({
      id: 8,
      stockCode: '00700',
      stockName: '腾讯控股',
      market: 'hk',
      action: 'buy',
      createdAt: '2026-06-17T09:30:00',
    });
    // 待处理区的挂载请求是首个「pageSize 100、无 stockCode」的调用。打开「去重最新」后
    // 主列表自身也会发同样形状的请求（本用例没有活跃筛选），无法按参数区分，因此只把
    // 第一次当作待处理区，其余一律返回主列表夹具。
    let queueRequestServed = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (isQueueRequest(params) && !queueRequestServed) {
        queueRequestServed = true;
        return listResponse([queueOnlySignal]);
      }
      return listResponse([olderMoutai, latestMoutai, tencent]);
    });

    renderPage();
    await screen.findAllByText('贵州茅台');

    // Default: paginated, no dedup, both 茅台 rows visible.
    expect(screen.getAllByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).toHaveLength(2);

    await expandFiltersSection();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        status: 'active',
        page: 1,
        pageSize: 100,
      }));
    });

    // Deduped to one 茅台 (the latest) plus one 腾讯.
    // 「去重最新」打开时待处理区复用主列表的原始结果（待处理区展示全貌、本身不去重），
    // 页面上另有 2 条茅台的待处理卡片，因此这里只统计主列表内的卡片。
    const queueCard = screen.getByRole('heading', { name: '待处理' }).closest('.terminal-card') as HTMLElement;
    const listButtons = (name: string) => screen.getAllByRole('button', { name })
      .filter((button) => !queueCard.contains(button));
    expect(listButtons('查看 贵州茅台 AI 建议详情')).toHaveLength(1);
    expect(listButtons('查看 腾讯控股 AI 建议详情')).toHaveLength(1);
    expect(screen.queryByText('去重后共 2 只股票')).toBeInTheDocument();
    expect(screen.queryByText(/已按股票去重，仅显示最近 3 条结果/)).toBeInTheDocument();
  });

  it('uses an exact analysis source report lookup when a report id filter is applied', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandFiltersSection();

    await selectByValue('市场', 'cn');
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } });
    await selectByValue('动作', 'hold');
    await selectByValue('来源', 'alert');
    await selectByValue('状态', 'closed');
    fireEvent.change(screen.getByLabelText('来源报告 ID'), { target: { value: '3001' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith({
        sourceReportId: 3001,
        sourceType: 'analysis',
        page: 1,
        pageSize: 20,
      });
    });
  });

  it('reassesses from the selected signal source report without triggering list lookup', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    expect(await screen.findByText('决策风格重评估预览')).toBeInTheDocument();
    vi.mocked(decisionSignalsApi.list).mockClear();

    fireEvent.click(screen.getByRole('button', { name: '生成预览' }));

    await waitFor(() => {
      expect(decisionSignalsApi.reassess).toHaveBeenCalledWith({
        sourceReportId: 3001,
        decisionProfile: 'balanced',
        persist: false,
      });
    });
    expect(decisionSignalsApi.list).not.toHaveBeenCalled();
    expect(await screen.findByText('actionable_signal_blocked_by_guardrail')).toBeInTheDocument();
    expect(screen.getByText('buy -> watch')).toBeInTheDocument();
    expect(screen.getByText('action_blocked_by_guardrail')).toBeInTheDocument();
  });

  it('reassesses from an existing source report id filter without a selected signal', async () => {
    window.history.pushState({}, '', '/decision-signals?sourceReportId=3001');
    vi.mocked(decisionSignalsApi.list).mockResolvedValueOnce(listResponse([], 0));

    renderPage();
    expect(await screen.findByText('决策风格重评估预览')).toBeInTheDocument();
    vi.mocked(decisionSignalsApi.list).mockClear();

    fireEvent.click(screen.getByRole('button', { name: '生成预览' }));

    await waitFor(() => {
      expect(decisionSignalsApi.reassess).toHaveBeenCalledWith({
        sourceReportId: 3001,
        decisionProfile: 'balanced',
        persist: false,
      });
    });
    expect(decisionSignalsApi.list).not.toHaveBeenCalled();
  });

  it('confirms persist, trusts the returned item, and refreshes list and active timeline state', async () => {
    let persisted = false;
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => {
      if (!request.persist) return persistableReassessResponse;
      persisted = true;
      return persistedReassessResponse;
    });
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params)
        ? listResponse([queueOnlySignal])
        : listResponse(persisted ? [persistedReassessItem, signal] : [signal])
    ));
    vi.mocked(decisionSignalsApi.getLatest).mockImplementation(async () => (
      listResponse(persisted ? [persistedReassessItem, signal] : [signal])
    ));

    renderPage();
    await screen.findByText('贵州茅台');
    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    submitCurrentStock('600519');
    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenCalledWith(expect.objectContaining({
        stockCode: '600519',
        pageSize: 100,
      }));
    });

    fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
    const saveButton = await screen.findByRole('button', { name: '确认保存' });
    fireEvent.click(saveButton);
    expect(screen.getByText('保存重评估信号')).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole('button', { name: '确认保存' });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(decisionSignalsApi.reassess).toHaveBeenLastCalledWith({
        sourceReportId: 3001,
        decisionProfile: 'balanced',
        persist: true,
      });
    });
    expect(await screen.findByText('已保存为新的 DecisionSignal #88。')).toBeInTheDocument();
    expect(screen.getByText('已由风控调整为 watch。')).toBeInTheDocument();
    expect(await screen.findByTestId('timeline-click-88')).toBeInTheDocument();
    // 只统计时间线查询（带 stockCode）；待处理区的宽查询没有 stockCode，不算在内。
    await waitFor(() => expect(
      vi.mocked(decisionSignalsApi.list).mock.calls.filter(
        ([params]) => params?.pageSize === 100 && params?.stockCode !== undefined,
      ),
    ).toHaveLength(2));
    await waitFor(() => expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(decisionSignalsApi.list).toHaveBeenCalledWith(expect.objectContaining({
      status: 'active',
      page: 1,
      pageSize: 20,
    })));
  });

  it('keeps a newly persisted terminal history item out of latest active while retaining it in the timeline', async () => {
    const terminalHistoryItem = makeSignal({
      id: 92,
      decisionProfile: 'balanced',
      sourceAgent: 'decision_profile_reassess',
      triggerSource: 'web:decision_profile_reassess',
      action: 'buy',
      status: 'invalidated',
      createdAt: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    });
    let persisted = false;
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => {
      if (!request.persist) return persistableReassessResponse;
      persisted = true;
      return {
        preview: null,
        item: terminalHistoryItem,
        created: true,
        persistStatus: 'created',
        warnings: [],
        blockedReason: null,
      };
    });
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params)
        ? listResponse([queueOnlySignal])
        : listResponse(persisted ? [terminalHistoryItem, signal] : [signal])
    ));
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValue(listResponse([signal]));

    renderPage();
    await screen.findByText('贵州茅台');
    submitCurrentStock('600519');
    await persistReassessFromFirstSignal();

    expect(await screen.findByText('已保存为新的 DecisionSignal #92。')).toBeInTheDocument();
    expect(await screen.findByTestId('timeline-click-92')).toBeInTheDocument();
    // 只统计时间线查询（带 stockCode）；待处理区的宽查询没有 stockCode，不算在内。
    await waitFor(() => expect(
      vi.mocked(decisionSignalsApi.list).mock.calls.filter(
        ([params]) => params?.pageSize === 100 && params?.stockCode !== undefined,
      ),
    ).toHaveLength(2));
    expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(1);
  });

  it('reports an auto-balanced exact match as existing without claiming a new save', async () => {
    const autoBalancedItem = makeSignal({
      id: 89,
      decisionProfile: 'balanced',
      sourceAgent: null,
      triggerSource: 'api',
      action: 'buy',
      metadata: {
        decision_profile: 'balanced',
        profile_source: 'auto_default',
        signal_generation_version: 'legacy-report-extractor-v1',
      },
    });
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => (
      request.persist
        ? {
          preview: null,
          item: autoBalancedItem,
          created: false,
          persistStatus: 'existing',
          warnings: [],
          blockedReason: null,
        }
        : persistableReassessResponse
    ));

    renderPage();
    await persistReassessFromFirstSignal();

    expect(await screen.findByText('已复用现有信号')).toBeInTheDocument();
    expect(screen.getByText(/DecisionSignal #89 已存在，本次没有重复创建/)).toBeInTheDocument();
    expect(screen.queryByText(/已保存为新的 DecisionSignal #89/)).not.toBeInTheDocument();
  });

  it('reports an expired signal refresh separately and refreshes active views', async () => {
    const refreshedItem = makeSignal({
      id: 90,
      // 必须显式给一个「现在之前 24 小时」的 createdAt：时间线的 90 天窗口是相对
      // Date.now() 计算的（itemMatchesAppliedTimeline），而 makeSignal 继承的主夹具
      // createdAt 是写死的绝对时间，一旦真实日期越过「夹具日期 + 90 天」，刷新出来的
      // 信号就会被窗口过滤掉，时间线上不再出现 #90。该用例自 2026-09-15 起因此失效。
      createdAt: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      decisionProfile: 'balanced',
      sourceAgent: null,
      triggerSource: 'api',
      action: 'buy',
      status: 'active',
      metadata: {
        decision_profile: 'balanced',
        profile_source: 'user_selected',
        signal_generation_version: 'decision-profile-reassess-v1',
      },
    });
    let persisted = false;
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => {
      if (!request.persist) return persistableReassessResponse;
      persisted = true;
      return {
        preview: null,
        item: refreshedItem,
        created: false,
        persistStatus: 'refreshed',
        warnings: [],
        blockedReason: null,
      };
    });
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params)
        ? listResponse([queueOnlySignal])
        : listResponse(persisted ? [refreshedItem, signal] : [signal])
    ));
    vi.mocked(decisionSignalsApi.getLatest).mockImplementation(async () => (
      listResponse(persisted ? [refreshedItem, signal] : [signal])
    ));

    renderPage();
    await screen.findByText('贵州茅台');
    submitCurrentStock('600519');
    await persistReassessFromFirstSignal();

    expect(await screen.findByText('重评估信号已刷新')).toBeInTheDocument();
    expect(screen.getByText(/DecisionSignal #90 已按存储契约完成过期续期或缺失维度补齐/)).toBeInTheDocument();
    expect(await screen.findByTestId('timeline-click-90')).toBeInTheDocument();
    await waitFor(() => expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(2));
  });

  it('keeps a terminal existing item terminal and does not inject it into active views', async () => {
    const terminalItem = makeSignal({
      id: 91,
      decisionProfile: 'balanced',
      sourceAgent: null,
      triggerSource: 'api',
      action: 'buy',
      status: 'closed',
      metadata: {
        decision_profile: 'balanced',
        profile_source: 'auto_default',
        signal_generation_version: 'legacy-report-extractor-v1',
      },
    });
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => (
      request.persist
        ? {
          preview: null,
          item: terminalItem,
          created: false,
          persistStatus: 'existing',
          warnings: [],
          blockedReason: null,
        }
        : persistableReassessResponse
    ));
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValue(listResponse([signal]));
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal])
    ));

    renderPage();
    await screen.findByText('贵州茅台');
    submitCurrentStock('600519');
    await persistReassessFromFirstSignal();

    expect(await screen.findByText('现有信号保持终态')).toBeInTheDocument();
    expect(screen.getByText(/DecisionSignal #91 已处于“已关闭”状态/)).toBeInTheDocument();
    expect(screen.queryByTestId('timeline-click-91')).not.toBeInTheDocument();
    expect(screen.queryByText(/已保存为新的 DecisionSignal #91/)).not.toBeInTheDocument();
    await waitFor(() => expect(
      vi.mocked(decisionSignalsApi.list).mock.calls.filter(([params]) => params?.pageSize === 20),
    ).toHaveLength(2));
    expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(1);
  });

  it('keeps the authoritative persist result visible after refreshing a latest-sourced detail', async () => {
    const latestSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Latest reassess source',
    });
    const persistedLatestItem = {
      ...persistedReassessItem,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us' as const,
    };
    let persisted = false;
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => {
      if (!request.persist) return persistableReassessResponse;
      persisted = true;
      return { ...persistedReassessResponse, item: persistedLatestItem };
    });
    vi.mocked(decisionSignalsApi.getLatest).mockImplementation(async () => (
      listResponse(persisted ? [persistedLatestItem, latestSignal] : [latestSignal])
    ));
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({
      ...persistedLatestItem,
      status: 'invalidated',
    });

    renderPage();
    await screen.findByText('贵州茅台');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByRole('button', { name: '查看 Apple AI 建议详情' }));

    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '生成预览' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认保存' }));
    const confirmButtons = screen.getAllByRole('button', { name: '确认保存' });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(2));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('已保存为新的 DecisionSignal #88。')).toBeInTheDocument();
    expect(within(dialog).getByText('观望')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));
    await waitFor(() => expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(88, { status: 'invalidated' }));
    expect(within(screen.getByRole('dialog')).getByText('已保存为新的 DecisionSignal #88。')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText('已失效')).toBeInTheDocument();
  });

  it('keeps the authoritative persist result visible after refreshing a timeline-sourced detail', async () => {
    const timelineSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Timeline reassess source',
    });
    const persistedTimelineItem = {
      ...persistedReassessItem,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us' as const,
    };
    let persisted = false;
    vi.mocked(decisionSignalsApi.reassess).mockImplementation(async (request) => {
      if (!request.persist) return persistableReassessResponse;
      persisted = true;
      return { ...persistedReassessResponse, item: persistedTimelineItem };
    });
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params)
        ? listResponse([queueOnlySignal])
        : params?.pageSize === 100
          ? listResponse(persisted ? [persistedTimelineItem, timelineSignal] : [timelineSignal])
          : listResponse()
    ));

    renderPage();
    await screen.findByText('贵州茅台');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByTestId('timeline-click-8'));

    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '生成预览' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认保存' }));
    const confirmButtons = screen.getAllByRole('button', { name: '确认保存' });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(
      vi.mocked(decisionSignalsApi.list).mock.calls.filter(([params]) => params?.pageSize === 100),
    ).toHaveLength(2));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('已保存为新的 DecisionSignal #88。')).toBeInTheDocument();
    expect(within(dialog).getByText('观望')).toBeInTheDocument();
  });

  it('keeps the preview visible and renders structured persist guardrail errors', async () => {
    const persistError = new Error('guardrail blocked');
    vi.mocked(decisionSignalsApi.reassess)
      .mockResolvedValueOnce(persistableReassessResponse)
      .mockRejectedValueOnce(persistError);
    vi.mocked(getDecisionSignalReassessBlockedError).mockImplementation((error) => (
      error === persistError
        ? {
          blockedReason: 'invalid_price_relationships',
          warnings: [{ code: 'action_blocked_by_guardrail', message: '价格关系矛盾，未保存。' }],
        }
        : null
    ));

    renderPage();
    await screen.findByText('贵州茅台');
    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认保存' }));
    const confirmButtons = screen.getAllByRole('button', { name: '确认保存' });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    expect(await screen.findByText('保存被风控阻断')).toBeInTheDocument();
    expect(screen.getByText('invalid_price_relationships')).toBeInTheDocument();
    expect(screen.getByText('价格关系矛盾，未保存。')).toBeInTheDocument();
    expect(screen.getByText('persistable preview reason')).toBeInTheDocument();
    expect(screen.queryByText(/DecisionSignal #88/)).not.toBeInTheDocument();
  });

  it('disables reassess when no source report id is available', async () => {
    vi.mocked(decisionSignalsApi.list).mockResolvedValueOnce(listResponse([
      makeSignal({ sourceReportId: null }),
    ]));

    renderPage();
    await screen.findByText('贵州茅台');
    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));

    await waitFor(() => {
      expect(screen.getAllByText('该信号不支持重评估').length).toBeGreaterThan(0);
    });
    expect(screen.getByRole('button', { name: '生成预览' })).toBeDisabled();
  });

  it('does not fallback to page source report id for a selected signal without source report id', async () => {
    window.history.pushState({}, '', '/decision-signals?sourceReportId=3001');
    vi.mocked(decisionSignalsApi.list).mockResolvedValueOnce(listResponse([
      makeSignal({ sourceReportId: null }),
    ]));

    renderPage();
    await screen.findByText('决策风格重评估预览');
    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));

    await waitFor(() => {
      expect(screen.getAllByText('该信号不支持重评估').length).toBeGreaterThan(0);
    });
    expect(screen.getByRole('button', { name: '生成预览' })).toBeDisabled();
    expect(decisionSignalsApi.reassess).not.toHaveBeenCalled();
  });

  it('ignores stale reassess responses after switching the selected signal', async () => {
    const nextSignal = makeSignal({
      id: 8,
      stockCode: '000001',
      stockName: '平安银行',
      sourceReportId: 3002,
    });
    const pending = deferredPromise<DecisionSignalReassessResponse>();
    mockListResponses(listResponse([signal, nextSignal], 2));
    vi.mocked(decisionSignalsApi.reassess).mockReturnValueOnce(pending.promise);

    renderPage();
    await screen.findByText('贵州茅台');
    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    fireEvent.click(await screen.findByRole('button', { name: '生成预览' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 平安银行 AI 建议详情' }));

    await act(async () => {
      pending.resolve({
        ...reassessResponse,
        preview: { ...reassessResponse.preview!, reason: 'stale A preview' },
      });
    });

    expect(screen.queryByText('stale A preview')).not.toBeInTheDocument();
  });

  it('queries latest active signals by stock code', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('600519');

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519', {
        market: undefined,
        limit: 5,
      });
    });
  });

  it('submits the main stock context once and keeps the applied context separate from the draft', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    vi.mocked(decisionSignalsApi.getLatest).mockClear();
    vi.mocked(decisionSignalsApi.list).mockClear();

    submitCurrentStock('AAPL');

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(1);
      expect(decisionSignalsApi.list).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText('当前查看：AAPL')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('当前股票'), { target: { value: 'MSFT' } });

    expect(screen.getByText('当前查看：AAPL')).toBeInTheDocument();
    expect(decisionSignalsApi.getLatest).toHaveBeenCalledTimes(1);
    expect(decisionSignalsApi.list).toHaveBeenCalledTimes(1);
  });

  it('uses autocomplete metadata for the active context instead of the old draft value', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.change(screen.getByLabelText('当前股票'), { target: { value: '6005' } });
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByRole('option', { name: /贵州茅台.*600519/ }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519.SH', {
        market: 'cn',
        limit: 5,
      });
    });
    expect(screen.getByText('当前查看：600519 / 贵州茅台 / cn')).toBeInTheDocument();
    expect(screen.getByLabelText('当前股票')).toHaveValue('600519');
  });

  it('shows recent history candidates and passes normalized market when a candidate is selected', async () => {
    renderPage();

    expect(await screen.findByText('最近分析')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /600519/ }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519', {
        market: 'cn',
        limit: 5,
      });
    });
    expect(screen.getByText('当前查看：600519 / cn')).toBeInTheDocument();
  });

  it('preserves the applied stock context metadata when the unchanged draft is submitted again', async () => {
    renderPage();

    expect(await screen.findByText('最近分析')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /600519/ }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenLastCalledWith('600519', {
        market: 'cn',
        limit: 5,
      });
    });
    expect(screen.getByLabelText('当前股票')).toHaveValue('600519');

    fireEvent.click(screen.getByRole('button', { name: '查看股票' }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenLastCalledWith('600519', {
        market: 'cn',
        limit: 5,
      });
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: '600519',
        market: 'cn',
      }));
    });
  });

  it('does not pass market for a history candidate when market cannot be inferred', async () => {
    vi.mocked(historyApi.getStockBarList).mockResolvedValueOnce({
      total: 1,
      items: [
        {
          id: 1,
          stockCode: '600519',
          analysisCount: 1,
          marketPhaseSummary: null,
        },
      ],
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^600519$/ }));

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519', {
        market: undefined,
        limit: 5,
      });
    });
  });

  it('falls back to popular stock index candidates when history is empty or fails', async () => {
    vi.mocked(historyApi.getStockBarList).mockResolvedValueOnce({ total: 0, items: [] });
    const { unmount } = renderPage();

    expect(await screen.findByText('热门候选')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /AAPL.*Apple.*us/ })).toBeInTheDocument();
    expect(decisionSignalsApi.getLatest).not.toHaveBeenCalled();

    unmount();
    vi.clearAllMocks();
    vi.mocked(historyApi.getStockBarList).mockRejectedValueOnce(new Error('history down'));
    vi.mocked(decisionSignalsApi.list).mockResolvedValue(listResponse());
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValue(listResponse([signal]));
    vi.mocked(decisionSignalsApi.getOutcomeStats).mockResolvedValue(outcomeStats);
    renderPage();

    expect(await screen.findByText('热门候选')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /AAPL.*Apple.*us/ })).toBeInTheDocument();
  });

  it('renders no candidate fallback without crashing when history and stock index are unavailable', async () => {
    stockIndexState = {
      index: [],
      loading: false,
      error: new Error('index down'),
      fallback: true,
      loaded: false,
    };
    vi.mocked(historyApi.getStockBarList).mockRejectedValueOnce(new Error('history down'));

    renderPage();

    expect(await screen.findByText('暂无可用候选，可直接输入股票代码或名称。')).toBeInTheDocument();
  });

  it('deduplicates history candidates with market-aware keys and falls back to stock code without market', async () => {
    vi.mocked(historyApi.getStockBarList).mockResolvedValueOnce({
      total: 4,
      items: [
        { id: 1, stockCode: '600519', analysisCount: 1, marketPhaseSummary: { market: 'CN', phase: 'unknown', warnings: [] } },
        { id: 2, stockCode: '600519', analysisCount: 1, marketPhaseSummary: { market: 'HK', phase: 'unknown', warnings: [] } },
        { id: 3, stockCode: 'AAPL', analysisCount: 1, marketPhaseSummary: null },
        { id: 4, stockCode: 'AAPL', analysisCount: 1, marketPhaseSummary: null },
      ],
    });
    renderPage();

    expect(await screen.findByText('最近分析')).toBeInTheDocument();
    const candidateButtons = screen.getAllByRole('button').filter((button) => (
      button.className.includes('rounded-full') &&
      (button.textContent?.includes('600519') || button.textContent?.includes('AAPL'))
    ));

    expect(candidateButtons.filter((button) => button.textContent?.includes('600519'))).toHaveLength(2);
    expect(candidateButtons.filter((button) => button.textContent?.includes('AAPL'))).toHaveLength(1);
  });

  it('does not use the advanced list market filter for latest lookup', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandFiltersSection();

    await selectByValue('市场', 'cn');
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        market: 'cn',
      }));
    });

    await selectByValue('市场', 'hk');
    submitCurrentStock('600519');

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenCalledWith('600519', {
        market: undefined,
        limit: 5,
      });
    });
  });

  it('ignores stale latest-search responses', async () => {
    const firstSearch = deferredPromise<DecisionSignalListResponse>();
    const secondSignal = {
      ...signal,
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us' as const,
      riskSummary: '第二次查询结果',
    };
    vi.mocked(decisionSignalsApi.getLatest)
      .mockReturnValueOnce(firstSearch.promise)
      .mockResolvedValueOnce(listResponse([secondSignal]));
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('600519');

    submitCurrentStock('AAPL');

    expect(await screen.findByText('第二次查询结果')).toBeInTheDocument();

    await act(async () => {
      firstSearch.resolve(listResponse([{ ...signal, riskSummary: '第一次晚返回结果' }]));
      await firstSearch.promise;
    });

    await waitFor(() => {
      expect(screen.queryByText('第一次晚返回结果')).not.toBeInTheDocument();
    });
    expect(screen.getByText('第二次查询结果')).toBeInTheDocument();
  });

  it('renders latest empty and error states', async () => {
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValueOnce(listResponse([], 0));
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('600519');

    expect(await screen.findByText('暂无最新有效信号')).toBeInTheDocument();

    vi.mocked(decisionSignalsApi.getLatest).mockRejectedValueOnce(new Error('latest down'));
    submitCurrentStock('600519');

    expect(await screen.findByRole('alert')).toHaveTextContent('latest down');
  });

  it('does not request the timeline before a current stock is selected', async () => {
    renderPage();

    await screen.findByText('贵州茅台');
    expect(screen.getAllByText('选择股票查看 AI 建议').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '查询时间线' })).toBeDisabled();
    expect(listCallsExcludingQueue()).toBe(1);
    fireEvent.click(screen.getByLabelText('时间线状态'));
    expect(screen.queryByRole('option', { name: '已关闭' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('时间线风格'));
    expect(await screen.findByRole('option', { name: '未知' })).toHaveAttribute('data-value', 'unknown');
  });

  it('queries timeline with independent filters and no default status', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('600519');
    await waitFor(() => expect(listCallsExcludingQueue()).toBe(2));

    await selectByValue('时间线市场', 'cn');
    await selectByValue('时间范围', '30d');
    await selectByValue('时间线风格', 'unknown');
    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }));

    await waitFor(() => {
      expect(listCallsExcludingQueue()).toBe(3);
    });
    expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
      market: 'cn',
      stockCode: '600519',
      page: 1,
      pageSize: 100,
      status: undefined,
      decisionProfile: 'unknown',
    }));
    const params = vi.mocked(decisionSignalsApi.list).mock.calls.at(-1)?.[0] as Record<string, string>;
    expect(params.createdFrom).toEqual(expect.any(String));
    expect(params.createdTo).toEqual(expect.any(String));
  });

  it('initializes timeline market from a new stock context once and preserves later user overrides', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    const getHistoryCandidateButton = () => screen.getAllByRole('button').find((button) => (
      button.textContent?.includes('600519') && button.textContent.includes('/ cn')
    ));
    fireEvent.click(await waitFor(() => {
      const button = getHistoryCandidateButton();
      expect(button).toBeTruthy();
      return button as HTMLButtonElement;
    }));

    await waitFor(() => {
      expectSelectValue('时间线市场', 'cn');
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: '600519',
        market: 'cn',
      }));
    });

    await selectByValue('时间线市场', 'hk');
    const sameCandidateButton = getHistoryCandidateButton();
    expect(sameCandidateButton).toBeTruthy();
    fireEvent.click(sameCandidateButton as HTMLButtonElement);

    await waitFor(() => {
      expectSelectValue('时间线市场', 'hk');
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: '600519',
        market: 'hk',
      }));
    });
  });

  it('clears timeline market from a previous stock context before a later manual stock submit without metadata', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    const historyCandidateButton = await waitFor(() => {
      const button = screen.getAllByRole('button').find((candidateButton) => (
        candidateButton.textContent?.includes('600519') && candidateButton.textContent.includes('/ cn')
      ));
      expect(button).toBeTruthy();
      return button as HTMLButtonElement;
    });
    fireEvent.click(historyCandidateButton);

    await waitFor(() => {
      expectSelectValue('时间线市场', 'cn');
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: '600519',
        market: 'cn',
      }));
    });

    fireEvent.click(screen.getByRole('button', { name: '清空当前股票' }));
    await waitFor(() => {
      expectSelectValue('时间线市场', '');
    });

    submitCurrentStock('AAPL');

    await waitFor(() => {
      expect(decisionSignalsApi.getLatest).toHaveBeenLastCalledWith('AAPL', {
        market: undefined,
        limit: 5,
      });
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: 'AAPL',
        market: undefined,
      }));
    });
  });

  it('preserves a user-selected timeline market when a later manual stock submit has no metadata', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    await selectByValue('时间线市场', 'us');
    submitCurrentStock('AAPL');

    await waitFor(() => {
      expectSelectValue('时间线市场', 'us');
      expect(decisionSignalsApi.getLatest).toHaveBeenLastCalledWith('AAPL', {
        market: undefined,
        limit: 5,
      });
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: 'AAPL',
        market: 'us',
      }));
    });
  });

  it('applies timeline draft filters only after the query button is clicked', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('AAPL');
    await waitFor(() => expect(listCallsExcludingQueue()).toBe(2));

    await selectByValue('时间线市场', 'us');
    await selectByValue('时间范围', '30d');
    await selectByValue('时间线状态', 'active');
    await selectByValue('时间线风格', 'conservative');

    expect(listCallsExcludingQueue()).toBe(2);

    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }));

    await waitFor(() => {
      expect(listCallsExcludingQueue()).toBe(3);
    });
    expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
      stockCode: 'AAPL',
      market: 'us',
      status: 'active',
      decisionProfile: 'conservative',
    }));
  });

  it('passes active timeline status, shows truncation, and opens details from a point', async () => {
    const timelineSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      action: 'alert',
      riskSummary: 'Timeline risk',
    });
    mockListResponses(listResponse(), listResponse([timelineSignal], 150));
    renderPage();
    await screen.findByText('贵州茅台');

    await selectByValue('时间线状态', 'active');
    submitCurrentStock('AAPL');

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: 'AAPL',
        status: 'active',
        pageSize: 100,
      }));
    });
    expect(await screen.findByText('仅展示最近 100 条信号，请缩小时间范围。')).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('timeline-click-8'));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Timeline risk')).toBeInTheDocument();
  });

  it('returns to the timeline guide when stock code is cleared after a search', async () => {
    const timelineSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Timeline stale risk',
    });
    mockListResponses(listResponse(), listResponse([timelineSignal], 1));
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByTestId('timeline-click-8'));
    expect(within(await screen.findByRole('dialog')).getByText('Timeline stale risk')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空当前股票' }));

    expect(screen.getAllByText('选择股票查看 AI 建议').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '查询时间线' })).toBeDisabled();
    expect(screen.queryByTestId('timeline-click-8')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(listCallsExcludingQueue()).toBe(2);
  });

  it('clears current stock derived state without closing a list-sourced drawer', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    expect(within(await screen.findByRole('dialog')).getByText('趋势保持')).toBeInTheDocument();

    submitCurrentStock('AAPL');
    expect(await screen.findByText('当前查看：AAPL')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空当前股票' }));

    expect(screen.getByLabelText('当前股票')).toHaveValue('');
    expect(screen.getAllByText('选择股票查看 AI 建议').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '查询时间线' })).toBeDisabled();
    expect(within(screen.getByRole('dialog')).getByText('趋势保持')).toBeInTheDocument();
  });

  it('closes a timeline-sourced drawer when an active timeline status update removes it', async () => {
    const timelineSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Timeline active risk',
    });
    mockListResponses(listResponse(), listResponse([timelineSignal], 1), listResponse());
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({ ...timelineSignal, status: 'invalidated' });
    renderPage();
    await screen.findByText('贵州茅台');

    await selectByValue('时间线状态', 'active');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByTestId('timeline-click-8'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(8, { status: 'invalidated' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('暂无时间线信号')).toBeInTheDocument();
  });

  it('uses applied timeline filters instead of draft filters after status updates', async () => {
    const timelineSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Timeline all risk',
    });
    mockListResponses(listResponse(), listResponse([timelineSignal], 1), listResponse());
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({ ...timelineSignal, status: 'invalidated' });
    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('AAPL');
    fireEvent.change(screen.getByLabelText('时间线状态'), { target: { value: 'active' } });
    fireEvent.click(await screen.findByTestId('timeline-click-8'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(8, { status: 'invalidated' });
    });
    await waitFor(() => {
      expect(within(screen.getByRole('dialog')).getByText('已失效')).toBeInTheDocument();
    });
    expect(screen.queryByText('暂无时间线信号')).not.toBeInTheDocument();
  });

  it('renders empty and error states', async () => {
    vi.mocked(decisionSignalsApi.list).mockResolvedValueOnce(listResponse([], 0));

    renderPage();

    expect(await screen.findByText('暂无决策信号')).toBeInTheDocument();
    vi.mocked(decisionSignalsApi.list).mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
  });

  it('clears stale list data and closes a list drawer when refresh fails', async () => {
    // 待处理区的挂载请求不占用序列：只有主列表的第二次调用（带筛选条件）才失败。
    let mainListCalls = 0;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (isQueueRequest(params)) return listResponse([queueOnlySignal]);
      mainListCalls += 1;
      if (mainListCalls > 1) throw new Error('filter failed');
      return listResponse();
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await expandFiltersSection();
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: 'AAPL' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('filter failed');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).not.toBeInTheDocument();
    expect(screen.getByText('共 0 条信号')).toBeInTheDocument();
  });

  it('opens details and confirms terminal status updates', async () => {
    mockListResponses(listResponse(), listResponse([], 0));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    expect(screen.getAllByText('贵州茅台')).toHaveLength(2);
    expect(within(dialog).getByText('趋势保持')).toBeInTheDocument();
    expect(within(dialog).getByText('#3001')).toBeInTheDocument();
    expect(await within(dialog).findByText('命中')).toBeInTheDocument();
    expect(within(dialog).getByText('暂无反馈')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    expect(await screen.findByRole('heading', { name: '更新信号状态' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(7, { status: 'invalidated' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('共 0 条信号')).toBeInTheDocument();
    expect(screen.getByText('暂无决策信号')).toBeInTheDocument();
  });

  it('submits useful feedback from the details drawer', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(await within(dialog).findByRole('button', { name: '有用' }));

    await waitFor(() => {
      expect(decisionSignalsApi.putFeedback).toHaveBeenCalledWith(7, {
        feedbackValue: 'useful',
        source: 'web',
      });
    });
    await waitFor(() => {
      expect(within(dialog).getAllByText('有用').length).toBeGreaterThan(1);
    });
  });

  it('ignores stale feedback submit responses after selecting another signal', async () => {
    const feedbackSave = deferredPromise<DecisionSignalFeedbackItem>();
    const nextSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      reason: 'Second signal reason',
    });
    mockListResponses(listResponse([signal, nextSignal], 2));
    vi.mocked(decisionSignalsApi.getFeedback).mockImplementation(async (signalId: number) => ({
      ...emptyFeedback,
      signalId,
    }));
    vi.mocked(decisionSignalsApi.putFeedback).mockReturnValueOnce(feedbackSave.promise);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    let dialog = await screen.findByRole('dialog');
    fireEvent.click(await within(dialog).findByRole('button', { name: '有用' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 Apple AI 建议详情' }));
    dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Second signal reason')).toBeInTheDocument();

    await act(async () => {
      feedbackSave.resolve({
        ...emptyFeedback,
        feedbackValue: 'useful',
        source: 'web',
      });
      await feedbackSave.promise;
    });

    await waitFor(() => {
      expect(within(dialog).getByText('暂无反馈')).toBeInTheDocument();
      expect(within(dialog).getAllByText('有用')).toHaveLength(1);
    });
  });

  it('closes a list-sourced drawer when filters remove the selected signal', async () => {
    mockListResponses(listResponse(), listResponse([], 0));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await expandFiltersSection();
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: 'AAPL' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('暂无决策信号')).toBeInTheDocument();
  });

  it('keeps a latest-sourced drawer open when the main list refreshes', async () => {
    const latestSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Latest risk',
    });
    vi.mocked(decisionSignalsApi.list)
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValueOnce(listResponse([], 0));
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValueOnce(listResponse([latestSignal]));
    renderPage();

    await screen.findByText('贵州茅台');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByRole('button', { name: '查看 Apple AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Latest risk')).toBeInTheDocument();

    await expandFiltersSection();
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => {
      expect(within(screen.getByRole('dialog')).getByText('Latest risk')).toBeInTheDocument();
    });
  });

  it('closes a latest-sourced drawer when the next latest search excludes the selected signal', async () => {
    const firstLatestSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Latest A risk',
    });
    const nextLatestSignal = makeSignal({
      id: 9,
      stockCode: 'MSFT',
      stockName: 'Microsoft',
      market: 'us',
      riskSummary: 'Latest B risk',
    });
    vi.mocked(decisionSignalsApi.getLatest)
      .mockResolvedValueOnce(listResponse([firstLatestSignal]))
      .mockResolvedValueOnce(listResponse([nextLatestSignal]));
    renderPage();

    await screen.findByText('贵州茅台');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByRole('button', { name: '查看 Apple AI 建议详情' }));
    expect(within(await screen.findByRole('dialog')).getByText('Latest A risk')).toBeInTheDocument();

    submitCurrentStock('MSFT');

    expect(await screen.findByText('Latest B risk')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('closes a latest-sourced drawer when latest search fails', async () => {
    const latestSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Latest risk before failure',
    });
    vi.mocked(decisionSignalsApi.getLatest)
      .mockResolvedValueOnce(listResponse([latestSignal]))
      .mockRejectedValueOnce(new Error('latest failed'));
    renderPage();

    await screen.findByText('贵州茅台');
    submitCurrentStock('AAPL');
    fireEvent.click(await screen.findByRole('button', { name: '查看 Apple AI 建议详情' }));
    expect(within(await screen.findByRole('dialog')).getByText('Latest risk before failure')).toBeInTheDocument();

    submitCurrentStock('MSFT');

    expect(await screen.findByRole('alert')).toHaveTextContent('latest failed');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('keeps a list-sourced drawer open when latest search results change', async () => {
    const latestSignal = makeSignal({
      id: 8,
      stockCode: 'AAPL',
      stockName: 'Apple',
      market: 'us',
      riskSummary: 'Latest lookup risk',
    });
    vi.mocked(decisionSignalsApi.getLatest).mockResolvedValueOnce(listResponse([latestSignal]));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    expect(within(await screen.findByRole('dialog')).getByText('趋势保持')).toBeInTheDocument();

    submitCurrentStock('AAPL');

    expect(await screen.findByText('Latest lookup risk')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText('趋势保持')).toBeInTheDocument();
  });

  it('ignores duplicate status confirmation clicks and disables confirmation controls', async () => {
    const statusUpdate = deferredPromise<DecisionSignalItem>();
    vi.mocked(decisionSignalsApi.updateStatus).mockReturnValueOnce(statusUpdate.promise);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    const confirmButton = await screen.findByRole('button', { name: '确定' });

    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);

    expect(decisionSignalsApi.updateStatus).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(confirmButton).toBeDisabled());

    await act(async () => {
      statusUpdate.resolve({ ...signal, status: 'invalidated' });
      await statusUpdate.promise;
    });
  });

  it('clamps to a valid page after status update removes the only item on the last page', async () => {
    const pageTwoSignal = makeSignal({ id: 8, stockCode: 'AAPL', stockName: 'Apple', market: 'us' });
    mockListResponses(
      listResponse([signal], 21),
      listResponse([pageTwoSignal], 21),
      listResponse([], 20),
      listResponse([signal], 20),
    );
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({ ...pageTwoSignal, status: 'invalidated' });
    renderPage();

    await screen.findByText('贵州茅台');
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看 Apple AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
        page: 1,
        pageSize: 20,
      }));
    });
    expect(screen.getByText('共 20 条信号')).toBeInTheDocument();
    expect(screen.queryByText('暂无决策信号')).not.toBeInTheDocument();
  });

  it('closes the status confirmation dialog and shows an error when status update fails', async () => {
    vi.mocked(decisionSignalsApi.updateStatus).mockRejectedValueOnce(new Error('status update failed'));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    expect(await screen.findByRole('heading', { name: '更新信号状态' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    const errorMessage = await screen.findByText('status update failed');
    expect(errorMessage.closest('[role="alert"]')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: '更新信号状态' })).not.toBeInTheDocument();
    });
    expect(within(dialog).getByText('有效')).toBeInTheDocument();
  });

  it.each([
    ['关闭信号', 'closed'],
    ['归档', 'archived'],
  ] as const)('confirms %s without exposing active recovery', async (buttonName, status) => {
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({ ...signal, status });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('button', { name: '关闭信号' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '标记失效' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '归档' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '有效' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '已过期' })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: buttonName }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(7, { status });
    });
  });

  it('groups active signals into the action queue and widens the queue fetch to 100', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    // 主列表用 pageSize 20；待处理区单独拉 100 条做全貌分档。
    expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
      ([params]) => params?.status === 'active' && params?.pageSize === 100 && params?.stockCode === undefined,
    )).toBe(true);
    expect(await screen.findByRole('heading', { name: '可以动手' })).toBeInTheDocument();
  });

  it('passes holdingOnly to both the queue and the paged list when the toggle is on', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.click(screen.getByLabelText('只看我的持仓'));

    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.holdingOnly === true && params?.pageSize === 100,
      )).toBe(true);
    });
    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.holdingOnly === true && params?.pageSize === 20,
      )).toBe(true);
    });
  });

  it('keeps a queue-sourced detail open after marking it invalid', async () => {
    window.history.pushState({}, '', '/decision-signals');
    vi.mocked(decisionSignalsApi.updateStatus).mockResolvedValueOnce({
      ...queueOnlySignal,
      status: 'invalidated',
    });
    renderPage();
    await screen.findByText('贵州茅台');

    fireEvent.click(await screen.findByRole('button', { name: '查看 宁德时代 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    await waitFor(() => {
      expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(9001, { status: 'invalidated' });
    });

    // 待处理区来源的详情在状态更新后必须保持打开，与 `persisted` 同语义。
    // 若 onSelect 写成了 `source: 'list'`、或页面缺了 `source === 'queue'` 分支，
    // 就会落到 list 的兜底分支：默认 active 筛选与 updated.status='invalidated' 不一致，
    // setSelected 返回 null，抽屉被关掉，下面的 findByRole('dialog') 直接抛错。
    const reopened = await screen.findByRole('dialog');
    expect(within(reopened).getByText('已失效')).toBeInTheDocument();
  });

  it('reuses the deduped main list for the queue and keeps the queue live on refresh', async () => {
    const tencent = makeSignal({
      id: 8,
      stockCode: '00700',
      stockName: '腾讯控股',
      market: 'hk',
      action: 'buy',
      createdAt: '2026-06-17T09:30:00',
    });
    // 主列表夹具必须落在「观察」档之外：待处理区默认收起「观察」，收起时子内容不挂载，
    // 用 watch 档信号当证据会把「待处理区拿到哪份数据」退化成「元素是否在 DOM 里」。
    const moutai = { ...signal, action: 'buy' as const };
    let dedupeOn = false;
    let refreshed = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) {
        // 「去重最新」打开后主列表自身即宽查询（pageSize 100、无 stockCode），
        // 与待处理区那次请求形状相同、无法按参数区分；本用例没有活跃筛选，此时
        // 待处理区复用主列表结果，因此只能按开关注记分派。
        return refreshed ? listResponse([tencent]) : listResponse([moutai, tencent]);
      }
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([moutai]);
    });

    renderPage();
    await screen.findByText('贵州茅台');
    const queueCard = () => screen.getByRole('heading', { name: '待处理' }).closest('.terminal-card') as HTMLElement;

    // 未打开去重时，待处理区用自己那次宽查询的结果。
    expect(within(queueCard()).getByRole('button', { name: '查看 宁德时代 AI 建议详情' })).toBeInTheDocument();

    dedupeOn = true;
    await expandFiltersSection();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 去重模式下 loadQueue 直接 return，待处理区必须复用主列表的**原始**（未去重）结果。
    // 若把 queueItems 固定成 rawQueueItems，待处理区会停在挂载时那份数据（宁德时代），
    // 既不跟随开关、也不跟随刷新。
    await waitFor(() => {
      expect(within(queueCard()).getByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).toBeInTheDocument();
    });

    refreshed = true;
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => {
      expect(within(queueCard()).getByRole('button', { name: '查看 腾讯控股 AI 建议详情' })).toBeInTheDocument();
    });
    expect(within(queueCard()).queryByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).not.toBeInTheDocument();
  });

  it('keeps invalidated signals out of the actionable group when dedupe is on with an all-status filter', async () => {
    const invalidatedBuy = makeSignal({
      id: 7777,
      stockCode: '000001',
      stockName: '平安银行',
      market: 'cn',
      action: 'buy',
      planQuality: 'complete',
      status: 'invalidated',
      createdAt: '2026-06-17T09:00:00',
    });
    let dedupeOn = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) {
        // 状态=「全部状态」时主列表不再限制 status，会带回已失效信号；
        // 待处理区自己的宽查询始终只取 status=active。
        return params?.status === 'active'
          ? listResponse([queueOnlySignal])
          : listResponse([signal, invalidatedBuy]);
      }
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal]);
    });

    renderPage();
    await screen.findByText('贵州茅台');

    await expandFiltersSection();
    await selectByValue('状态', '');
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    // 空字符串的 status 与 DEFAULT_LIST_FILTERS 相异，是活跃筛选：主列表随后不带 status。
    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.pageSize === 20 && params?.status === undefined,
      )).toBe(true);
    });

    dedupeOn = true;
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 等「去重最新」下的主列表请求落地（这一份数据里带着已失效的 buy）。
    await waitFor(() => {
      expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
        ([params]) => params?.pageSize === 100 && params?.status === undefined,
      )).toBe(true);
    });

    // 核心契约：已失效的 buy + plan_quality=complete 不得出现在「可以动手」——
    // 复用主列表结果时它会直接落进这一档，等于让用户去执行一条已作废的信号。
    expect(within(queueGroupSection('可以动手')).queryByRole(
      'button',
      { name: '查看 平安银行 AI 建议详情' },
    )).not.toBeInTheDocument();
    // 待处理区仍然自己发一次宽查询，筛选不当缩小它的全貌。
    await waitFor(() => {
      expect(within(queueGroupSection('可以动手')).getByRole(
        'button',
        { name: '查看 宁德时代 AI 建议详情' },
      )).toBeInTheDocument();
    });
  });

  it('keeps the queue on its own unfiltered wide request when dedupe is on with an active filter', async () => {
    const tencent = makeSignal({
      id: 7789,
      stockCode: '00700',
      stockName: '腾讯控股',
      market: 'hk',
      action: 'buy',
      planQuality: 'complete',
      createdAt: '2026-06-17T09:05:00',
    });
    let dedupeOn = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) {
        // 主列表带着 market=hk 筛选；待处理区的宽查询不带筛选，因此还能看到 cn 信号。
        return params?.market === 'hk'
          ? listResponse([tencent])
          : listResponse([queueOnlySignal]);
      }
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal]);
    });

    renderPage();
    await screen.findByText('贵州茅台');

    await expandFiltersSection();
    await selectByValue('市场', 'hk');
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    await waitFor(() => {
      expect(decisionSignalsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ market: 'hk' }));
    });

    dedupeOn = true;
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 主列表（被 market=hk 缩小）不能成为待处理区的数据源：
    // 否则「可以动手」会漏掉筛选项之外的可执行信号，且页面上没有任何提示。
    await waitFor(() => {
      expect(within(queueGroupSection('可以动手')).getByRole(
        'button',
        { name: '查看 宁德时代 AI 建议详情' },
      )).toBeInTheDocument();
    });
    expect(vi.mocked(decisionSignalsApi.list).mock.calls.some(
      ([params]) => params?.pageSize === 100 && params?.status === 'active' && params?.market === undefined,
    )).toBe(true);
  });

  it('reuses the main list for the queue under dedupe with default filters and adds no second request', async () => {
    const tencent = makeSignal({
      id: 7789,
      stockCode: '00700',
      stockName: '腾讯控股',
      market: 'hk',
      action: 'buy',
      planQuality: 'complete',
      createdAt: '2026-06-17T09:05:00',
    });
    let dedupeOn = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) return listResponse([tencent]);
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal]);
    });

    renderPage();
    await screen.findByText('贵州茅台');
    const queueCard = () => screen.getByRole('heading', { name: '待处理' }).closest('.terminal-card') as HTMLElement;
    const wideCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 100 && params?.stockCode === undefined).length;

    // 挂载时待处理区自己发过一次宽查询。
    expect(wideCalls()).toBe(1);

    dedupeOn = true;
    await expandFiltersSection();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 默认筛选下两边等价，复用成立：待处理区展示主列表那次宽查询的结果，
    // 且宽查询只增加这一次——复用不成立时这里会是 3。
    await waitFor(() => {
      expect(within(queueCard()).getByRole('button', { name: '查看 腾讯控股 AI 建议详情' })).toBeInTheDocument();
    });
    expect(wideCalls()).toBe(2);
    // 待处理区刷新按钮可点之后宽查询数不变：复用态下它不会再补一次自己的请求。
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新待处理信号' })).toBeEnabled());
    expect(wideCalls()).toBe(2);

    // 「只看我的持仓」不是列表筛选（不在 appliedFilters 里，而是同时作用于两次请求的
    // 独立开关），只开它不得把复用关掉：宽查询只再增加主列表那一次。
    fireEvent.click(screen.getByLabelText('只看我的持仓'));
    await waitFor(() => expect(wideCalls()).toBe(3));
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新待处理信号' })).toBeEnabled());
    expect(wideCalls()).toBe(3);
  });

  it('shows the queue loading state from the reused main list request when dedupe is on', async () => {
    const pending = deferredPromise<DecisionSignalListResponse>();
    let dedupeOn = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) return pending.promise;
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal]);
    });

    renderPage();
    await screen.findByText('贵州茅台');
    const queueCard = () => screen.getByRole('heading', { name: '待处理' }).closest('.terminal-card') as HTMLElement;
    expect(within(queueCard()).queryByText('正在加载...')).not.toBeInTheDocument();

    dedupeOn = true;
    await expandFiltersSection();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 复用主列表结果意味着待处理区的 loading 也必须来自主列表：把 queueLoading 固定成
    // rawQueueLoading 时，待处理区在去重请求进行中会错误地显示上一轮内容而非加载态。
    await waitFor(() => {
      expect(within(queueCard()).getByText('正在加载...')).toBeInTheDocument();
    });

    await act(async () => {
      pending.resolve(listResponse([signal]));
      await pending.promise;
    });
  });

  it('surfaces a failed reused main list request in the queue when dedupe is on', async () => {
    let dedupeOn = false;
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => {
      if (dedupeOn) throw new Error('去重请求失败');
      return isQueueRequest(params) ? listResponse([queueOnlySignal]) : listResponse([signal]);
    });

    renderPage();
    await screen.findByText('贵州茅台');
    const queueCard = () => screen.getByRole('heading', { name: '待处理' }).closest('.terminal-card') as HTMLElement;

    dedupeOn = true;
    await expandFiltersSection();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));

    // 与 loading 同理：去重模式下的待处理区没有自己的请求，错误也必须复用主列表的 error。
    // 固定成 rawQueueError（挂载时那次宽查询成功，值为 null）时这里会退回展示旧数据。
    await waitFor(() => {
      expect(within(queueCard()).getByText('去重请求失败')).toBeInTheDocument();
    });
  });

  it('notes that the queue is truncated when the wide fetch fills the queue page size', async () => {
    const hundred = Array.from({ length: 100 }, (_, index) => makeSignal({
      id: 5000 + index,
      stockCode: `9${String(index).padStart(5, '0')}`,
      stockName: `分档样本${index}`,
      createdAt: '2026-06-17T09:30:00',
    }));
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params) ? listResponse(hundred) : listResponse([signal])
    ));

    renderPage();
    await screen.findByText('贵州茅台');

    // 恰好取满 QUEUE_PAGE_SIZE（100）条时才提示「只在最近 100 条内分档」。
    expect(await screen.findByText('仅在最近 100 条启用中的信号内分档。')).toBeInTheDocument();
  });

  it('does not note truncation below the queue page size', async () => {
    // 夹具落在「可以动手」档（buy + complete）而不是「观察」档：后者默认收起、收起时
    // 子内容不挂载，用它当证据就只能证明「元素是否在 DOM 里」，证明不了待处理区真的
    // 收到了 99 条——那样截断说明的缺失可能只是因为内容没挂载。
    const ninetyNine = Array.from({ length: 99 }, (_, index) => makeSignal({
      id: 5000 + index,
      stockCode: `9${String(index).padStart(5, '0')}`,
      stockName: `分档样本${index}`,
      action: 'buy',
      planQuality: 'complete',
      createdAt: '2026-06-17T09:30:00',
    }));
    vi.mocked(decisionSignalsApi.list).mockImplementation(async (params) => (
      isQueueRequest(params) ? listResponse(ninetyNine) : listResponse([signal])
    ));

    renderPage();
    await screen.findByText('贵州茅台');

    await waitFor(() => {
      expect(screen.getByText('分档样本98')).toBeInTheDocument();
    });
    expect(screen.queryByText('仅在最近 100 条启用中的信号内分档。')).not.toBeInTheDocument();
  });

  it('collapses the advanced filter form by default and expands it on demand', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    // 条件渲染：收起时整个 Card 不挂载，所以输入框查不到。
    // 默认 status='active' 与 DEFAULT_LIST_FILTERS 相同，不算活跃筛选，不得自动展开。
    expect(screen.queryByLabelText('股票代码')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '高级筛选' }));

    expect(await screen.findByLabelText('股票代码')).toBeInTheDocument();
  });

  it('auto-expands the filter form when the URL carries an active filter', async () => {
    // getInitialFilters 只还原 sourceReportId，而 DEFAULT_LIST_FILTERS.sourceReportId
    // 是 ''，因此 '3001' 构成活跃筛选 → 挂载后应自动展开，无需点「高级筛选」。
    window.history.pushState({}, '', '/decision-signals?sourceReportId=3001');
    renderPage();
    await screen.findByText('贵州茅台');

    expect(await screen.findByLabelText('股票代码')).toBeInTheDocument();
  });

  it('does not request either stats card until the statistics section is expanded', async () => {
    renderPage();
    await screen.findByText('贵州茅台');

    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();
    expect(decisionSignalsApi.getSkillOutcomeStats).not.toHaveBeenCalled();

    // 收起状态下点页面刷新同样不得请求统计区：否则除了白跑两个请求，
    // statsLoading 翻真还会反过来把用户刚点的刷新按钮卡住。
    const pageListCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 20).length;
    const pageListCallsBefore = pageListCalls();
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(pageListCalls()).toBe(pageListCallsBefore + 1));
    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();
    expect(decisionSignalsApi.getSkillOutcomeStats).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '统计数据' }));

    await waitFor(() => {
      expect(decisionSignalsApi.getOutcomeStats).toHaveBeenCalledTimes(1);
      expect(decisionSignalsApi.getSkillOutcomeStats).toHaveBeenCalledTimes(1);
    });
  });

  it('refreshes the reused main list from the queue refresh button when dedupe is on', async () => {
    renderPage();
    await screen.findByText('贵州茅台');
    await expandFiltersSection();

    // 待处理区的宽查询与「去重最新」下的主列表同为 pageSize 100、无 stockCode，
    // 无法按参数区分，只能按调用次数追踪。
    const wideCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 100 && params?.stockCode === undefined).length;
    const refreshQueueButton = () => screen.getByRole('button', { name: '刷新待处理信号' });

    const wideCallsAtMount = wideCalls();
    fireEvent.click(screen.getByLabelText('只显示每只股票的最新信号'));
    // 打开「去重最新」后主列表自身变成宽查询；等它落地再取基线，避免把它的调用
    // 误记到下面那次点击上。
    await waitFor(() => expect(wideCalls()).toBe(wideCallsAtMount + 1));
    await waitFor(() => expect(refreshQueueButton()).toBeEnabled());

    // loadQueue 在去重模式下直接 return，待处理区的刷新入口必须落到主列表上；
    // 否则按钮可点击、可转圈，却既不请求也不改变任何东西。
    const wideCallsBefore = wideCalls();
    fireEvent.click(refreshQueueButton());
    await waitFor(() => expect(wideCalls()).toBe(wideCallsBefore + 1));
  });

  it('spins the page refresh button while a non-list request is in flight', async () => {
    const pendingLatest = deferredPromise<DecisionSignalListResponse>();
    vi.mocked(decisionSignalsApi.getLatest).mockReturnValueOnce(pendingLatest.promise);

    renderPage();
    await screen.findByText('贵州茅台');

    submitCurrentStock('600519');

    // 刷新按钮的禁用条件覆盖 latest / timeline 等在途状态，转圈必须与禁用同源：
    // 只认主列表 loading 时，这里就是「按钮已灰却不转」。
    const refreshButton = screen.getByRole('button', { name: '刷新' });
    await waitFor(() => expect(refreshButton).toBeDisabled());
    expect(refreshButton.querySelector('svg')).toHaveClass('animate-spin');

    await act(async () => {
      pendingLatest.resolve(listResponse([signal]));
      await pendingLatest.promise;
    });
  });

  it('does not request outcome stats on a status update while the statistics section is collapsed', async () => {
    mockListResponses(listResponse(), listResponse([], 0));
    renderPage();
    await screen.findByText('贵州茅台');

    const pageListCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 20).length;
    const pageListCallsBefore = pageListCalls();
    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    // 状态更新后的整条刷新链路（待处理区 → 主列表 → [统计]）都在 statusUpdating
    // 期间完成，「处理中...」消失即链路跑完，此时断言才不是抢跑。
    await waitFor(() => expect(pageListCalls()).toBe(pageListCallsBefore + 1));
    await waitFor(() => expect(screen.queryByText('处理中...')).not.toBeInTheDocument());
    await act(async () => undefined);

    // 统计区收起时不夹带统计请求：与挂载、页面刷新按钮保持同一契约（spec §4.3）。
    expect(decisionSignalsApi.getOutcomeStats).not.toHaveBeenCalled();
    expect(decisionSignalsApi.getSkillOutcomeStats).not.toHaveBeenCalled();
  });

  it('refreshes outcome stats on a status update while the statistics section is expanded', async () => {
    mockListResponses(listResponse(), listResponse([], 0));
    renderPage();
    await screen.findByText('贵州茅台');
    await expandStatsSection();
    // 展开态自身会请求一次统计，先让它落地再取基线。
    await act(async () => undefined);
    const statsCallsBefore = vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length;

    const pageListCalls = () => vi.mocked(decisionSignalsApi.list).mock.calls
      .filter(([params]) => params?.pageSize === 20).length;
    const pageListCallsBefore = pageListCalls();

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '标记失效' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    expect(screen.getByText('处理中...')).toBeInTheDocument();
    // 「处理中...」覆盖状态更新的整条刷新链路（pendingStatus 早于两个 await 就清空了，
    // 所以弹窗关闭不是有效屏障）；它消失即链路跑完，此时断言才不是抢跑。
    await waitFor(() => expect(screen.queryByText('处理中...')).not.toBeInTheDocument());
    await waitFor(() => expect(pageListCalls()).toBe(pageListCallsBefore + 1));

    // 门控必须是「展开就刷、收起就不刷」双向的：只钉住收起侧时，把函数体掏空成
    // `if (statsExpanded) { void 0; }`（甚至门控到别的布尔上）依然全绿。
    expect(vi.mocked(decisionSignalsApi.getOutcomeStats).mock.calls.length).toBe(statsCallsBefore + 1);
  });
});
