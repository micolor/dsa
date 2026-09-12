import { describe, expect, it } from 'vitest';
import { formatPriceTick } from '../format';

describe('formatPriceTick', () => {
  it('收敛价格轴 domain 的浮点噪声', () => {
    // domain 是 [minLow - pad, maxHigh + pad]，pad 为浮点数，未格式化时会渲染成
    // 21.840999999999998 这类二进制浮点噪声。
    expect(formatPriceTick(21.840999999999998)).toBe('21.84');
    expect(formatPriceTick(98.00000000000001)).toBe('98.00');
  });

  it('保留两位小数', () => {
    expect(formatPriceTick(100)).toBe('100.00');
    expect(formatPriceTick(0.5)).toBe('0.50');
    expect(formatPriceTick(-3.456)).toBe('-3.46');
  });

  it('非有限值不做格式化', () => {
    expect(formatPriceTick(Number.NaN)).toBe('');
    expect(formatPriceTick(Number.POSITIVE_INFINITY)).toBe('');
  });
});
