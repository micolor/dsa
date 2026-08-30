import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { systemConfigApi, type WatchlistSetInfo } from '../api/systemConfig';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { findMatchingStockCode, includesStockCode } from '../utils/stockCode';

/** 操作提示消息自动消失的时长。 */
const MESSAGE_AUTO_DISMISS_MS = 3000;

/** 默认自选列表（STOCK_LIST）在 UI 中的稳定标识。 */
export const DEFAULT_WATCHLIST_ID = '__default__';

/** 由列表名生成配置键，与后端 `_watchlist_env_key` 的归一化保持一致。 */
function listNameToKey(name: string): string {
  const normalized = name.trim().toUpperCase().replace(/[^\w]+/g, '_');
  return `WATCHLIST_${normalized}`;
}

/** 服务端命名列表的展示条目（默认列表的 name 为默认标识）。 */
export interface WatchlistOption {
  key: string;
  name: string;
  count: number;
  isDefault: boolean;
}

export interface UseWatchlistReturn {
  watchlistCodes: string[];
  isLoading: boolean;
  isActioning: boolean;
  actionMessage: string | null;
  /** 可选列表（首个恒为默认列表）。 */
  watchlistOptions: WatchlistOption[];
  /** 当前激活列表的展示标识；默认列表为 DEFAULT_WATCHLIST_ID。 */
  activeListId: string;
  onCreateList: (name: string) => Promise<void>;
  onSwitchList: (listId: string) => Promise<void>;
  isInWatchlist: (stockCode: string) => boolean;
  addToWatchlist: (stockCode: string) => Promise<void>;
  removeFromWatchlist: (stockCode: string) => Promise<void>;
  toggleWatchlist: (stockCode: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useWatchlist(): UseWatchlistReturn {
  const { t } = useUiLanguage();
  const [codes, setCodes] = useState<string[]>([]);
  const [lists, setLists] = useState<WatchlistSetInfo[]>([]);
  const [activeListId, setActiveListId] = useState<string>(DEFAULT_WATCHLIST_ID);
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

  // 当前激活列表进入后端 API 的 list_name；默认列表传 undefined。
  const activeListName = useMemo(
    () => (activeListId === DEFAULT_WATCHLIST_ID ? undefined : activeListId),
    [activeListId],
  );

  const refreshCodes = useCallback(async (listName?: string) => {
    try {
      const result = await systemConfigApi.getWatchlist(listName);
      if (mountedRef.current) {
        setCodes(result);
      }
    } catch {
      // keep existing codes
    }
  }, []);

  const refreshLists = useCallback(async (): Promise<WatchlistSetInfo[]> => {
    try {
      const result = await systemConfigApi.getWatchlistLists();
      if (mountedRef.current) {
        setLists(result);
      }
      return result;
    } catch {
      return [];
    }
  }, []);

  // 仅在首次挂载时拉取默认列表与命名列表；后续切换由 onSwitchList 显式触发，
  // 避免依赖 activeListName 的 effect 在切换时重复拉取。
  const refresh = useCallback(async () => {
    await refreshCodes(activeListName);
  }, [activeListName, refreshCodes]);

  const initialLoadedRef = useRef(false);
  useEffect(() => {
    if (initialLoadedRef.current) return;
    initialLoadedRef.current = true;
    setIsLoading(true);
    void Promise.all([refreshCodes(undefined), refreshLists()]).finally(() => {
      if (mountedRef.current) {
        setIsLoading(false);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const watchlistOptions = useMemo<WatchlistOption[]>(() => {
    const options: WatchlistOption[] = [
      { key: DEFAULT_WATCHLIST_ID, name: t('watchlist.defaultListName'), count: 0, isDefault: true },
      ...lists.map((item) => ({
        key: item.name,
        name: item.name,
        count: item.count,
        isDefault: false,
      })),
    ];
    // 默认列表 count 反映当前激活的是默认列表时的 codes；否则读取不到，保留 0。
    const defaultOption = options[0];
    if (activeListId === DEFAULT_WATCHLIST_ID) {
      defaultOption.count = codes.length;
    }
    return options;
  }, [lists, t, activeListId, codes.length]);

  const isInWatchlist = useCallback(
    (stockCode: string) => includesStockCode(codes, stockCode),
    [codes],
  );

  const addToWatchlist = useCallback(async (stockCode: string) => {
    if (!stockCode || isActioningRef.current) return;
    isActioningRef.current = true;
    setIsActioning(true);
    try {
      const result = await systemConfigApi.addToWatchlist(stockCode, activeListName);
      if (mountedRef.current) {
        setCodes(result);
        showMessage(t('watchlist.addedMessage', { code: stockCode }));
        void refreshLists();
      }
    } catch {
      if (mountedRef.current) showMessage(t('watchlist.actionFailed'));
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [activeListName, showMessage, t, refreshLists]);

  const removeFromWatchlist = useCallback(async (stockCode: string) => {
    if (!stockCode || isActioningRef.current) return;
    isActioningRef.current = true;
    setIsActioning(true);
    try {
      const result = await systemConfigApi.removeFromWatchlist(stockCode, activeListName);
      if (mountedRef.current) {
        setCodes(result);
        showMessage(t('watchlist.removedMessage', { code: stockCode }));
        void refreshLists();
      }
    } catch {
      if (mountedRef.current) showMessage(t('watchlist.actionFailed'));
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [activeListName, showMessage, t, refreshLists]);

  const toggleWatchlist = useCallback(async (stockCode: string) => {
    const existingStockCode = findMatchingStockCode(codes, stockCode);
    if (existingStockCode) {
      await removeFromWatchlist(existingStockCode);
    } else {
      await addToWatchlist(stockCode);
    }
  }, [codes, removeFromWatchlist, addToWatchlist]);

  const onSwitchList = useCallback(async (listId: string) => {
    if (listId === activeListId) return;
    setActiveListId(listId);
    setIsLoading(true);
    const listName = listId === DEFAULT_WATCHLIST_ID ? undefined : listId;
    try {
      await refreshCodes(listName);
    } finally {
      if (mountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [activeListId, refreshCodes]);

  const onCreateList = useCallback(async (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || isActioningRef.current) return;
    isActioningRef.current = true;
    setIsActioning(true);
    try {
      const key = listNameToKey(trimmed);
      // 乐观更新：纳入候选但不覆盖已有同名列表。
      setLists((prev) => {
        const exists = prev.some((item) => item.name === trimmed);
        if (exists) return prev;
        return [...prev, { key, name: trimmed, count: 0 }];
      });
      setActiveListId(trimmed);
      await refreshCodes(trimmed);
      showMessage(t('watchlist.listCreatedMessage', { name: trimmed }));
    } catch {
      if (mountedRef.current) showMessage(t('watchlist.actionFailed'));
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [showMessage, t, refreshCodes]);

  return {
    watchlistCodes: codes,
    isLoading,
    isActioning,
    actionMessage,
    watchlistOptions,
    activeListId,
    onCreateList,
    onSwitchList,
    isInWatchlist,
    addToWatchlist,
    removeFromWatchlist,
    toggleWatchlist,
    refresh,
  };
}
