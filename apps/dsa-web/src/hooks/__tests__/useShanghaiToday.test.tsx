import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useShanghaiToday } from '../useShanghaiToday';

describe('useShanghaiToday', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('reads the date in Asia/Shanghai rather than the browser-local date', () => {
    vi.useFakeTimers();
    // UTC 还是 03-18，上海已经是 03-19：市场日语义必须按上海算。
    vi.setSystemTime(new Date('2026-03-18T23:30:00Z'));

    const { result } = renderHook(() => useShanghaiToday());

    expect(result.current).toBe('2026-03-19');
  });

  it('rolls over to the new date when Shanghai midnight passes', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-18T15:59:00Z')); // 上海 2026-03-18 23:59

    const { result } = renderHook(() => useShanghaiToday());
    expect(result.current).toBe('2026-03-18');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * 60 * 1000);
    });

    expect(result.current).toBe('2026-03-19');
  });

  it('re-checks the date when the document becomes visible again', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-18T15:59:00Z'));

    const { result } = renderHook(() => useShanghaiToday());
    expect(result.current).toBe('2026-03-18');

    // 休眠的设备唤回前台时定时器不会准点补跑，必须自己重新对一次日期。
    vi.setSystemTime(new Date('2026-03-19T01:00:00Z'));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(result.current).toBe('2026-03-19');
  });
});
