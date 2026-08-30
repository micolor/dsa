import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { EventFactList } from '../EventFactList';
import { filterEventTriggers, isEventDataSource } from '../eventFacts';
import type { AlertTriggerItem } from '../../../types/alerts';

function makeTrigger(overrides: Partial<AlertTriggerItem>): AlertTriggerItem {
  return {
    id: 1,
    target: '600519',
    status: 'triggered',
    dataSource: 'dragon_tiger',
    diagnostics: '{"event":"dragon_tiger","is_on_list":true,"recent_count":3}',
    reason: '600519 近 3 次登上龙虎榜',
    observedValue: 3,
    threshold: 1,
    dataTimestamp: '2026-08-20T00:00:00',
    triggeredAt: '2026-08-20T10:00:00',
    ...overrides,
  };
}

function renderEn(items: AlertTriggerItem[]) {
  window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
  render(
    <UiLanguageProvider>
      <EventFactList triggers={items} />
    </UiLanguageProvider>,
  );
}

describe('filterEventTriggers', () => {
  it('keeps only event-source triggers', () => {
    const items = [
      makeTrigger({ id: 1, dataSource: 'dragon_tiger' }),
      makeTrigger({ id: 2, dataSource: 'capital_flow' }),
      makeTrigger({ id: 3, dataSource: 'stock_events' }),
      makeTrigger({ id: 4, dataSource: 'realtime_quote' }),
      makeTrigger({ id: 5, dataSource: null }),
    ];
    expect(filterEventTriggers(items).map((i) => i.id)).toEqual([1, 2, 3]);
  });

  it('recognizes each event source', () => {
    expect(isEventDataSource('dragon_tiger')).toBe(true);
    expect(isEventDataSource('capital_flow')).toBe(true);
    expect(isEventDataSource('stock_events')).toBe(true);
    expect(isEventDataSource('realtime_quote')).toBe(false);
    expect(isEventDataSource(null)).toBe(false);
  });
});

describe('EventFactList', () => {
  it('renders event source label, target, and observed facts', () => {
    renderEn([makeTrigger({})]);
    expect(screen.getByText('Dragon-tiger')).toBeInTheDocument();
    expect(screen.getByText('600519')).toBeInTheDocument();
    expect(screen.getByText(/dragon_tiger/)).toBeInTheDocument();
    expect(screen.getByText(/近 3 次登上龙虎榜/)).toBeInTheDocument();
  });

  it('shows empty state when no event triggers', () => {
    renderEn([]);
    expect(screen.getByText('No event records')).toBeInTheDocument();
  });

  it('handles malformed diagnostics without crashing', () => {
    renderEn([makeTrigger({ diagnostics: '{not json' })]);
    expect(screen.getByText('600519')).toBeInTheDocument();
  });

  it('formats capital flow as 万元/亿元', () => {
    renderEn([makeTrigger({ id: 2, dataSource: 'capital_flow', diagnostics: '{"event":"capital_flow","main_net_inflow":150000000}' })]);
    expect(screen.getByText(/1.50亿/)).toBeInTheDocument();
  });
});
