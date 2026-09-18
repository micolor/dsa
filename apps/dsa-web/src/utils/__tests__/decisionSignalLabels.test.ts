import { describe, expect, it } from 'vitest';
import {
  getDecisionSignalHorizonLabel,
  getDecisionSignalMarketLabel,
  getDecisionSignalMarketPhaseLabel,
  getDecisionSignalPlanQualityLabel,
  getDecisionSignalSourceTypeLabel,
  getSkillLabel,
} from '../decisionSignalLabels';
import type { UiTextKey } from '../../i18n/uiText';

const labels: Partial<Record<UiTextKey, string>> = {
  'decisionSignals.horizon.10d': '10 days',
  'decisionSignals.market.jp': 'Japan',
  'decisionSignals.marketPhase.closing_auction': 'Closing auction',
  'decisionSignals.planQuality.partial': 'Partial',
  'decisionSignals.sourceType.market_review': 'Market review',
};

const t = (key: UiTextKey): string => labels[key] ?? '';

describe('decisionSignalLabels helpers', () => {
  it('maps known wire values through explicit i18n keys', () => {
    expect(getDecisionSignalMarketLabel('jp', t)).toBe('Japan');
    expect(getDecisionSignalMarketPhaseLabel('closing_auction', t)).toBe('Closing auction');
    expect(getDecisionSignalHorizonLabel('10d', t)).toBe('10 days');
    expect(getDecisionSignalPlanQualityLabel('partial', t)).toBe('Partial');
    expect(getDecisionSignalSourceTypeLabel('market_review', t)).toBe('Market review');
  });

  it('does not expose unknown runtime values as raw enum text', () => {
    expect(getDecisionSignalHorizonLabel('30d' as never, t)).toBe('-');
    expect(getDecisionSignalSourceTypeLabel('batch_job' as never, t)).toBe('-');
  });
});

describe('getSkillLabel', () => {
  const skillLabels: Partial<Record<UiTextKey, string>> = {
    'decisionSignals.skill.bull_trend': '默认多头趋势',
    'decisionSignals.skill.box_oscillation': '箱体震荡',
  };
  const skillT = (key: UiTextKey): string => skillLabels[key] ?? '';

  it('maps known skill ids through explicit i18n keys', () => {
    expect(getSkillLabel('bull_trend', skillT)).toBe('默认多头趋势');
    expect(getSkillLabel('box_oscillation', skillT)).toBe('箱体震荡');
  });

  it('falls back to the raw skill id for unknown skills instead of a dash', () => {
    // skill 是开放集合（AGENT_SKILLS 可配置自定义策略），
    // 未知 id 必须回退原始值；返回 '-' 会让整列变成横杠，比显示英文 id 更糟。
    expect(getSkillLabel('my_custom_skill', skillT)).toBe('my_custom_skill');
  });

  it('renders a dash only for empty input', () => {
    expect(getSkillLabel(null, skillT)).toBe('-');
    expect(getSkillLabel(undefined, skillT)).toBe('-');
    expect(getSkillLabel('', skillT)).toBe('-');
    expect(getSkillLabel('   ', skillT)).toBe('-');
  });
});
