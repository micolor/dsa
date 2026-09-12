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
  /** 返回是否创建成功，供调用方呈现字段级错误。 */
  onCreateList: (name: string) => Promise<boolean>;
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

  // 返回是否成功：调用方需要区分「拉到了空列表」和「请求失败」。
  // 此前这里是静默 catch，导致切换列表失败时界面停留在「新列表名 + 上一个列表的股票」，
  // 新建列表失败时还会提示「已创建」。
  const refreshCodes = useCallback(async (listName?: string): Promise<boolean> => {
    try {
      const result = await systemConfigApi.getWatchlist(listName);
      if (mountedRef.current) {
        setCodes(result);
      }
      return true;
    } catch {
      // 失败时保留已有 codes（不要清空成空列表，那会让误判成「列表被清空」）。
      return false;
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
    const previousListId = activeListId;
    setActiveListId(listId);
    setIsLoading(true);
    const listName = listId === DEFAULT_WATCHLIST_ID ? undefined : listId;
    try {
      const ok = await refreshCodes(listName);
      if (!ok && mountedRef.current) {
        // 回滚激活列表：否则用户看到的是新列表名配着上一个列表的内容，
        // 比单纯的失败更难理解。
        setActiveListId(previousListId);
        showMessage(t('watchlist.actionFailed'));
      }
    } finally {
      if (mountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [activeListId, refreshCodes, showMessage, t]);

  // 返回是否创建成功；成功/失败的文案由调用方（新建列表 Dialog）呈现，
  // 因为它能给出字段级错误提示，而 hook 只能弹一条通用 toast。
  const onCreateList = useCallback(async (name: string): Promise<boolean> => {
    const trimmed = name.trim();
    if (!trimmed || isActioningRef.current) return false;
    isActioningRef.current = true;
    setIsActioning(true);
    const previousListId = activeListId;
    // 创建列表目前是纯前端行为（后端没有 create 接口，列表在首次 add 时才落库），
    // 所以仍然是乐观写入；但写完必须确认新列表真的可读——refreshCodes 以前会静默
    // 吞掉失败，让用户在列表实际不可读时也看到「已创建」。
    const inserted = !lists.some((item) => item.name === trimmed);
    try {
      const key = listNameToKey(trimmed);
      // 乐观更新：纳入候选但不覆盖已有同名列表。
      setLists((prev) => (
        prev.some((item) => item.name === trimmed)
          ? prev
          : [...prev, { key, name: trimmed, count: 0 }]
      ));
      setActiveListId(trimmed);
      const ok = await refreshCodes(trimmed);
      if (!ok) {
        if (mountedRef.current) {
          // 回滚乐观写入，避免留下一个「看起来建好了、打开却是空的」列表。
          if (inserted) {
            setLists((prev) => prev.filter((item) => item.name !== trimmed));
          }
          setActiveListId(previousListId);
        }
        return false;
      }
      return true;
    } catch {
      return false;
    } finally {
      if (mountedRef.current) {
        isActioningRef.current = false;
        setIsActioning(false);
      }
    }
  }, [activeListId, lists, refreshCodes]);

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
