import type { AlertTriggerItem } from '../../types/alerts';

// Event-driven trigger data sources (must mirror src/services/event_alerts.EVENT_DATA_SOURCES).
const EVENT_SOURCES = ['dragon_tiger', 'capital_flow', 'stock_events'] as const;

export function isEventDataSource(source?: string | null): boolean {
  return EVENT_SOURCES.includes(source as (typeof EVENT_SOURCES)[number]);
}

export function filterEventTriggers(items: AlertTriggerItem[]): AlertTriggerItem[] {
  return items.filter((item) => isEventDataSource(item.dataSource));
}

export function parseEventDiagnostics(raw?: string | null): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function formatMoney(value: unknown): string {
  const number = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(number)) return '--';
  const abs = Math.abs(number);
  if (abs >= 100_000_000) return `${(number / 100_000_000).toFixed(2)}亿`;
  if (abs >= 10_000) return `${(number / 10_000).toFixed(0)}万`;
  return number.toFixed(0);
}
