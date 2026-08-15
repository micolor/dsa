import type React from 'react';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Bookmark,
  Building2,
  ChevronDown,
  Clock3,
  Droplet,
  Factory,
  Flame,
  Gem,
  Landmark,
  Loader2,
  Pickaxe,
  Plane,
  Play,
  RefreshCw,
  Search,
  Shield,
  SlidersHorizontal,
  Stethoscope,
  Trees,
  Utensils,
  Wrench,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  screeningApi,
  type ScreeningCandidate,
  type ScreeningHotspotDetail,
  type ScreeningHotspot,
  type ScreeningHotspotsResponse,
  type ScreeningScreenResponse,
  type ScreeningScreenTaskStatus,
  type ScreeningStrategy,
} from '../api/screening';
import { formatParsedApiError, getParsedApiError, toApiErrorMessage, type ParsedApiError } from '../api/error';
import { AppPage, Badge, Button, EmptyState, InlineAlert, Select } from '../components/common';
import { DashboardPanelHeader } from '../components/dashboard/DashboardPanelHeader';
import { CandidateListItem } from '../components/screening/CandidateListItem';
import { formatNumber } from '../components/screening/candidateFormat';
import { useScreeningTaskStore } from '../stores/screeningTaskStore';

const SCREEN_TASK_STORAGE_KEY = 'dsa.screening.activeScreenTask.v1';
const SCREEN_TASK_POLL_INTERVAL_MS = 2000;
const STRATEGY_CATEGORY_LABELS: Record<string, string> = {
  framework: '综合',
  income: '收益',
  momentum: '动量',
  quality: '质量',
  reversal: '反转',
  trend: '趋势',
  value: '价值',
};

const formatStrategyCategory = (value?: string) => {
  const normalized = value?.trim();
  if (!normalized) {
    return '自定义';
  }
  return STRATEGY_CATEGORY_LABELS[normalized.toLowerCase()] || normalized;
};

type PersistedScreenTask = {
  taskId: string;
  market: string;
  strategy: string;
  maxResults: number;
};

const readPersistedScreenTask = (): PersistedScreenTask | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(SCREEN_TASK_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<PersistedScreenTask>;
    if (typeof parsed.taskId !== 'string' || !parsed.taskId.trim()) {
      return null;
    }
    const restoredMaxResults = Number(parsed.maxResults);
    return {
      taskId: parsed.taskId,
      market: typeof parsed.market === 'string' && parsed.market.trim() ? parsed.market : 'cn',
      strategy: typeof parsed.strategy === 'string' && parsed.strategy.trim() ? parsed.strategy : 'dual_low',
      maxResults: Number.isFinite(restoredMaxResults) ? Math.min(100, Math.max(1, restoredMaxResults)) : 3,
    };
  } catch {
    return null;
  }
};

const SCREEN_FORM_STORAGE_KEY = 'dsa.screening.formPrefs.v1';

type ScreenFormPrefs = {
  market: string;
  strategy: string;
  maxResults: number;
};

const readScreenFormPrefs = (): ScreenFormPrefs | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(SCREEN_FORM_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<ScreenFormPrefs>;
    const restoredMaxResults = Number(parsed.maxResults);
    return {
      market: typeof parsed.market === 'string' && parsed.market.trim() ? parsed.market : 'cn',
      strategy: typeof parsed.strategy === 'string' && parsed.strategy.trim() ? parsed.strategy : 'dual_low',
      maxResults: Number.isFinite(restoredMaxResults) ? Math.min(100, Math.max(1, restoredMaxResults)) : 3,
    };
  } catch {
    return null;
  }
};

const persistScreenFormPrefs = (prefs: ScreenFormPrefs) => {
  try {
    window.localStorage.setItem(SCREEN_FORM_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage 写入失败时静默忽略。
  }
};

const SCREEN_RESULT_STORAGE_KEY = 'dsa.screening.lastResult.v1';

const persistScreenResult = (result: ScreeningScreenResponse) => {
  try {
    window.localStorage.setItem(SCREEN_RESULT_STORAGE_KEY, JSON.stringify(result));
  } catch {
    // localStorage 写入失败时静默忽略。
  }
};

const readScreenResult = (): ScreeningScreenResponse | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(SCREEN_RESULT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as ScreeningScreenResponse;
  } catch {
    return null;
  }
};

const persistScreenTask = (task: PersistedScreenTask) => {
  try {
    window.sessionStorage.setItem(SCREEN_TASK_STORAGE_KEY, JSON.stringify(task));
  } catch {
    // Session storage is best-effort; polling still works while the page stays mounted.
  }
};

const clearPersistedScreenTask = () => {
  try {
    window.sessionStorage.removeItem(SCREEN_TASK_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
};

const isUnrecoverableScreenTaskError = (error: ParsedApiError) =>
  error.title === '选股任务不可恢复';

const formatRecoverableScreenTaskPollingError = (error: ParsedApiError) => {
  if (error.category === 'upstream_timeout') {
    return '选股任务仍在后台运行，状态轮询暂时超时，将自动重试。';
  }
  if (error.category === 'upstream_network' || error.category === 'local_connection_failed') {
    return '选股任务仍在后台运行，暂时无法连接本地服务获取状态，将自动重试。';
  }
  return formatParsedApiError(error) || '暂时无法获取选股任务状态，稍后将自动重试。';
};

const HOTSPOT_QUALITY_LABELS: Record<string, string> = {
  available: '可用',
  failed: '不可用',
  partial: '部分可用',
  stale: '缓存',
};

const HOTSPOT_STAGE_LABELS: Record<string, string> = {
  accelerating: '加速主升',
  cooling: '降温退潮',
  diverging: '分歧放量',
  initial: '初次异动',
  persistent_hot: '确认扩散',
  warming: '确认扩散',
  weakening: '降温退潮',
};

const HOTSPOT_ROLE_LABELS: Record<string, string> = {
  core_leader: '核心龙头',
  follower: '助攻',
  laggard: '掉队',
  leader: '核心龙头',
  secondary: '补涨',
};

const HOTSPOT_MISSING_FIELD_LABELS: Record<string, string> = {
  canonical_topic: '标准题材',
  hotspot_constituents: '概念股列表',
  leader_stocks: '核心股',
  live_stocks: '实时概念股行情',
  route: '发酵路径',
  source: '数据来源',
  stocks: '概念股列表',
  timeline: '发酵时间线',
};

const getHotspotStageLabel = (value: unknown) => {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  return HOTSPOT_STAGE_LABELS[text.toLowerCase()] || text;
};

const getHotspotRoleLabel = (value: unknown) => {
  const text = String(value || '').trim();
  if (!text) {
    return '概念股';
  }
  return HOTSPOT_ROLE_LABELS[text.toLowerCase()] || text;
};

const getHotspotQualityLabel = (value: unknown) => {
  const text = String(value || '').trim();
  return HOTSPOT_QUALITY_LABELS[text.toLowerCase()] || '待确认';
};

const toMessageList = (values: string[] | undefined) =>
  Array.isArray(values) ? values.map((value) => String(value).trim()).filter(Boolean) : [];

const KNOWN_SNAPSHOT_SOURCES = new Set(['tushare', 'sina', 'efinance', 'akshare_em', 'em_datacenter', 'baostock']);
const MAX_MESSAGE_DETAIL_LENGTH = 96;

const truncateMessageDetail = (value: string, maxLength = MAX_MESSAGE_DETAIL_LENGTH) => {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
};

const summarizeScreeningDiagnostic = (detail: string) => {
  if (/no_json_found|invalid_response|coverage below threshold/i.test(detail)) {
    return '模型未返回可用的结构化排序结果';
  }
  if (/call_failed/i.test(detail)) {
    return '模型调用失败';
  }
  if (/trade_cal returned no open trading days/i.test(detail)) {
    return '交易日历暂无可用开市日';
  }
  if (/too many requests|rate limit|http\s*429/i.test(detail)) {
    return '请求过于频繁';
  }
  if (/403 forbidden|forbidden|access denied/i.test(detail)) {
    return '访问被拒绝';
  }
  if (/timeout|timed out/i.test(detail)) {
    return '请求超时';
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(detail)) {
    return '网络连接中断';
  }
  if (/missing .*api key|GEMINI_API_KEY|GOOGLE_API_KEY|gemini_api_key/i.test(detail)) {
    return '缺少可用 LLM API Key';
  }
  if (/returned no data|empty/i.test(detail)) {
    return '未返回可用数据';
  }

  const withoutUrl = detail
    .replace(/https?:\/\/\S+/gi, 'URL')
    .replace(/\bwith url:\s*\S+/gi, 'with url: URL')
    .replace(/\burl:\s*\S+/gi, 'url: URL');
  return truncateMessageDetail(withoutUrl);
};

const parseSourceDiagnostic = (value: string) => {
  const match = value.match(/^([a-zA-Z0-9_-]+)\s*[:：]\s*(.+)$/);
  if (!match) {
    return null;
  }
  return {
    source: match[1],
    detail: match[2],
  };
};

const normalizeScreenMessageKey = (value: string) => {
  const formatted = formatScreenMessage(value);
  return formatted ? formatted.trim().toLowerCase() : value.trim().toLowerCase();
};

const formatScreenMessage = (value: string) => {
  if (/^DSA provider context applied \d+ of \d+ candidates/i.test(value)) {
    return '';
  }
  if (/^LLM ranking skipped:\s*no LLM config/i.test(value)) {
    return '未配置智能重排模型，当前使用确定性因子排序。';
  }
  if (/^LLM ranking failed/i.test(value)) {
    return `未完成智能重排：${summarizeScreeningDiagnostic(value)}；当前结果继续使用确定性因子评分。`;
  }
  if (/no_json_found|invalid_response|coverage below threshold|call_failed/i.test(value)) {
    return `未完成智能重排：${summarizeScreeningDiagnostic(value)}；当前结果继续使用确定性因子评分。`;
  }
  if (/^(?:LLM ranking prompt|LLM context) truncated:/i.test(value)) {
    return '';
  }
  if (/^(?:Remote post-analysis cap|Risk veto excluded|Snapshot hard-filter waterfall|Daily hard-filter waterfall|Daily hard-filter rejections|Candidate context collected rows=)/i.test(value)) {
    return '';
  }
  if (/^Daily K-line (?:enrichment attempted|sources|quality flags|source ordering|source health):?/i.test(value)) {
    return '';
  }
  if (/^Daily K-line enrichment row errors:/i.test(value)) {
    return '部分候选的日线数据未能补齐，结果已按可用数据生成。';
  }
  if (/^Daily K-line enrichment skipped:/i.test(value)) {
    return '可选日线数据补充未完成，结果已按快照数据生成。';
  }
  if (/^Candidate context row errors:/i.test(value)) {
    return '部分候选的辅助数据未能补齐。';
  }
  if (/^Industry\/concepts enrichment:/i.test(value)) {
    return '部分行业或题材信息未能补齐。';
  }
  if (/^DSA deep analysis failed for /i.test(value)) {
    return '部分候选的深度分析未完成。';
  }

  const snapshotFallback = value.match(/^Snapshot source fallback:\s*(.+)$/i);
  if (snapshotFallback) {
    const parsed = parseSourceDiagnostic(snapshotFallback[1]);
    if (parsed) {
      return `数据源降级：${parsed.source}（${summarizeScreeningDiagnostic(parsed.detail)}）`;
    }
    return `数据源降级：${summarizeScreeningDiagnostic(snapshotFallback[1])}`;
  }

  const parsed = parseSourceDiagnostic(value);
  if (parsed && KNOWN_SNAPSHOT_SOURCES.has(parsed.source.toLowerCase())) {
    return `数据源降级：${parsed.source}（${summarizeScreeningDiagnostic(parsed.detail)}）`;
  }
  return truncateMessageDetail(value);
};

const getScreenMessages = (meta: ScreeningScreenResponse | null) => {
  if (!meta) {
    return [];
  }
  const messages: string[] = [];
  const seen = new Set<string>();
  [...toMessageList(meta.warnings), ...toMessageList(meta.sourceErrors), ...toMessageList(meta.llmParseErrors)].forEach(
    (value) => {
      const key = normalizeScreenMessageKey(value);
      if (seen.has(key)) {
        return;
      }
      const message = formatScreenMessage(value);
      if (!message) {
        return;
      }
      seen.add(key);
      messages.push(message);
    },
  );
  return messages;
};

const isRunningScreenTask = (status: string | undefined | null) => status === 'pending' || status === 'processing';

const formatScreenTaskFailure = (value: string | null | undefined) => {
  const text = String(value || '').trim();
  if (!text) {
    return '选股任务失败，请稍后重试。';
  }
  return `选股任务失败：${summarizeScreeningDiagnostic(text)}`;
};

const SCREENING_HOTSPOT_NO_CACHE_HINT = 'No cached Screening hotspot snapshot. Click refresh to fetch live hotspots.';
const SCREENING_HOTSPOT_UNAVAILABLE_CODE = 'eastmoney_hotspot_unavailable';

const formatHotspotEmptyMessage = (result: ScreeningHotspotsResponse) => {
  const message = String(result.message || '').trim();
  const sourceErrors = result.sourceErrors || [];
  if (message && sourceErrors.includes(SCREENING_HOTSPOT_UNAVAILABLE_CODE)) {
    return message;
  }
  if (message === SCREENING_HOTSPOT_NO_CACHE_HINT) {
    return '暂无热点缓存';
  }
  const sourceError = sourceErrors[0];
  if (sourceError) {
    return `热点题材暂未返回数据：${summarizeScreeningDiagnostic(sourceError)}`;
  }
  return '热点题材暂未返回数据';
};

const ScreenAlertMessage: React.FC<{ messages: string[] }> = ({ messages }) => {
  if (messages.length <= 1) {
    return <span>{messages[0]}</span>;
  }
  return (
    <ul className="list-disc space-y-1 pl-4">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
};

const getRouteTimeLabel = (item: ScreeningHotspotDetail['route'][number]) => {
  const rawTime = item.publishedAt || item.date || item.time || '';
  if (!rawTime) {
    return '时间待确认';
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawTime)) {
    return rawTime;
  }
  const parsed = new Date(rawTime);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  return rawTime;
};

const formatHotspotRouteTitle = (value: string) => {
  const text = String(value || '').trim();
  const normalized = text.toLowerCase();
  if (normalized === 'current fermentation') {
    return '当前发酵';
  }
  if (normalized === 'news catalyst') {
    return '消息催化';
  }
  return text || '热点变化';
};

const formatHotspotRouteDescription = (value: string) => {
  const text = String(value || '').trim();
  if (!text) {
    return '暂无更多说明。';
  }
  const parts = text.split(/\s*;\s*/).map((part) => part.trim()).filter(Boolean);
  if (parts.some((part) => /\b(?:heat|stage|leaders?)\b/i.test(part))) {
    const localized = parts.map((part) => {
      const heat = part.match(/^(.*?)\s+heat\s+(-?\d+(?:\.\d+)?)$/i);
      if (heat) {
        return `${heat[1]}热度 ${formatNumber(heat[2], 1)}`;
      }
      const stage = part.match(/^stage\s+(.+)$/i);
      if (stage) {
        return `阶段 ${getHotspotStageLabel(stage[1])}`;
      }
      const leaders = part.match(/^leaders?\s+(.+)$/i);
      if (leaders) {
        return `核心股 ${leaders[1].split(/\s*,\s*/).filter(Boolean).join('、')}`;
      }
      return part;
    });
    return `${localized.join('，')}。`;
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache/i.test(text)) {
    return '热点数据出现新变化。';
  }
  return text;
};

const getHotspotMissingFieldLabels = (values: string[] | undefined) => {
  const labels = (values || []).map((value) => HOTSPOT_MISSING_FIELD_LABELS[String(value).trim().toLowerCase()] || '部分明细');
  return [...new Set(labels)];
};

const formatHotspotDiagnostic = (value: string) => {
  const text = String(value || '').trim();
  const timeoutSeconds = text.match(/timed out after\s*(\d+(?:\.\d+)?)s/i);
  if (timeoutSeconds) {
    return `热点明细请求超时（${timeoutSeconds[1]} 秒）`;
  }
  if (/timeout|timed out/i.test(text)) {
    return '热点明细请求超时';
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(text)) {
    return '热点数据源连接中断';
  }
  if (/eastmoney_hotspot_unavailable|returned no data|no live hotspot rows|\bempty\b/i.test(text)) {
    return '热点数据源暂未返回数据';
  }
  if (/rate limit|too many requests|http\s*429/i.test(text)) {
    return '热点数据请求过于频繁';
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache|^[a-z0-9_.:-]+$/i.test(text)) {
    return '部分热点数据暂不可用';
  }
  if (/[\u0080-\uFFFF]/.test(text)) {
    return truncateMessageDetail(text);
  }
  return '部分热点数据暂不可用';
};

const getHotspotDiagnosticMessages = (values: string[] | undefined) =>
  [...new Set((values || []).map(formatHotspotDiagnostic).filter(Boolean))].slice(0, 4);

const hasHotspotDetailDegradation = (detail: ScreeningHotspotDetail) => {
  if ((detail.missingFields || []).length > 0) {
    return true;
  }
  const qualityStatus = String(detail.qualityStatus || '').trim().toLowerCase();
  if (qualityStatus) {
    return qualityStatus !== 'available';
  }
  return (detail.sourceErrors || []).length > 0;
};

const getHotspotFallbackLabel = (detail: ScreeningHotspotDetail) => {
  if (detail.stale || detail.cacheUsed) {
    return detail.staleAgeHours != null
      ? `缓存回退 ${formatNumber(detail.staleAgeHours, 1)}h`
      : '缓存回退';
  }
  return '备用数据源';
};

const getHotspotSummaryText = (detail: ScreeningHotspotDetail, hotspot?: ScreeningHotspot) => {
  const summaryDetail = detail.summaryDetail || {};
  const heatScore = summaryDetail.heatScore ?? summaryDetail.heat_score ?? hotspot?.heatScore;
  const stage = summaryDetail.stage ?? hotspot?.stage ?? hotspot?.state;
  const rawLeaders = summaryDetail.leaders ?? hotspot?.leaders;
  const leaders = Array.isArray(rawLeaders)
    ? rawLeaders.map((value) => String(value).trim()).filter(Boolean).slice(0, 3)
    : [];
  const parts: string[] = [];
  if (heatScore != null && !Number.isNaN(Number(heatScore))) {
    parts.push(`热度 ${formatNumber(heatScore, 1)}`);
  }
  if (stage) {
    parts.push(`阶段 ${getHotspotStageLabel(stage)}`);
  }
  if (leaders.length > 0) {
    parts.push(`核心股 ${leaders.join('、')}`);
  }
  if (parts.length > 0) {
    return `${detail.name || detail.canonicalTopic || detail.topic}：${parts.join('，')}。`;
  }
  const summary = String(detail.summary || '').trim();
  if (summary && !/\b(?:heat|stage|leaders?|quality status|available|partial|stale|failed)\b|Dsa[A-Z]|Provider\b|stock_board_/i.test(summary)) {
    return summary;
  }
  return '已加载热点详情。';
};

const buildHotspotPreviewDetail = (hotspot: ScreeningHotspot): ScreeningHotspotDetail => {
  const leaders = (hotspot.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  const stage = getHotspotStageLabel(hotspot.stage || hotspot.state);
  const descriptionParts = [`${hotspot.name || hotspot.topic}热度 ${formatHotspotMetric(hotspot.heatScore)}`];
  if (stage) {
    descriptionParts.push(`阶段 ${stage}`);
  }
  if (leaders.length > 0) {
    descriptionParts.push(`核心股 ${leaders.slice(0, 3).join('、')}`);
  }
  const stocks = (hotspot.leaderStocks || []).slice(0, 10);
  return {
    enabled: true,
    provider: 'akshare',
    topic: hotspot.topic,
    name: hotspot.name || hotspot.topic,
    canonicalTopic: hotspot.topic,
    summaryDetail: {
      heatScore: hotspot.heatScore,
      stage: hotspot.stage || hotspot.state,
      leaders,
    },
    route: [{ title: '当前发酵', description: `${descriptionParts.join('，')}。` }],
    stocks,
    stockCount: hotspot.sampleStockCount ?? stocks.length,
    sourceErrors: hotspot.sourceErrors,
    qualityStatus: hotspot.qualityStatus,
    missingFields: hotspot.missingFields,
    fallbackUsed: hotspot.fallbackUsed,
    stale: hotspot.stale,
    staleAgeHours: hotspot.staleAgeHours,
    cacheUsed: hotspot.cacheUsed,
    cachedAt: hotspot.cachedAt,
  };
};

const stripHotspotSearchAugmentation = (detail: ScreeningHotspotDetail): ScreeningHotspotDetail => {
  const baseDetail: ScreeningHotspotDetail = {
    ...detail,
    route: (detail.route || []).filter((item) => !item.searchResult),
    ...(detail.timeline
      ? { timeline: detail.timeline.filter((item) => !item.searchResult) }
      : {}),
  };
  delete baseDetail.newsSearchRequested;
  delete baseDetail.newsSearchStatus;
  return baseDetail;
};

const stripHotspotSearchAugmentationByTopic = (
  details: Record<string, ScreeningHotspotDetail>,
) => Object.fromEntries(
  Object.entries(details).map(([topic, detail]) => [topic, stripHotspotSearchAugmentation(detail)]),
) as Record<string, ScreeningHotspotDetail>;

const getHotspotRouteItems = (detail: ScreeningHotspotDetail) => {
  const route = detail.route || [];
  if (route.length > 0) {
    return route;
  }
  return detail.timeline || [];
};

const formatHotspotMetric = (value: unknown, digits = 1) => {
  const formatted = formatNumber(value, digits);
  return formatted === '-' ? '观察中' : formatted;
};

const getHotspotLeadersText = (item: ScreeningHotspot) => {
  const leaders = (item.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  if (leaders.length > 0) {
    return leaders.slice(0, 2).join('、');
  }
  return '观察中';
};

const getHotspotSampleText = (item: ScreeningHotspot) => {
  if (item.sampleStockCount == null || Number.isNaN(Number(item.sampleStockCount))) {
    return '活跃股观察中';
  }
  return `覆盖 ${item.sampleStockCount} 股`;
};

const formatStockChangeText = (value: unknown) => {
  const formatted = formatNumber(value);
  return formatted === '-' ? '暂无行情' : `${formatted}%`;
};

const formatHotspotUpdatedAt = (value: string | null) => {
  if (!value) {
    return '待刷新';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
};

const getHotspotStrength = (item: ScreeningHotspot, index: number) => {
  const heat = Number(item.heatScore ?? 0);
  const changePct = Number(item.changePct ?? 0);
  if (index === 0 || heat >= 90 || changePct >= 8) {
    return { label: '强势领先', className: 'bg-red-500/10 text-red-500' };
  }
  if (heat >= 80 || changePct >= 5) {
    return { label: '强势', className: 'bg-blue-500/10 text-blue-500' };
  }
  return { label: '较强', className: 'bg-cyan/10 text-cyan' };
};

const HOTSPOT_ICON_RULES: Array<{
  pattern: RegExp;
  icon: React.ComponentType<{ className?: string }>;
  className: string;
}> = [
  { pattern: /金|银|铜|铝|铅|锌|钼|钴|镍|贵金属|矿|有色/, icon: Pickaxe, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /黄金|珠宝/, icon: Gem, className: 'bg-amber-500/10 text-amber-500' },
  { pattern: /油|气|能源|煤/, icon: Droplet, className: 'bg-yellow-700/10 text-yellow-700' },
  { pattern: /金融|券商|银行|保险|资本/, icon: Landmark, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /航空|机场|航天|运输/, icon: Plane, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /林业|农业|种植/, icon: Trees, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /医疗|诊断|卫生|医药/, icon: Stethoscope, className: 'bg-teal-500/10 text-teal-500' },
  { pattern: /食品|餐饮|酒/, icon: Utensils, className: 'bg-violet-500/10 text-violet-500' },
  { pattern: /工业|制造|修理|机械|设备/, icon: Wrench, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /租赁|地产|建筑/, icon: Building2, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /电|芯片|算力|AI|机器人/, icon: Factory, className: 'bg-indigo-500/10 text-indigo-500' },
  { pattern: /保险|安全/, icon: Shield, className: 'bg-blue-500/10 text-blue-500' },
];

const getHotspotIcon = (topic: string) => {
  const match = HOTSPOT_ICON_RULES.find((rule) => rule.pattern.test(topic));
  return match || { icon: Activity, className: 'bg-cyan/10 text-cyan' };
};

const MiniSparkline: React.FC<{ score?: number | null; selected?: boolean }> = ({ score, selected }) => {
  const normalized = Number.isFinite(Number(score)) ? Math.max(0, Math.min(100, Number(score))) : 0;
  return (
    <div className="flex h-8 w-20 flex-col justify-end gap-1" aria-hidden="true">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/15">
        <div
          className={`h-full rounded-full transition-all ${selected ? 'bg-orange-500' : 'bg-blue-500'}`}
          style={{ width: `${normalized}%` }}
        />
      </div>
      <span className={`text-right text-[10px] font-medium tabular-nums ${selected ? 'text-orange-500' : 'text-blue-500'}`}>
        {Math.round(normalized)}
      </span>
    </div>
  );
};

const HotspotCard: React.FC<{
  item: ScreeningHotspot;
  index: number;
  selected: boolean;
  onSelect: (topic: string) => void;
}> = memo(({ item, index, selected, onSelect }) => {
  const strength = getHotspotStrength(item, index);
  const iconMeta = getHotspotIcon(item.name || item.topic);
  const Icon = iconMeta.icon;
  return (
    <button
      className={`group relative min-h-[116px] overflow-hidden rounded-xl border px-3 py-3 text-left transition-all ${
        selected
          ? 'border-orange-400 bg-gradient-to-br from-orange-500/10 via-card to-card shadow-[0_0_0_1px_rgba(249,115,22,0.16),0_18px_44px_rgba(249,115,22,0.14)]'
          : 'border-subtle bg-card/70 backdrop-blur-md hover:-translate-y-0.5 hover:border-orange-300/70 hover:shadow-soft-card'
      }`}
      type="button"
      onClick={() => onSelect(item.topic)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold ${
              index < 3 ? 'bg-orange-500 text-white shadow-[0_8px_24px_rgba(249,115,22,0.24)]' : 'bg-surface text-secondary-text'
            }`}
          >
            {index + 1}
          </span>
          <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${iconMeta.className}`}>
            <Icon className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold text-foreground">{item.name || item.topic}</p>
            <span className={`mt-1 inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${strength.className}`}>
              {strength.label}
            </span>
          </div>
        </div>
        <span className="shrink-0 text-2xl font-black leading-none text-orange-500">
          {formatNumber(item.heatScore, 0)}
        </span>
      </div>
      <div className="mt-4 grid max-w-[72%] gap-1 text-[11px] text-secondary-text">
        <span>涨跌幅 <strong className="font-semibold text-foreground">{formatHotspotMetric(item.changePct)}%</strong></span>
        <span>趋势 <strong className="font-semibold text-foreground">{formatHotspotMetric(item.trendScore)}</strong> · 持续 <strong className="font-semibold text-foreground">{formatHotspotMetric(item.persistenceScore)}</strong></span>
        <span>{getHotspotSampleText(item)} · 龙头 {getHotspotLeadersText(item)}</span>
      </div>
      <div className="absolute bottom-3 right-3 opacity-95 transition-transform group-hover:scale-105">
        <MiniSparkline score={item.heatScore} selected={selected} />
      </div>
    </button>
  );
});
HotspotCard.displayName = 'HotspotCard';

const StockScreeningPage: React.FC = () => {
  const navigate = useNavigate();
  const [restoredTask] = useState<PersistedScreenTask | null>(() => readPersistedScreenTask());
  const [formPrefs] = useState<ScreenFormPrefs | null>(() => readScreenFormPrefs());
  const [restoredResult] = useState<ScreeningScreenResponse | null>(() => readScreenResult());
  const [enabled, setEnabled] = useState(false);
  const [available, setAvailable] = useState(false);
  // 当前仅支持 A 股场景：market 为固定值，页面没有市场切换控件，setter 从未使用。
  // 若未来要支持多市场，需补市场选择控件与 setter，再把这里改回 state。
  const market = formPrefs?.market ?? restoredTask?.market ?? 'cn';
  const [strategy, setStrategy] = useState(formPrefs?.strategy ?? restoredTask?.strategy ?? 'dual_low');
  const [strategies, setStrategies] = useState<ScreeningStrategy[]>([]);
  const [maxResults, setMaxResults] = useState(formPrefs?.maxResults ?? restoredTask?.maxResults ?? 3);

  // 市场/策略/返回数量改变时持久化到 localStorage，刷新/重开后仍记住上次选择。
  useEffect(() => {
    persistScreenFormPrefs({ market, strategy, maxResults });
  }, [market, strategy, maxResults]);

  useEffect(() => () => { mountedRef.current = false; }, []);
  const [candidates, setCandidates] = useState<ScreeningCandidate[]>(restoredResult?.candidates ?? []);
  const [hotspots, setHotspots] = useState<ScreeningHotspot[]>([]);
  const [hotspotsUpdatedAt, setHotspotsUpdatedAt] = useState<string | null>(null);
  const [hotspotsExpanded, setHotspotsExpanded] = useState(false);
  const [selectedHotspotTopic, setSelectedHotspotTopic] = useState<string | null>(null);
  const selectedHotspotTopicRef = useRef<string | null>(null);
  const hotspotDetailRequestIdRef = useRef(0);
  const hotspotDetailsByTopicRef = useRef<Record<string, ScreeningHotspotDetail>>({});
  // 镜像 hotspots，供 handleHotspotSelect 读取最新列表做预览 fallback，而不把数组放入
  // 回调依赖（否则每次刷新都会使已 memo 的 HotspotCard 失效，全量重渲染）。
  const hotspotsRef = useRef<ScreeningHotspot[]>([]);
  const mountedRef = useRef(true);
  const [hotspotDetail, setHotspotDetail] = useState<ScreeningHotspotDetail | null>(null);
  const [loadingHotspotDetail, setLoadingHotspotDetail] = useState(false);
  const [searchingHotspotNews, setSearchingHotspotNews] = useState(false);
  const [hotspotDetailError, setHotspotDetailError] = useState('');
  const [loadingHotspots, setLoadingHotspots] = useState(false);
  const [hotspotError, setHotspotError] = useState('');
  const [screenMeta, setScreenMeta] = useState<ScreeningScreenResponse | null>(restoredResult);
  const [expandedCode, setExpandedCode] = useState<string | null>(restoredResult?.candidates?.[0]?.code ?? null);
  const [loading, setLoading] = useState(Boolean(restoredTask?.taskId));
  const [enabling, setEnabling] = useState(false);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState('');
  const [strategyLoadError, setStrategyLoadError] = useState('');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(restoredTask?.taskId ?? null);
  const setScreenTask = useScreeningTaskStore((state) => state.setScreenTask);
  const clearScreenTask = useScreeningTaskStore((state) => state.clearScreenTask);

  const selectedStrategy = useMemo(() => strategies.find((item) => item.id === strategy), [strategies, strategy]);
  const selectedStrategyTitle = selectedStrategy?.name || selectedStrategy?.title || '自定义策略';
  const selectedStrategyTag = formatStrategyCategory(
    selectedStrategy?.category || selectedStrategy?.tag || selectedStrategy?.tags?.[0],
  );
  const displayedStrategy = selectedStrategy ? selectedStrategyTitle : `自定义策略 (${strategy})`;
  const screenMessages = useMemo(() => getScreenMessages(screenMeta), [screenMeta]);
  const selectedHotspot = useMemo(
    () => hotspots.find((item) => item.topic === selectedHotspotTopic),
    [hotspots, selectedHotspotTopic],
  );
  const factorRanking = Boolean(screenMeta && (screenMeta.rankingMode === 'factor' || screenMeta.llmRanked === false));
  const llmFailed = Boolean(factorRanking && screenMeta?.llmFailureReason);
  const alertMessages = llmFailed
    ? screenMessages.length > 0
      ? screenMessages
      : ['智能重排未完成，当前候选继续使用确定性因子评分。']
    : screenMessages;
  const isScreeningEnabled = enabled && available;
  const statusText = isScreeningEnabled ? '选股已开启' : '选股未开启';

  const applyScreenResult = useCallback((result: ScreeningScreenResponse) => {
    const nextCandidates = result.candidates || [];
    setScreenMeta(result);
    setCandidates(nextCandidates);
    setExpandedCode(nextCandidates[0]?.code ?? null);
    persistScreenResult(result);
  }, []);

  const clearScreeningResults = () => {
    setCandidates([]);
    setScreenMeta(null);
    setExpandedCode(null);
  };

  const loadHotspotDetail = useCallback(async (
    topic: string,
    options: { refresh?: boolean; includeSearch?: boolean } = {},
  ) => {
    if (!topic) {
      return;
    }
    const cachedDetail = !options.refresh && !options.includeSearch
      ? hotspotDetailsByTopicRef.current[topic]
      : null;
    if (cachedDetail) {
      // 缓存命中同样递增请求序号，让任何仍在途的旧请求作废（双重防线，不依赖 topic 校验）。
      hotspotDetailRequestIdRef.current += 1;
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
      return;
    }
    const requestId = hotspotDetailRequestIdRef.current + 1;
    hotspotDetailRequestIdRef.current = requestId;
    const isCurrentRequest = () => hotspotDetailRequestIdRef.current === requestId;
    const canApplyRequest = () => isCurrentRequest() && selectedHotspotTopicRef.current === topic;
    setLoadingHotspotDetail(!options.includeSearch);
    setSearchingHotspotNews(Boolean(options.includeSearch));
    setHotspotDetail((currentDetail) => (currentDetail?.topic === topic ? currentDetail : null));
    setHotspotDetailError('');
    try {
      const detail = await screeningApi.getHotspotDetail({
        topic,
        provider: 'akshare',
        refresh: options.refresh ?? false,
        ...(options.includeSearch ? { includeSearch: true } : {}),
      });
      if (!canApplyRequest()) {
        return;
      }
      const cacheableDetail = options.includeSearch
        ? hotspotDetailsByTopicRef.current[topic] || stripHotspotSearchAugmentation(detail)
        : stripHotspotSearchAugmentation(detail);
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        [topic]: cacheableDetail,
      };
      setHotspotDetail(options.includeSearch ? detail : cacheableDetail);
      if (options.includeSearch && detail.newsSearchStatus === 'no_results') {
        setHotspotDetailError('暂未搜到该题材近期的有效消息。');
      } else if (options.includeSearch && detail.newsSearchStatus !== 'available') {
        setHotspotDetailError('消息搜索失败，请稍后重试。');
      }
    } catch (err) {
      if (!canApplyRequest()) {
        return;
      }
      if (!options.includeSearch) {
        setHotspotDetail(null);
      }
      setHotspotDetailError(toApiErrorMessage(
        err,
        options.includeSearch ? '消息搜索失败，请稍后重试。' : '热点题材详情加载失败，请稍后重试。',
      ));
    } finally {
      if (isCurrentRequest()) {
        setLoadingHotspotDetail(false);
        setSearchingHotspotNews(false);
      }
    }
  }, []);

  const loadStrategies = useCallback(async () => {
    setLoadingStrategies(true);
    try {
      setStrategyLoadError('');
      const result = await screeningApi.getStrategies();
      if (!mountedRef.current) {
        return;
      }
      const loadedStrategies = result.strategies || [];
      setStrategies(loadedStrategies);
      if (loadedStrategies.length > 0) {
        setStrategy((currentStrategy) =>
          loadedStrategies.some((item) => item.id === currentStrategy) ? currentStrategy : loadedStrategies[0].id,
        );
      }
    } catch (err) {
      setStrategies([]);
      setStrategyLoadError(err instanceof Error ? err.message : '策略列表加载失败');
    } finally {
      setLoadingStrategies(false);
    }
  }, []);

  const loadHotspots = useCallback(async (refresh = false) => {
    setLoadingHotspots(true);
    setHotspotError('');
    try {
      const result = await screeningApi.getHotspots({ provider: 'akshare', top: 12, refresh });
      if (!mountedRef.current) {
        return;
      }
      const nextHotspots = result.hotspots || [];
      const nextDetails = stripHotspotSearchAugmentationByTopic(result.details || {});
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        ...nextDetails,
      };
      const currentTopic = selectedHotspotTopicRef.current;
      const retainedTopic = Boolean(currentTopic && nextHotspots.some((item) => item.topic === currentTopic));
      const nextTopic = retainedTopic ? currentTopic : null;
      hotspotsRef.current = nextHotspots;
      setHotspots(nextHotspots);
      setHotspotsUpdatedAt(result.cachedAt || (nextHotspots.length > 0 ? new Date().toISOString() : null));
      setSelectedHotspotTopic(nextTopic);
      selectedHotspotTopicRef.current = nextTopic;
      setHotspotDetailError('');
      if (nextTopic && nextDetails[nextTopic]) {
        setHotspotDetail(nextDetails[nextTopic]);
        setLoadingHotspotDetail(false);
      } else if (!retainedTopic) {
        setHotspotDetail(null);
      } else if (refresh && nextTopic) {
        // A refreshed list and a retained detail must describe the same source
        // snapshot. The list endpoint intentionally omits details by default,
        // so explicitly bypass the detail cache for the retained topic.
        await loadHotspotDetail(nextTopic, { refresh: true });
      }
      if (nextHotspots.length === 0) {
        setHotspotError(formatHotspotEmptyMessage(result));
      }
    } catch (err) {
      setHotspotError(toApiErrorMessage(err, '热点题材加载失败，请稍后重试。'));
    } finally {
      setLoadingHotspots(false);
    }
  }, [loadHotspotDetail]);

  const handleHotspotSelect = useCallback((topic: string) => {
    selectedHotspotTopicRef.current = topic;
    setSelectedHotspotTopic(topic);
    const cachedDetail = hotspotDetailsByTopicRef.current[topic];
    if (cachedDetail) {
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
    } else {
      const preview = hotspotsRef.current.find((item) => item.topic === topic);
      setHotspotDetail((currentDetail) => (
        currentDetail?.topic === topic ? currentDetail : preview ? buildHotspotPreviewDetail(preview) : null
      ));
    }
  }, []);

  const toggleHotspotsExpanded = useCallback(() => {
    setHotspotsExpanded((expanded) => {
      const nextExpanded = !expanded;
      if (!nextExpanded) {
        selectedHotspotTopicRef.current = null;
        setSelectedHotspotTopic(null);
        setHotspotDetail(null);
        setHotspotDetailError('');
      }
      return nextExpanded;
    });
  }, []);

  const handleAnalyzeHotspotStock = useCallback((stock: ScreeningHotspotDetail['stocks'][number]) => {
    const stockCode = String(stock.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(stock.name || stockCode).trim();
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_hotspot',
        skills: ['hot_theme'],
      },
    });
  }, [navigate]);

  const handleAnalyzeCandidate = useCallback((candidate: ScreeningCandidate) => {
    const stockCode = String(candidate.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(candidate.name || stockCode).trim();
    const analysisSkills = (selectedStrategy?.analysisSkills || []).filter(Boolean);
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_result',
        ...(analysisSkills.length > 0 ? { skills: analysisSkills } : {}),
      },
    });
  }, [navigate, selectedStrategy]);

  // 展开状态驱动，身份稳定，配合 CandidateListItem 的 memo 让未展开项在父重渲染时跳过。
  const handleToggleCandidate = useCallback((code: string) => {
    setExpandedCode((current) => (current === code ? null : code));
  }, []);

  useEffect(() => {
    selectedHotspotTopicRef.current = selectedHotspotTopic;
  }, [selectedHotspotTopic]);

  useEffect(() => {
    if (!selectedHotspotTopic) {
      return;
    }
    void loadHotspotDetail(selectedHotspotTopic);
  }, [loadHotspotDetail, selectedHotspotTopic]);

  useEffect(() => {
    let active = true;
    screeningApi
      .getStatus()
      .then((status) => {
        if (!active) {
          return;
        }
        setEnabled(status.enabled);
        setAvailable(status.available);
        if (status.enabled && status.available) {
          void loadStrategies();
          void loadHotspots(false);
        }
      })
      .catch(() => {
        if (active) {
          setEnabled(false);
          setAvailable(false);
        }
      });
    return () => {
      active = false;
    };
  }, [loadHotspots, loadStrategies]);

  // 恢复 sessionStorage 中的在途选股任务时，先发布到全局任务图标，避免首次轮询前图标为空。
  useEffect(() => {
    if (!restoredTask?.taskId) {
      return;
    }
    setScreenTask({
      taskId: restoredTask.taskId,
      title: `选股任务 (${restoredTask.strategy})`,
      progress: 10,
      message: '正在恢复选股任务状态...',
      status: 'pending',
    });
    // 仅在挂载时发布一次；后续进度由轮询更新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeTaskId) {
      return undefined;
    }

    const pollingTaskId = activeTaskId;
    let active = true;
    let timer: ReturnType<typeof window.setTimeout> | undefined;

    function finishTask() {
      clearPersistedScreenTask();
      setActiveTaskId(null);
      setLoading(false);
      clearScreenTask();
    }

    function applyTaskStatus(task: ScreeningScreenTaskStatus) {
      const nextProgress = Number(task.progress ?? 0);
      const progress = Number.isFinite(nextProgress) ? nextProgress : 0;

      if (task.status === 'completed') {
        if (task.result) {
          applyScreenResult(task.result);
          setError('');
        } else {
          setError('选股任务已完成，但服务端未返回候选结果。');
          setCandidates([]);
          setScreenMeta(null);
        }
        finishTask();
        return;
      }

      if (task.status === 'failed') {
        setCandidates([]);
        setScreenMeta(null);
        setExpandedCode(null);
        setError(formatScreenTaskFailure(task.error || task.message));
        finishTask();
        return;
      }

      if (isRunningScreenTask(task.status)) {
        setLoading(true);
        setScreenTask({
          taskId: pollingTaskId,
          title: `${displayedStrategy} 选股`,
          progress,
          message: task.message || '',
          status: task.status,
        });
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
        return;
      }

      setError(`选股任务返回未知状态：${task.status || 'unknown'}`);
      finishTask();
    }

    async function pollTask() {
      try {
        const task = await screeningApi.getScreenTask(pollingTaskId);
        if (!active) {
          return;
        }
        applyTaskStatus(task);
      } catch (err) {
        if (!active) {
          return;
        }
        const parsedError = getParsedApiError(err);
        if (isUnrecoverableScreenTaskError(parsedError)) {
          setError(formatParsedApiError(parsedError) || '选股任务不可恢复，请重新提交。');
          setCandidates([]);
          setScreenMeta(null);
          finishTask();
          return;
        }
        setError(formatRecoverableScreenTaskPollingError(parsedError));
        setLoading(true);
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
      }
    }

    void pollTask();

    return () => {
      active = false;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [activeTaskId, applyScreenResult, clearScreenTask, displayedStrategy, setScreenTask]);

  const handleEnable = async () => {
    setEnabling(true);
    setError('');
    try {
      await screeningApi.enable();
      setEnabled(true);
      setAvailable(true);
      await loadStrategies();
    } catch (err) {
      try {
        const status = await screeningApi.getStatus();
        setEnabled(status.enabled);
        setAvailable(status.available);
      } catch {
        setEnabled(false);
        setAvailable(false);
      }
      setError(err instanceof Error ? err.message : '开启选股失败');
    } finally {
      setEnabling(false);
    }
  };

  const handleStrategyChange = (nextStrategy: string) => {
    if (nextStrategy !== strategy) {
      clearScreeningResults();
    }
    setStrategy(nextStrategy);
  };

  const handleMaxResultsChange = (nextMaxResults: number) => {
    // 提交前收敛到 1..100：手输越界（如 500、0）或清空（Number('')===0）都归位，
    // NaN 回退默认值 3，避免把非法返回数量传给后端。
    const clamped = Number.isFinite(nextMaxResults)
      ? Math.min(100, Math.max(1, Math.round(nextMaxResults)))
      : 3;
    if (clamped !== maxResults) {
      clearScreeningResults();
    }
    setMaxResults(clamped);
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setScreenMeta(null);
    try {
      const task = await screeningApi.startScreen({ market, strategy, maxResults });
      persistScreenTask({
        taskId: task.taskId,
        market,
        strategy,
        maxResults,
      });
      setActiveTaskId(task.taskId);
      setScreenTask({
        taskId: task.taskId,
        traceId: task.traceId ?? undefined,
        title: `${displayedStrategy} 选股`,
        progress: 0,
        message: task.message || '选股任务已提交',
        status: 'pending',
      });
    } catch (err) {
      setCandidates([]);
      setLoading(false);
      setError(toApiErrorMessage(err, '选股任务提交失败，请稍后重试。'));
    }
  };

  return (
    <AppPage className="space-y-6 pb-12 pt-6">
      <div className="flex items-center">
        <div className="inline-flex w-fit items-center gap-2 rounded-full border border-border/60 bg-card/70 px-3 py-1.5 text-xs shadow-soft-card backdrop-blur-md">
          <span className={`h-2 w-2 rounded-full ${isScreeningEnabled ? 'bg-success' : 'bg-warning'}`} />
          <span className="font-medium text-secondary-text">{statusText}</span>
        </div>
      </div>

      {!enabled ? (
        <InlineAlert
          variant="info"
          title="选股未开启"
          message="开启后即可运行选股策略。"
          action={
            <Button size="sm" isLoading={enabling} loadingText="开启中..." onClick={() => void handleEnable()}>
              开启选股
            </Button>
          }
        />
      ) : null}

      {enabled && !available ? (
        <InlineAlert
          variant="warning"
          title="选股功能不可用"
          message="请检查后端日志、策略文件和基础数据依赖后重启服务。"
        />
      ) : null}

      {error ? <InlineAlert variant="danger" title="调用失败" message={error} /> : null}

      <section className="glass-card !border-transparent p-4 md:p-5">
        <DashboardPanelHeader
          className="mb-4"
          leading={
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-orange-500/10 text-orange-500 shadow-[0_10px_30px_rgba(249,115,22,0.16)]">
              <Flame className="h-5 w-5" />
            </span>
          }
          title="热点题材"
          titleClassName="text-sm font-medium"
          actions={
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={!isScreeningEnabled}
                onClick={toggleHotspotsExpanded}
              >
                <Bookmark className="h-4 w-4" />
                {hotspotsExpanded ? '收起热点题材' : `展开热点题材${hotspots.length ? `（${hotspots.length}）` : ''}`}
                <ChevronDown className={`h-4 w-4 transition-transform ${hotspotsExpanded ? 'rotate-180' : ''}`} />
              </Button>
              {hotspotsExpanded ? (
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={loadingHotspots}
                  loadingText="刷新中..."
                  disabled={!isScreeningEnabled || loadingHotspots}
                  onClick={() => void loadHotspots(true)}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  <span className="sr-only">刷新热点题材</span>
                </Button>
              ) : null}
            </>
          }
        />
        {hotspotsUpdatedAt ? (
          <p className="mb-3 text-right text-xs text-secondary-text">更新于 {formatHotspotUpdatedAt(hotspotsUpdatedAt)}</p>
        ) : null}

        {hotspotsExpanded && hotspotError ? (
          <InlineAlert variant="warning" message={hotspotError} className="mb-3" />
        ) : null}

        {!hotspotsExpanded ? null : hotspots.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-4 py-6 text-sm text-secondary-text">
            暂无热点数据，点击“刷新热点题材”获取。
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {hotspots.map((item, index) => (
              <HotspotCard
                key={`${item.topic}-${item.rank ?? ''}`}
                item={item}
                index={index}
                selected={selectedHotspotTopic === item.topic}
                onSelect={handleHotspotSelect}
              />
            ))}
          </div>
        )}

        {hotspotsExpanded && selectedHotspotTopic ? (
          <div className="mt-4 rounded-xl border border-subtle bg-card/70 p-4 backdrop-blur-md">
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  {hotspotDetail?.name || selectedHotspotTopic}
                </h3>
                <p className="mt-1 text-xs leading-5 text-secondary-text">
                  {hotspotDetail
                    ? getHotspotSummaryText(hotspotDetail, selectedHotspot)
                    : loadingHotspotDetail
                      ? '正在读取发酵路线与概念股...'
                      : '点击题材查看发酵路线与概念股。'}
                </p>
                {hotspotDetail?.canonicalTopic && hotspotDetail.canonicalTopic !== selectedHotspotTopic ? (
                  <p className="mt-1 text-[11px] text-secondary-text">标准题材：{hotspotDetail.canonicalTopic}</p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={searchingHotspotNews}
                  loadingText="搜索中..."
                  disabled={loadingHotspotDetail || searchingHotspotNews}
                  onClick={() => void loadHotspotDetail(selectedHotspotTopic, { includeSearch: true })}
                >
                  <Search className="h-3.5 w-3.5" />
                  搜索最新消息
                </Button>
                {loadingHotspotDetail ? (
                  <Badge size="sm" variant="info" className="w-fit">正在补充详情</Badge>
                ) : null}
                {hotspotDetail?.qualityStatus ? (
                  <Badge size="sm" variant="warning" className="w-fit">质量 {getHotspotQualityLabel(hotspotDetail.qualityStatus)}</Badge>
                ) : null}
                {hotspotDetail?.fallbackUsed || hotspotDetail?.stale ? (
                  <Badge size="sm" variant="warning" className="w-fit">{getHotspotFallbackLabel(hotspotDetail)}</Badge>
                ) : null}
                {hotspotDetail?.stockCount != null ? (
                  <Badge size="sm" className="w-fit border-orange-500/20 bg-orange-500/10 text-orange-500">概念股 {hotspotDetail.stockCount}</Badge>
                ) : null}
              </div>
            </div>

            {hotspotDetailError ? (
              <InlineAlert variant="warning" message={hotspotDetailError} className="mb-3" />
            ) : null}

            {hotspotDetail && hasHotspotDetailDegradation(hotspotDetail) ? (
              <details className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                <summary className="cursor-pointer font-semibold">详情数据已降级，展开查看原因</summary>
                <div className="mt-2 space-y-1 leading-5">
                  {(hotspotDetail.missingFields || []).length > 0 ? (
                    <p>暂缺：{getHotspotMissingFieldLabels(hotspotDetail.missingFields).join('、')}</p>
                  ) : null}
                  {getHotspotDiagnosticMessages(hotspotDetail.sourceErrors).map((message, index) => (
                    <p key={`${message}-${index}`}>{message}</p>
                  ))}
                </div>
              </details>
            ) : null}

            {hotspotDetail ? (
              <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
                <div>
                  <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-secondary-text">
                    <Clock3 className="h-3.5 w-3.5 text-orange-500" />
                    发酵时间线
                  </p>
                  <div className="relative space-y-0 pl-4 before:absolute before:bottom-3 before:left-[5px] before:top-2 before:w-px before:bg-border">
                    {getHotspotRouteItems(hotspotDetail).map((item, index) => (
                      <div key={`${item.title}-${index}`} className="relative pb-4 last:pb-0">
                        <span className="absolute -left-4 top-1 h-2.5 w-2.5 rounded-full border border-orange-400 bg-card" />
                        <div className="rounded-lg border border-border/70 bg-card/80 p-3">
                          <p className="text-[11px] font-semibold text-orange-500">{getRouteTimeLabel(item)}</p>
                          <p className="mt-1 text-xs font-semibold text-foreground">{formatHotspotRouteTitle(item.title)}</p>
                          <p className="mt-1 text-xs leading-5 text-secondary-text">{formatHotspotRouteDescription(item.description)}</p>
                          {item.url ? (
                            <a
                              className="mt-2 inline-flex text-[11px] font-semibold text-cyan hover:text-foreground"
                              href={item.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              查看消息
                            </a>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-xs font-semibold text-secondary-text">概念股</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(hotspotDetail.stocks || []).slice(0, 10).map((stock) => (
                      <div key={`${stock.code || stock.name}`} className="rounded-lg border border-border/70 bg-card/80 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-xs font-semibold text-foreground">{stock.name || stock.code || '-'}</p>
                            <p className="mt-1 text-[11px] text-secondary-text">{stock.code || '-'}</p>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <Badge size="sm" variant="info" className="text-[11px]">{getHotspotRoleLabel(stock.role)}</Badge>
                            {stock.code ? (
                              <button
                                type="button"
                                aria-label={`分析 ${stock.name || stock.code}`}
                                className="inline-flex h-7 items-center gap-1 rounded-full border border-cyan/30 bg-cyan/10 px-2 text-[11px] font-semibold text-cyan transition-colors hover:border-cyan hover:bg-cyan/15 hover:text-foreground"
                                onClick={() => handleAnalyzeHotspotStock(stock)}
                              >
                                <Play className="h-3 w-3" />
                                分析
                              </button>
                            ) : null}
                          </div>
                        </div>
                        <p className="mt-2 text-[11px] text-secondary-text">
                          涨跌幅 {formatStockChangeText(stock.changePct)} · 热度 {formatNumber(stock.hotStockScore, 0)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="glass-card !border-transparent p-4 md:p-5">
        <DashboardPanelHeader
          className="mb-4"
          leading={<SlidersHorizontal className="h-4 w-4 text-cyan" />}
          title="运行选股"
          titleClassName="text-sm font-medium"
          actions={<Badge size="sm" variant="info" className="w-fit">{selectedStrategyTag}</Badge>}
        />

        <div className="grid gap-4 lg:grid-cols-[1.2fr_180px_auto] lg:items-end">
          <div className="space-y-2 text-xs font-medium text-secondary-text">
            <label htmlFor="screening-strategy">策略</label>
            <Select
              id="screening-strategy"
              value={strategy}
              disabled={loading || loadingStrategies}
              placeholder=""
              options={strategies.map((item) => ({
                value: item.id,
                label: item.name || item.title || item.id,
              }))}
              onChange={(value) => handleStrategyChange(value)}
            />
          </div>

          <label className="space-y-2 text-xs font-medium text-secondary-text">
            返回数量
            <input
              className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
              type="number"
              min={1}
              max={100}
              value={maxResults}
              disabled={loading}
              onChange={(event) => handleMaxResultsChange(Number(event.target.value))}
            />
          </label>

          <Button
            className="h-11 min-w-40"
            isLoading={loading}
            loadingText="筛选中..."
            disabled={!isScreeningEnabled || loading || !strategy.trim()}
            onClick={() => void handleSubmit()}
          >
            <Play className="h-4 w-4" />
            运行选股
          </Button>
        </div>

        <div className="mt-3 rounded-xl border border-border/75 bg-surface/55 px-3 py-2 text-xs leading-5 text-secondary-text">
          {strategyLoadError
            ? strategyLoadError
            : selectedStrategy?.description || '策略会先执行硬过滤和因子评分，再进行风险与组合约束。'}
        </div>
      </section>

      {screenMeta && alertMessages.length > 0 ? (
        <InlineAlert
          variant={llmFailed ? 'warning' : 'info'}
          title={llmFailed ? '当前使用因子排序' : '选股提示'}
          message={<ScreenAlertMessage messages={alertMessages} />}
        />
      ) : null}

      {screenMeta ? (
        <section className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader
            className="mb-4"
            title="选股结果"
            titleClassName="text-sm font-medium"
            actions={
              <div className="flex items-center gap-2 rounded-full border border-border/60 bg-surface px-3 py-1.5 text-xs text-secondary-text">
                <Search className="h-4 w-4 text-cyan" />
                {candidates.length} 条候选
              </div>
            }
          />
          {candidates.length === 0 ? (
            <EmptyState className="px-4 py-6" title="暂无符合条件的候选" />
          ) : (
          <div className="space-y-2.5">
            {candidates.map((item) => (
              <CandidateListItem
                key={`${item.rank}-${item.code}`}
                item={item}
                rank={item.rank}
                factorRanking={factorRanking}
                expanded={expandedCode === item.code}
                onToggle={handleToggleCandidate}
                onAnalyze={handleAnalyzeCandidate}
              />
            ))}
          </div>
        )}
        </section>
      ) : null}

      {loading && !screenMeta ? (
        <section className="glass-card !border-transparent p-4 md:p-5">
          <DashboardPanelHeader
            className="mb-4"
            title="选股结果"
            titleClassName="text-sm font-medium"
          />
          <EmptyState
            className="px-4 py-6"
            icon={<Loader2 className="h-5 w-5 animate-spin" />}
            title="选股任务运行中"
            description="正在执行策略筛选与评分，完成后结果会显示在这里。可在顶部任务图标查看进度。"
          />
        </section>
      ) : null}
    </AppPage>
  );
};

export default StockScreeningPage;
