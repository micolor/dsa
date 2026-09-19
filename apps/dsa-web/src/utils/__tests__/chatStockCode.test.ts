import { describe, expect, it } from 'vitest';
import { extractStockCodeFromMessage, extractStockCodesFromMessage } from '../chatStockCode';

describe('extractStockCodesFromMessage', () => {
  it('keeps zero-padded bare five-digit HK codes', () => {
    expect(extractStockCodesFromMessage('分析 00700 的走势')).toEqual(['HK00700']);
    expect(extractStockCodesFromMessage('看看 01810 怎么样')).toEqual(['HK01810']);
    expect(extractStockCodeFromMessage('看看 00001')).toBe('HK00001');
  });

  it('keeps five-digit HK codes written with an explicit marker', () => {
    expect(extractStockCodesFromMessage('分析 hk81200')).toEqual(['HK81200']);
    expect(extractStockCodesFromMessage('分析 81200.HK')).toEqual(['HK81200']);
  });

  it('ignores markerless five-digit numbers that are prices or quantities', () => {
    // Regression: 这些句子曾把裸 5 位数字当成港股代码，切换会话标的到不存在的
    // HK12000 / HK25000 / HK21000，同时清空当前标的的上下文。
    expect(extractStockCodesFromMessage('看看 600519 的成交量 12000 手')).toEqual(['600519']);
    expect(extractStockCodesFromMessage('分析 600519，目标价 25000')).toEqual(['600519']);
    expect(extractStockCodesFromMessage('研究 600519 市值 21000 亿')).toEqual(['600519']);
    expect(extractStockCodesFromMessage('看看 600519 的现价 18000')).toEqual(['600519']);
  });

  it('still extracts the surrounding explicit codes', () => {
    expect(extractStockCodesFromMessage('比较 600519 和 00700')).toEqual(['600519', 'HK00700']);
    expect(extractStockCodesFromMessage('比较 01810 和 AAPL')).toEqual(['HK01810', 'AAPL']);
  });
});
