/**
 * Shared formatting/derivation helpers for screening candidates.
 *
 * These are used by both the screening page (状态编排) and the candidate list
 * item component. Keeping them here (rather than inlining in the page or
 * duplicating in the list item) honors the "组件风格先统一 / 复用优先" rule:
 * one definition, one source of truth.
 */
import type { ScreeningCandidate } from '../../api/screening';

export const formatScore = (score: ScreeningCandidate['score']) => {
  if (score == null || Number.isNaN(Number(score))) {
    return '-';
  }
  return Number(score).toFixed(2);
};

export const formatNumber = (value: unknown, digits = 2) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toFixed(digits);
};

export const formatAmount = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  const amount = Number(value);
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)} 亿`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)} 万`;
  }
  return amount.toFixed(2);
};

export const formatPercent = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(0)}%`;
};

export const FACTOR_LABELS: Record<string, string> = {
  value: '估值',
  liquidity: '流动性',
  momentum: '动量',
  reversal: '反转',
  activity: '活跃度',
  stability: '稳定性',
  size: '规模',
  theme_heat: '题材热度',
  topic_alignment: '题材匹配',
};

const POST_TAG_LABELS: Record<string, string> = {
  value_quality: '价值质量',
  controlled_reversal: '受控反转',
  momentum: '趋势动量',
  liquidity: '流动性',
};

export const getRiskClassName = (riskLevel: string | undefined) => {
  if (riskLevel === 'high') {
    return 'bg-danger/10 text-danger';
  }
  if (riskLevel === 'medium') {
    return 'bg-warning/10 text-warning';
  }
  if (riskLevel === 'low') {
    return 'bg-success/10 text-success';
  }
  return 'bg-surface text-secondary-text';
};

export const getRiskLabel = (riskLevel: string | undefined) => {
  if (riskLevel === 'high') return '高';
  if (riskLevel === 'medium') return '中';
  if (riskLevel === 'low') return '低';
  return '待评估';
};

export const getLocalFactorReason = (item: ScreeningCandidate) => {
  const factors = Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([key, value]) => `${FACTOR_LABELS[key] || key} ${Number(value).toFixed(0)}`);
  const tags = (item.postAnalysisTags || [])
    .slice(0, 2)
    .map((tag) => POST_TAG_LABELS[tag] || tag);
  if (factors.length > 0) {
    return `主要优势：${factors.join('、')}${tags.length > 0 ? `；标签：${tags.join('、')}` : ''}`;
  }
  return '';
};

export const getCandidateReason = (item: ScreeningCandidate) => {
  if (item.llmThesis || item.llmScore != null) {
    return item.reason || item.llmThesis || 'LLM 已完成相对排序。';
  }
  const localReason = getLocalFactorReason(item);
  if (localReason) {
    return localReason;
  }
  if (item.reason) {
    return item.reason;
  }
  const summaries = item.postAnalysisSummaries || {};
  const summary = Object.values(summaries).find((value) => typeof value === 'string' && value.trim());
  if (typeof summary === 'string') {
    return summary;
  }
  return '暂无摘要，请查看因子和风险信息。';
};

export const getSignal = (item: ScreeningCandidate) => {
  const rawSignal = item.raw.action ?? item.raw.signal ?? item.raw.recommendation;
  return typeof rawSignal === 'string' && rawSignal.trim() ? rawSignal : '观察';
};

export const getFactorEntries = (item: ScreeningCandidate) =>
  Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 6);

export const hasLlmInsight = (item: ScreeningCandidate) =>
  Boolean(
    item.llmThesis ||
      item.llmSector ||
      item.llmTheme ||
      item.llmConfidence != null ||
      item.llmWatchItems?.length ||
      item.llmCatalysts?.length,
  );

export const formatEnrichmentSummary = (value: string) =>
  value
    .replace(/DSA行情\s*[:：]\s*/gi, '行情：')
    .replace(/DSA新闻\s*[:：]\s*/gi, '新闻：')
    .replace(/DSA事件\s*[:：]\s*/gi, '事件：');
