import { describe, expect, it } from 'vitest';
import { toCamelCase, toSnakeCase } from '../utils';

describe('toSnakeCase', () => {
  it('converts nested keys recursively', () => {
    expect(toSnakeCase({ ruleId: 1, nested: { baseCurrency: 'CNY' } })).toEqual({
      rule_id: 1,
      nested: { base_currency: 'CNY' },
    });
  });

  it('leaves arrays and primitives intact', () => {
    expect(toSnakeCase({ items: [{ listName: 'x' }] })).toEqual({
      items: [{ list_name: 'x' }],
    });
  });

  it('round-trips camelCase keys produced by toCamelCase', () => {
    const snake = { account_id: 1, base_currency: 'CNY', nested: { trade_date: '2026-09-22' } };
    expect(toSnakeCase(toCamelCase(snake))).toEqual(snake);
  });
});
