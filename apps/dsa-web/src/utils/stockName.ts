import { stockCodeKey } from './stockCode';

/**
 * Stock name truncation configuration
 * English characters: 15 chars max
 * Chinese characters: 8 chars max
 * Mixed (Chinese + English): 10 chars max
 */
export const STOCK_NAME_MAX_LENGTH = {
  ENGLISH: 15,
  CHINESE: 8,
  MIXED: 10,
} as const;

/**
 * Get max allowed length for a stock name based on character type
 * - Pure English: 15 chars
 * - Pure Chinese: 8 chars
 * - Mixed: 10 chars
 */
function getMaxLength(name: string): number {
  const isChinese = /[\u4e00-\u9fa5]/.test(name);
  const isMixed = isChinese && /[a-zA-Z]/.test(name);
  if (isMixed) return STOCK_NAME_MAX_LENGTH.MIXED;
  if (isChinese) return STOCK_NAME_MAX_LENGTH.CHINESE;
  return STOCK_NAME_MAX_LENGTH.ENGLISH;
}

/**
 * Truncate stock name based on character type
 * - Pure English: max 15 characters
 * - Pure Chinese: max 8 characters
 * - Mixed: max 10 characters
 */
export function truncateStockName(name: string): string {
  if (!name) return name;
  const maxLen = getMaxLength(name);
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen) + '.';
}

/**
 * Check if stock name will be truncated
 */
export function isStockNameTruncated(name: string): boolean {
  if (!name) return false;
  return name.length > getMaxLength(name);
}

/**
 * 名称与代码是否指向同一标的。
 *
 * 后端对部分场外基金只回代码做名称（`stock_name === stock_code`，例如 001052），
 * 大盘复盘这类伪标的则名称与代码同为 MARKET。此时界面上再展示一次代码就是纯重复：
 * 卡片里同一串出现两遍，aria-label 也会被读屏念两遍（「001052 001052 历史记录」）。
 *
 * 判定统一走 stockCodeKey，因而兼容带交易所前后缀的写法（sh600519 / 600519.SH）。
 */
export function isStockCodeRedundantWithName(
  name?: string | null,
  code?: string | null,
): boolean {
  const trimmedName = (name ?? '').trim();
  const trimmedCode = (code ?? '').trim();
  if (!trimmedName || !trimmedCode) return false;
  return stockCodeKey(trimmedName) === stockCodeKey(trimmedCode);
}
