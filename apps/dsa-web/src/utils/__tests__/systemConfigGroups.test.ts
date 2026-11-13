import { describe, expect, it } from 'vitest';
import { groupConfigItems } from '../systemConfigGroups';
import type { SystemConfigItem } from '../../types/systemConfig';

function item(key: string): SystemConfigItem {
  return { key, value: '', rawValueExists: false, isMasked: false };
}

function flatten(groups: ReturnType<typeof groupConfigItems>): string[] {
  return groups.flatMap((group) => group.items.map((entry) => entry.key));
}

describe('groupConfigItems', () => {
  it('keeps every agent item exactly once and assigns it to the expected group', () => {
    const keys = [
      'AGENT_BACKEND',
      'AGENT_ARCH',
      'AGENT_SKILLS',
      'AGENT_SKILL_CONCURRENCY',
      'AGENT_MEMORY_ENABLED',
      'AGENT_CONTEXT_COMPRESSION_ENABLED',
      'AGENT_PROACTIVE_BRIEF_ENABLED',
      'AGENT_EVENT_ANALYSIS_ENABLED',
      'AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY',
      'AGENT_WEEKLY_REVIEW_ENABLED',
      'AGENT_OPPORTUNITY_SCAN_ENABLED',
      'AGENT_OPPORTUNITY_SCAN_REFRESH',
      'AGENT_RISK_PROFILE_HORIZON',
      'AGENT_PROACTIVE_QUIET_HOURS',
      'AGENT_PROACTIVE_NOTIFY_RETRIES',
      'AGENT_PROACTIVE_MARKET_GUARD',
      'AGENT_DATA_REFRESH_ENABLED',
      'AGENT_EVENT_MONITOR_ENABLED',
      'AGENT_EVENT_ALERT_RULES_JSON',
      'SCHEDULED_TASKS_ENABLED',
    ];

    const groups = groupConfigItems('agent', keys.map(item));
    const flattened = flatten(groups);

    expect(flattened).toHaveLength(keys.length);
    expect(new Set(flattened)).toEqual(new Set(keys));

    const groupOf = (key: string) =>
      groups.find((group) => group.items.some((entry) => entry.key === key))?.id;

    expect(groupOf('AGENT_BACKEND')).toBe('agent-runtime');
    expect(groupOf('SCHEDULED_TASKS_ENABLED')).toBe('agent-runtime');
    expect(groupOf('AGENT_SKILLS')).toBe('agent-skills');
    expect(groupOf('AGENT_MEMORY_ENABLED')).toBe('agent-context');
    expect(groupOf('AGENT_PROACTIVE_BRIEF_ENABLED')).toBe('proactive-brief-event');
    expect(groupOf('AGENT_EVENT_ANALYSIS_ENABLED')).toBe('proactive-brief-event');
    expect(groupOf('AGENT_WEEKLY_REVIEW_ENABLED')).toBe('proactive-review-opportunity');
    expect(groupOf('AGENT_RISK_PROFILE_HORIZON')).toBe('risk-profile');
    expect(groupOf('AGENT_PROACTIVE_NOTIFY_RETRIES')).toBe('proactive-push');
    expect(groupOf('AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY')).toBe('proactive-push');
    expect(groupOf('AGENT_PROACTIVE_QUIET_HOURS')).toBe('proactive-push');
    expect(groupOf('AGENT_DATA_REFRESH_ENABLED')).toBe('proactive-refresh-guard');
    expect(groupOf('AGENT_OPPORTUNITY_SCAN_REFRESH')).toBe('proactive-refresh-guard');
    expect(groupOf('AGENT_EVENT_MONITOR_ENABLED')).toBe('event-monitor');
  });

  it('drops empty groups and keeps declaration order', () => {
    const groups = groupConfigItems('data_source', [item('TAVILY_API_KEYS'), item('TUSHARE_TOKEN')]);

    expect(groups.map((group) => group.id)).toEqual(['market-source', 'search-source']);
    expect(flatten(groups)).toEqual(['TUSHARE_TOKEN', 'TAVILY_API_KEYS']);
  });

  it('puts unmatched keys into the fallback group', () => {
    const groups = groupConfigItems('notification', [item('FEISHU_WEBHOOK_URL')], {
      zh: '渠道配置',
      en: 'Channel configuration',
    });

    expect(groups).toHaveLength(1);
    expect(groups[0].titleZh).toBe('渠道配置');
    expect(flatten(groups)).toEqual(['FEISHU_WEBHOOK_URL']);
  });

  it('returns one untitled group for categories without definitions', () => {
    const groups = groupConfigItems('backtest', [item('BACKTEST_ENABLED')]);

    expect(groups).toHaveLength(1);
    expect(groups[0].titleZh).toBeUndefined();
    expect(flatten(groups)).toEqual(['BACKTEST_ENABLED']);
  });
});
