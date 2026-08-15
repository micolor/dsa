import { useCallback, useEffect, useRef, useState } from 'react';
import { systemConfigApi } from '../api/systemConfig';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { findMatchingStockCode, includesStockCode } from '../utils/stockCode';

/** 操作提示消息自动消失的时长。 */
const MESSAGE_AUTO_DISMISS_MS = 3000;

export interface UseWatchlistReturn {
  watchlistCodes: string[];
  isLoading: boolean;
  isActioning: boolean;
  actionMessage: string | null;
  isInWatchlist: (stockCode: string) => boolean;
  addToWatchlist: (stockCode: string) => Promise<void>;
  removeFromWatchlist: (stockCode: string) => Promise<void>;
  toggleWatchlist: (stockCode: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useWatchlist(): UseWatchlistReturn {
  const { t } = useUiLanguage();
  const [codes, setCodes] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isActioning, setIsActioning] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const messageTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  // 用 ref 镜像 isActioning，避免增删回调依赖该 state 而改变身份，从而保住行的 memo。
  const isActioningRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (messageTimerRef.current !== null) {
        window.clearTimeout(messageTimerRef.current);
      }
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const result = await systemConfigApi.getWatchlist();
      if (mountedRef.current) {
        setCodes(result);
      }
    } catch {
      // keep existing codes
    }
  }, []);

  useEffect(() => {
    setIsLoading(true);
    void refresh().finally(() => {
      if (mountedRef.current) {
        setIsLoading(false);
      }
    });
  }, [refresh]);

  const showMessage = useCallback((msg: string) => {
    if (messageTimerRef.current !== null) {
      window.clearTimeout(messageTimerRef.current);
    }
    setActionMessage(msg);
    messageTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current) {
        setActionMessage(null);
      }
    }, MESSAGE_AUTO_DISMISS_MS);
  }, []);

  const isInWatchlist = useCallback(
    (stockCode: string) => includesStockCode(codes, stockCode),
    [codes],
  );

  const addToWatchlist = useCallback(async (stockCode: string) => {
    if (!stockCode || isActioningRef.current) return;
    isActioningRef.current = true;
    setIsActioning(true);
    try {
      const result = await systemConfigApi.addToWatchlist(stockCode);
      if (mountedRef.current) {
        setCodes(result);
        showMessage(t('watchlist.addedMessage', { code: stockCode }));
      }
    } catch {
      if (mountedRef.current) showMessage(t('watchlist.actionFailed'));
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [showMessage, t]);

  const removeFromWatchlist = useCallback(async (stockCode: string) => {
    if (!stockCode || isActioningRef.current) return;
    isActioningRef.current = true;
    setIsActioning(true);
    try {
      const result = await systemConfigApi.removeFromWatchlist(stockCode);
      if (mountedRef.current) {
        setCodes(result);
        showMessage(t('watchlist.removedMessage', { code: stockCode }));
      }
    } catch {
      if (mountedRef.current) showMessage(t('watchlist.actionFailed'));
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [showMessage, t]);

  const toggleWatchlist = useCallback(async (stockCode: string) => {
    const existingStockCode = findMatchingStockCode(codes, stockCode);
    if (existingStockCode) {
      await removeFromWatchlist(existingStockCode);
    } else {
      await addToWatchlist(stockCode);
    }
  }, [codes, removeFromWatchlist, addToWatchlist]);

  return {
    watchlistCodes: codes,
    isLoading,
    isActioning,
    actionMessage,
    isInWatchlist,
    addToWatchlist,
    removeFromWatchlist,
    toggleWatchlist,
    refresh,
  };
}
