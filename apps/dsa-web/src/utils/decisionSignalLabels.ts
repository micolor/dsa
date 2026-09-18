import type { MarketPhaseValue } from '../types/analysis';
import type {
  DecisionSignalHorizon,
  DecisionSignalMarket,
  DecisionSignalPlanQuality,
  DecisionSignalSourceType,
} from '../types/decisionSignals';
import type { UiTextKey } from '../i18n/uiText';

type Translator = (key: UiTextKey) => string;

const MARKET_LABEL_KEYS: Record<DecisionSignalMarket, UiTextKey> = {
  cn: 'decisionSignals.market.cn',
  hk: 'decisionSignals.market.hk',
  us: 'decisionSignals.market.us',
  jp: 'decisionSignals.market.jp',
  kr: 'decisionSignals.market.kr',
  tw: 'decisionSignals.market.tw',
};

const MARKET_PHASE_LABEL_KEYS: Record<MarketPhaseValue, UiTextKey> = {
  premarket: 'decisionSignals.marketPhase.premarket',
  intraday: 'decisionSignals.marketPhase.intraday',
  lunch_break: 'decisionSignals.marketPhase.lunch_break',
  closing_auction: 'decisionSignals.marketPhase.closing_auction',
  postmarket: 'decisionSignals.marketPhase.postmarket',
  non_trading: 'decisionSignals.marketPhase.non_trading',
  unknown: 'decisionSignals.marketPhase.unknown',
};

const HORIZON_LABEL_KEYS: Record<DecisionSignalHorizon, UiTextKey> = {
  intraday: 'decisionSignals.horizon.intraday',
  '1d': 'decisionSignals.horizon.1d',
  '3d': 'decisionSignals.horizon.3d',
  '5d': 'decisionSignals.horizon.5d',
  '10d': 'decisionSignals.horizon.10d',
  swing: 'decisionSignals.horizon.swing',
  long: 'decisionSignals.horizon.long',
};

const PLAN_QUALITY_LABEL_KEYS: Record<DecisionSignalPlanQuality, UiTextKey> = {
  complete: 'decisionSignals.planQuality.complete',
  partial: 'decisionSignals.planQuality.partial',
  minimal: 'decisionSignals.planQuality.minimal',
  unknown: 'decisionSignals.planQuality.unknown',
};

const SOURCE_TYPE_LABEL_KEYS: Record<DecisionSignalSourceType, UiTextKey> = {
  analysis: 'decisionSignals.sourceType.analysis',
  agent: 'decisionSignals.sourceType.agent',
  alert: 'decisionSignals.sourceType.alert',
  market_review: 'decisionSignals.sourceType.market_review',
  manual: 'decisionSignals.sourceType.manual',
};

// Skill 意见样本充分性由后端以 `sufficient` / `observational` 下发，前端此前直接渲染原值。
const SKILL_SAMPLE_STATUS_LABEL_KEYS: Record<string, UiTextKey> = {
  sufficient: 'decisionSignals.skillStatsSampleStatus.sufficient',
  observational: 'decisionSignals.skillStatsSampleStatus.observational',
};

function translatedKnownValue<Value extends string>(
  value: Value | null | undefined,
  keys: Record<Value, UiTextKey>,
  t: Translator,
): string {
  if (!value) return '-';
  const key = keys[value];
  return key ? t(key) || '-' : '-';
}

export function getDecisionSignalMarketLabel(
  market: DecisionSignalMarket | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(market, MARKET_LABEL_KEYS, t);
}

export function getDecisionSignalMarketPhaseLabel(
  marketPhase: MarketPhaseValue | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(marketPhase, MARKET_PHASE_LABEL_KEYS, t);
}

export function getDecisionSignalHorizonLabel(
  horizon: DecisionSignalHorizon | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(horizon, HORIZON_LABEL_KEYS, t);
}

export function getDecisionSignalPlanQualityLabel(
  planQuality: DecisionSignalPlanQuality | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(planQuality, PLAN_QUALITY_LABEL_KEYS, t);
}

export function getDecisionSignalSourceTypeLabel(
  sourceType: DecisionSignalSourceType | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(sourceType, SOURCE_TYPE_LABEL_KEYS, t);
}

export function getSkillOpinionSampleStatusLabel(
  sampleStatus: string | null | undefined,
  t: Translator,
): string {
  return translatedKnownValue(sampleStatus, SKILL_SAMPLE_STATUS_LABEL_KEYS, t);
}

// 与后端 src/report_language.py 的 _STRATEGY_SKILL_TRANSLATIONS 对应；
// 由 tests/test_skill_label_parity.py 保证两边不漂移。
const SKILL_LABEL_KEYS: Record<string, UiTextKey> = {
  bull_trend: 'decisionSignals.skill.bull_trend',
  hot_theme: 'decisionSignals.skill.hot_theme',
  volume_breakout: 'decisionSignals.skill.volume_breakout',
  ma_golden_cross: 'decisionSignals.skill.ma_golden_cross',
  growth_quality: 'decisionSignals.skill.growth_quality',
  bottom_volume: 'decisionSignals.skill.bottom_volume',
  box_oscillation: 'decisionSignals.skill.box_oscillation',
  chan_theory: 'decisionSignals.skill.chan_theory',
  dragon_head: 'decisionSignals.skill.dragon_head',
  emotion_cycle: 'decisionSignals.skill.emotion_cycle',
  event_driven: 'decisionSignals.skill.event_driven',
  expectation_repricing: 'decisionSignals.skill.expectation_repricing',
  one_yang_three_yin: 'decisionSignals.skill.one_yang_three_yin',
  shrink_pullback: 'decisionSignals.skill.shrink_pullback',
  wave_theory: 'decisionSignals.skill.wave_theory',
};

/**
 * Skill 是开放集合（AGENT_SKILLS 支持自定义策略），因此未知 id 必须回退为原始
 * id —— 不能像 translatedKnownValue 那样返回 '-'，否则整列会变成横杠，
 * 比显示英文 id 更不可读。
 */
export function getSkillLabel(
  skillId: string | null | undefined,
  t: Translator,
): string {
  const normalized = typeof skillId === 'string' ? skillId.trim() : '';
  if (!normalized) return '-';
  const key = SKILL_LABEL_KEYS[normalized];
  if (!key) return normalized;
  return t(key) || normalized;
}
