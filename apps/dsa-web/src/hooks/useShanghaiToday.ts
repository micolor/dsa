import { useEffect, useState } from 'react';
import { getTodayInShanghai } from '../utils/format';

/** 上海全年固定 UTC+8、没有夏令时，「下一个上海零点」可以直接按固定偏移算。 */
const SHANGHAI_OFFSET_MS = 8 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
/**
 * 定时器多留 1 秒：正好落在零点那一毫秒触发时，`Intl` 取到的还可能算作前一天。
 */
const MIDNIGHT_MARGIN_MS = 1000;

/** 从现在到下一个上海零点之间的毫秒数。 */
function msUntilNextShanghaiMidnight(nowMs: number): number {
  const shifted = nowMs + SHANGHAI_OFFSET_MS;
  const nextMidnight = (Math.floor(shifted / DAY_MS) + 1) * DAY_MS;
  return nextMidnight - shifted + MIDNIGHT_MARGIN_MS;
}

/**
 * 当前的上海日期（YYYY-MM-DD），跨零点自己更新。
 *
 * 首页的「今日」是一个**随时间变化**的事实：今日排行、自选行的「今日已分析」、
 * 「分析未分析」要提交的批次都按它过滤。此前这里是 `useMemo(() => getTodayInShanghai(), [])`,
 * 只在挂载时算一次，于是常驻的桌面端 / 隔夜标签页跨零点后还按昨天的日期过滤 ——
 * 今日列表变空、覆盖数恒为 0，还会把当天已经分析过的股票当成未分析重跑一轮，只有刷新才能恢复。
 *
 * 两条更新路径缺一不可：定时器负责正常跨零点，`visibilitychange` 负责设备休眠期间定时器被
 * 推迟或冻结的情况（回到前台补一次）。两者都只在日期真的变了才写 state，同一天重复同步
 * 不会触发重渲染。
 */
export function useShanghaiToday(): string {
  const [dateKey, setDateKey] = useState(getTodayInShanghai);

  useEffect(() => {
    let timer: number | undefined;

    const sync = () => {
      const next = getTodayInShanghai();
      setDateKey((prev) => (prev === next ? prev : next));
    };

    const scheduleMidnightRollover = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      timer = window.setTimeout(() => {
        sync();
        scheduleMidnightRollover();
      }, msUntilNextShanghaiMidnight(Date.now()));
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') {
        return;
      }
      // 睡了一夜回来时旧定时器的到期时间早已过去，同步完重排一次，让它对准下一个零点。
      sync();
      scheduleMidnightRollover();
    };

    scheduleMidnightRollover();
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  return dateKey;
}
