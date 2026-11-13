import type { SystemConfigCategory, SystemConfigItem } from '../types/systemConfig';

export interface SettingsFieldGroupDefinition {
  id: string;
  titleZh: string;
  titleEn: string;
  descriptionZh?: string;
  descriptionEn?: string;
  matches: (key: string) => boolean;
}

export interface SettingsFieldGroup {
  id: string;
  titleZh?: string;
  titleEn?: string;
  descriptionZh?: string;
  descriptionEn?: string;
  items: SystemConfigItem[];
}

const startsWithAny = (key: string, prefixes: string[]) =>
  prefixes.some((prefix) => key.startsWith(prefix));

const equalsAny = (key: string, keys: string[]) => keys.includes(key);

const AGENT_RUNTIME_KEYS = [
  'AGENT_BACKEND',
  'AGENT_MODE',
  'AGENT_ARCH',
  'AGENT_MAX_STEPS',
  'AGENT_NL_ROUTING',
  'AGENT_CHAT_INTENT_ROUTING_ENABLED',
];

const AI_MODEL_ROUTING_KEYS = [
  'LITELLM_MODEL',
  'AGENT_LITELLM_MODEL',
  'LITELLM_FALLBACK_MODELS',
  'LITELLM_CONFIG',
  'LLM_CHANNELS',
  'LLM_TEMPERATURE',
];

const AI_MODEL_GENERATION_KEYS = [
  'GENERATION_FALLBACK_BACKEND',
  'LOCAL_CLI_BACKEND_MAX_CONCURRENCY',
  'OPENCODE_CLI_MODEL',
];

const AI_PROVIDER_PREFIXES = [
  'ANSPIRE_LLM_',
  'DEEPSEEK_API_',
  'GEMINI_',
  'ANTHROPIC_',
  'OPENAI_',
];

const DATA_SOURCE_MARKET_PREFIXES = [
  'TICKFLOW_',
  'ENABLE_REALTIME_',
  'PYTDX_',
];

const DATA_SOURCE_MARKET_KEYS = [
  'TUSHARE_TOKEN',
  'STOCK_INDEX_REMOTE_UPDATE_ENABLED',
  'REALTIME_SOURCE_PRIORITY',
  'ENABLE_CHIP_DISTRIBUTION',
];

const DATA_SOURCE_SEARCH_PREFIXES = [
  'TAVILY_',
  'SERPAPI_',
  'BRAVE_',
  'BOCHA_',
  'SEARXNG_',
  'MINIMAX_',
];

const AGENT_PROACTIVE_PUSH_EXCLUDED_KEYS = [
  'AGENT_PROACTIVE_QUIET_HOURS',
  'AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY',
];

const AGENT_PROACTIVE_GUARD_KEYS = [
  'AGENT_PROACTIVE_MARKET_GUARD',
  'AGENT_PROACTIVE_RETENTION_DAYS',
  'AGENT_PROACTIVE_SCAN_CONCURRENCY',
];

/**
 * Ordered subsections used inside a settings category. The first matching
 * definition wins, so specific groups must be declared before broader ones.
 */
export const SETTINGS_FIELD_GROUPS: Partial<Record<SystemConfigCategory, SettingsFieldGroupDefinition[]>> = {
  base: [
    {
      id: 'watchlist',
      titleZh: '自选与交易日',
      titleEn: 'Watchlist and trading days',
      matches: (key) => equalsAny(key, ['STOCK_LIST', 'TRADING_DAY_CHECK_ENABLED']),
    },
    {
      id: 'market-review',
      titleZh: '大盘复盘',
      titleEn: 'Market review',
      matches: (key) => key.startsWith('MARKET_REVIEW_') || key === 'DAILY_MARKET_CONTEXT_ENABLED',
    },
    {
      id: 'paper-trading',
      titleZh: '模拟盘',
      titleEn: 'Paper trading',
      descriptionZh: '模拟盘自动交易、成交通知与运行参数。',
      descriptionEn: 'Auto trade, fill notifications and runtime parameters for the paper account.',
      matches: (key) => key.startsWith('PAPER_'),
    },
  ],
  ai_model: [
    {
      id: 'generation-backend',
      titleZh: '生成方式与本地 CLI',
      titleEn: 'Generation backend and local CLI',
      descriptionZh: '选择默认生成方式、回退方式与本地 CLI 的并发和超时。',
      descriptionEn: 'Default and fallback generation methods plus local CLI concurrency and timeouts.',
      matches: (key) => key.startsWith('GENERATION_') || equalsAny(key, AI_MODEL_GENERATION_KEYS),
    },
    {
      id: 'model-routing',
      titleZh: '主模型与路由',
      titleEn: 'Primary model and routing',
      descriptionZh: '主模型、Agent 专用模型、回退模型、渠道与温度。',
      descriptionEn: 'Primary model, Agent model, fallback models, channels and temperature.',
      matches: (key) => equalsAny(key, AI_MODEL_ROUTING_KEYS) || key.startsWith('LLM_USAGE_'),
    },
    {
      id: 'model-providers',
      titleZh: '模型提供商',
      titleEn: 'Model providers',
      descriptionZh: '各提供商的密钥、地址与模型参数；已配置的渠道也可在设置页统一管理。',
      descriptionEn: 'Provider keys, base URLs and model parameters; configured channels can also be managed from the channel editor.',
      matches: (key) => key === 'AIHUBMIX_KEY' || startsWithAny(key, AI_PROVIDER_PREFIXES),
    },
  ],
  data_source: [
    {
      id: 'market-source',
      titleZh: '行情与 K 线数据源',
      titleEn: 'Quotes and K-line sources',
      descriptionZh: '实时行情、日线、筹码分布与通达信服务。',
      descriptionEn: 'Realtime quotes, daily bars, chip distribution and TDX services.',
      matches: (key) => equalsAny(key, DATA_SOURCE_MARKET_KEYS) || startsWithAny(key, DATA_SOURCE_MARKET_PREFIXES),
    },
    {
      id: 'search-source',
      titleZh: '搜索与资讯服务',
      titleEn: 'Search and news services',
      descriptionZh: '题材、板块和个股资讯的搜索服务。',
      descriptionEn: 'Search providers for themes, sectors and stock news.',
      matches: (key) => key === 'ANSPIRE_API_KEYS' || startsWithAny(key, DATA_SOURCE_SEARCH_PREFIXES),
    },
    {
      id: 'news-metrics',
      titleZh: '新闻与指标',
      titleEn: 'News and indicators',
      matches: (key) => equalsAny(key, ['NEWS_MAX_AGE_DAYS', 'NEWS_STRATEGY_PROFILE', 'BIAS_THRESHOLD']),
    },
  ],
  notification: [
    {
      id: 'report-content',
      titleZh: '报告内容',
      titleEn: 'Report content',
      descriptionZh: '报告类型、模板、完整性与摘要选项。',
      descriptionEn: 'Report type, templates, integrity and summary options.',
      matches: (key) => key.startsWith('REPORT_') || equalsAny(key, ['SINGLE_STOCK_NOTIFY', 'MERGE_EMAIL_NOTIFICATION']),
    },
    {
      id: 'notify-routing',
      titleZh: '通知路由与频控',
      titleEn: 'Routing and frequency control',
      descriptionZh: '不同消息类型的投递渠道、去重、冷却、静默时段与最低等级。',
      descriptionEn: 'Per-type delivery channels, dedup, cooldown, quiet hours and minimum severity.',
      matches: (key) => key.startsWith('NOTIFICATION_'),
    },
  ],
  system: [
    {
      id: 'webui',
      titleZh: 'Web 服务',
      titleEn: 'Web service',
      matches: (key) => key.startsWith('WEBUI_') || key === 'TRUST_X_FORWARDED_FOR',
    },
    {
      id: 'runtime',
      titleZh: '运行与性能',
      titleEn: 'Runtime and performance',
      matches: (key) => equalsAny(key, ['RUN_IMMEDIATELY', 'MAX_WORKERS', 'ANALYSIS_DELAY', 'SAVE_CONTEXT_SNAPSHOT']),
    },
    {
      id: 'ops',
      titleZh: '日志、代理与调试',
      titleEn: 'Logging, proxy and debug',
      matches: (key) => equalsAny(key, ['LOG_LEVEL', 'LOG_DIR', 'DEBUG', 'HTTP_PROXY']),
    },
  ],
  agent: [
    {
      id: 'agent-runtime',
      titleZh: '运行方式与架构',
      titleEn: 'Runtime and architecture',
      descriptionZh: '问股后端、Agent 模式、多智能体架构与单轮步数上限。',
      descriptionEn: 'Ask-stock backend, agent mode, multi-agent architecture and step limits.',
      matches: (key) => equalsAny(key, [...AGENT_RUNTIME_KEYS, 'SCHEDULED_TASKS_ENABLED']) || key.startsWith('AGENT_ORCHESTRATOR_'),
    },
    {
      id: 'agent-skills',
      titleZh: '策略与技能',
      titleEn: 'Strategies and skills',
      descriptionZh: '可用策略、技能目录、并发/加权/路由与深度研究预算。',
      descriptionEn: 'Available strategies, skill directory, concurrency/weighting/routing and deep-research budget.',
      matches: (key) => key.startsWith('AGENT_SKILL') || key.startsWith('AGENT_DEEP_RESEARCH') || key === 'AGENT_RISK_OVERRIDE',
    },
    {
      id: 'agent-context',
      titleZh: '记忆与上下文',
      titleEn: 'Memory and context',
      descriptionZh: '会话记忆、上下文压缩阈值与保留轮次。',
      descriptionEn: 'Conversation memory, compression thresholds and protected turns.',
      matches: (key) => key === 'AGENT_MEMORY_ENABLED' || key.startsWith('AGENT_CONTEXT_'),
    },
    {
      id: 'proactive-brief-event',
      titleZh: '主动提醒：日报与事件',
      titleEn: 'Proactive: daily brief and events',
      descriptionZh: '盘前/盘后日报与盘中事件分析的开关、时间、限额和推送。',
      descriptionEn: 'Daily brief and intraday event analysis switches, schedules, limits and push.',
      matches: (key) => (
        (key.startsWith('AGENT_PROACTIVE_BRIEF_') || key.startsWith('AGENT_EVENT_ANALYSIS_'))
        && !equalsAny(key, AGENT_PROACTIVE_PUSH_EXCLUDED_KEYS)
      ),
    },
    {
      id: 'proactive-review-opportunity',
      titleZh: '主动提醒：周复盘与机会扫描',
      titleEn: 'Proactive: weekly review and opportunities',
      descriptionZh: '每周复盘与机会扫描的开关、时间与候选数量。',
      descriptionEn: 'Weekly review and opportunity scan switches, schedules and candidate limits.',
      matches: (key) => (
        (key.startsWith('AGENT_WEEKLY_REVIEW_') || key.startsWith('AGENT_OPPORTUNITY_SCAN_'))
        && !key.startsWith('AGENT_OPPORTUNITY_SCAN_REFRESH')
      ),
    },
    {
      id: 'risk-profile',
      titleZh: '风险画像',
      titleEn: 'Risk profile',
      descriptionZh: '投资期限、单票上限、回撤容忍与按账户覆盖。',
      descriptionEn: 'Horizon, max position, drawdown tolerance and per-account overrides.',
      matches: (key) => key.startsWith('AGENT_RISK_PROFILE_'),
    },
    {
      id: 'proactive-push',
      titleZh: '推送策略与免打扰',
      titleEn: 'Push policy and quiet hours',
      descriptionZh: '主动消息的最低等级、路由、重试、深链、摘要与免打扰时段。',
      descriptionEn: 'Minimum severity, routing, retries, deep links, digests and quiet hours.',
      matches: (key) => key.startsWith('AGENT_PROACTIVE_NOTIFY_') || equalsAny(key, AGENT_PROACTIVE_PUSH_EXCLUDED_KEYS),
    },
    {
      id: 'proactive-refresh-guard',
      titleZh: '数据刷新与运行护栏',
      titleEn: 'Data refresh and guards',
      descriptionZh: '独立数据刷新、机会扫描自动重跑、交易日守卫、保留天数与扫描并发。',
      descriptionEn: 'Independent data refresh, opportunity re-run, trading-day guard, retention and scan concurrency.',
      matches: (key) => key.startsWith('AGENT_DATA_REFRESH_')
        || key.startsWith('AGENT_OPPORTUNITY_SCAN_REFRESH')
        || equalsAny(key, AGENT_PROACTIVE_GUARD_KEYS),
    },
    {
      id: 'event-monitor',
      titleZh: '事件监控',
      titleEn: 'Event monitor',
      descriptionZh: '传统告警轮询与规则 JSON，和上面的主动事件分析相互独立。',
      descriptionEn: 'Legacy alert polling and rule JSON, independent from event-driven analysis above.',
      matches: (key) => key.startsWith('AGENT_EVENT_MONITOR_') || key === 'AGENT_EVENT_ALERT_RULES_JSON',
    },
  ],
};

export function groupConfigItems(
  category: SystemConfigCategory,
  items: SystemConfigItem[],
  fallbackTitle?: { zh: string; en: string },
): SettingsFieldGroup[] {
  const definitions = SETTINGS_FIELD_GROUPS[category];
  if (!definitions || items.length === 0) {
    return items.length ? [{ id: `${category}-all`, items }] : [];
  }

  const groups: SettingsFieldGroup[] = definitions.map((definition) => ({
    id: definition.id,
    titleZh: definition.titleZh,
    titleEn: definition.titleEn,
    descriptionZh: definition.descriptionZh,
    descriptionEn: definition.descriptionEn,
    items: [],
  }));
  const assigned = new Set<SystemConfigItem>();

  for (const group of groups) {
    const definition = definitions.find((entry) => entry.id === group.id);
    if (!definition) continue;
    for (const item of items) {
      if (assigned.has(item)) continue;
      if (definition.matches(item.key)) {
        group.items.push(item);
        assigned.add(item);
      }
    }
  }

  const unmatched = items.filter((item) => !assigned.has(item));
  if (unmatched.length) {
    groups.push({
      id: `${category}-other`,
      titleZh: fallbackTitle?.zh ?? '其他设置',
      titleEn: fallbackTitle?.en ?? 'Other settings',
      items: unmatched,
    });
  }

  return groups.filter((group) => group.items.length > 0);
}
