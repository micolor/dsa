interface ValidationResult {
  valid: boolean;
  message?: string;
  normalized: string;
}

const SUPPORTED_QUERY_CHARACTERS = /^[A-Z0-9.\u3400-\u9FFF\s]+$/;

const FUND_CODE_PATTERN = /^fund:(\d{6})$/;

/**
 * 是否为显式场外基金前缀：`fund:` + 6 位数字，例如 `fund:003095`。
 * 大小写均接受，归一化后一律为小写前缀。
 */
export const isFundQuery = (value: string): boolean =>
  FUND_CODE_PATTERN.test(value.trim().toLowerCase());

/** 归一化后的基金标记（小写前缀），例如 `fund:006229`；非法输入返回空串。 */
export const canonicalFundCode = (value: string): string => {
  const match = FUND_CODE_PATTERN.exec(value.trim().toLowerCase());
  return match ? `fund:${match[1]}` : '';
};

const STOCK_CODE_PATTERNS = [
  /^\d{6}$/, // A-share 6-digit code
  /^(SH|SZ|BJ)\d{6}$/, // A-share code with exchange prefix
  /^\d{6}\.(SH|SZ|SS|BJ)$/, // A-share code with exchange suffix
  /^\d{5}$/, // HK code without prefix
  /^HK\d{1,5}$/, // HK-prefixed code, for example HK00700
  /^\d{1,5}\.HK$/, // HK suffix format, for example 00700.HK
  /^\d{4,5}\.T$/, // Japan Yahoo suffix format, for example 7203.T
  /^\d{6}\.(KS|KQ)$/, // Korea Yahoo suffix format, for example 005930.KS or 035720.KQ
  /^[A-Z]{1,5}(?:\.(?:US|[A-Z]))?$/, // Common US ticker format
];

/**
 * Check whether the input looks like a stock code.
 */
export const looksLikeStockCode = (value: string): boolean => {
  if (isFundQuery(value)) {
    return true;
  }
  const normalized = value.trim().toUpperCase();
  return STOCK_CODE_PATTERNS.some((regex) => regex.test(normalized));
};

/**
 * Validate common A-share, HK, US, JP, and KR stock code formats.
 */
export const validateStockCode = (value: string): ValidationResult => {
  const trimmed = value.trim();

  if (!trimmed) {
    return { valid: false, message: '请输入股票代码', normalized: '' };
  }

  // 基金标记单独归一化：保留小写 `fund:` 前缀，并非从大写化后的股票代码。
  if (isFundQuery(trimmed)) {
    return { valid: true, normalized: canonicalFundCode(trimmed) };
  }

  const normalized = trimmed.toUpperCase();
  const valid = looksLikeStockCode(normalized);

  return {
    valid,
    message: valid ? undefined : '股票代码格式不正确',
    normalized,
  };
};

/**
 * Reject obviously invalid free-text queries before they reach the backend.
 */
export const isObviouslyInvalidStockQuery = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();

  if (!normalized || looksLikeStockCode(normalized)) {
    return false;
  }

  if (!SUPPORTED_QUERY_CHARACTERS.test(normalized)) {
    return true;
  }

  const hasLetters = /[A-Z]/.test(normalized);
  const hasDigits = /\d/.test(normalized);

  return hasLetters && hasDigits;
};
