import { describe, expect, test } from 'vitest';
import {
  isObviouslyInvalidStockQuery,
  looksLikeStockCode,
  validateStockCode,
} from '../validation';

describe('stock code validation', () => {
  test.each([
    ['7203.T', '7203.T'],
    ['6758.t', '6758.T'],
    ['005930.KS', '005930.KS'],
    ['035720.kq', '035720.KQ'],
  ])('accepts JP/KR Yahoo suffix code %s', (input, normalized) => {
    expect(looksLikeStockCode(input)).toBe(true);
    expect(validateStockCode(input)).toEqual({
      valid: true,
      normalized,
    });
    expect(isObviouslyInvalidStockQuery(input)).toBe(false);
  });

  test.each(['7203', '005930.K', '035720.KRX'])(
    'does not treat ambiguous JP/KR-like query %s as a valid suffix code',
    (input) => {
      const result = validateStockCode(input);
      expect(result.valid).toBe(false);
    }
  );
});

describe('off-exchange fund query (fund: prefix)', () => {
  test.each([
    ['fund:006229', 'fund:006229'],
    ['FUND:006229', 'fund:006229'],
    ['fund:003095', 'fund:003095'],
  ])('accepts and normalizes fund query %s', (input, normalized) => {
    expect(looksLikeStockCode(input)).toBe(true);
    expect(validateStockCode(input)).toEqual({
      valid: true,
      normalized,
    });
    expect(isObviouslyInvalidStockQuery(input)).toBe(false);
  });

  test.each(['fund:', 'fund:12345', 'fund:006229.XX', 'fund:abc'])(
    'rejects malformed fund-like query %s',
    (input) => {
      expect(validateStockCode(input).valid).toBe(false);
    }
  );

  test('keeps lowercase fund prefix (backend is_fund_code requires lowercase startswith)', () => {
    // 后端路由依据 is_fund_code("fund:…") 的小写前缀判定，前端不得将其大写化。
    expect(validateStockCode('FUND:006229').normalized).toBe('fund:006229');
  });
});
