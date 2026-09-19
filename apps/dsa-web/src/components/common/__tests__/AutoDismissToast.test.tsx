import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AutoDismissToast } from '../AutoDismissToast';

const DELAY_MS = 3200;

function renderToast(overrides: Partial<React.ComponentProps<typeof AutoDismissToast>> = {}) {
  const onDismiss = vi.fn();
  const result = render(
    <AutoDismissToast active="已刷新" onDismiss={onDismiss} delayMs={DELAY_MS} {...overrides}>
      <span>已刷新</span>
    </AutoDismissToast>,
  );
  return { onDismiss, ...result };
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe('AutoDismissToast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('dismisses itself once the delay elapses', () => {
    const { onDismiss } = renderToast();

    advance(DELAY_MS - 1);
    expect(onDismiss).not.toHaveBeenCalled();

    advance(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('does not dismiss while the pointer rests on it', () => {
    const { onDismiss } = renderToast();

    fireEvent.mouseEnter(screen.getByText('已刷新'));
    advance(DELAY_MS * 4);

    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('restarts the full countdown after the pointer leaves', () => {
    const { onDismiss } = renderToast();

    // 悬停前先耗掉大部分倒计时，确认移开后拿到的是完整窗口而不是剩余时间。
    advance(DELAY_MS - 100);
    fireEvent.mouseEnter(screen.getByText('已刷新'));
    advance(DELAY_MS * 2);
    fireEvent.mouseLeave(screen.getByText('已刷新'));

    advance(DELAY_MS - 1);
    expect(onDismiss).not.toHaveBeenCalled();

    advance(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('restarts the countdown when a new toast replaces the current one', () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <AutoDismissToast active="第一次" onDismiss={onDismiss} delayMs={DELAY_MS}>
        <span>第一次</span>
      </AutoDismissToast>,
    );

    advance(DELAY_MS - 100);
    rerender(
      <AutoDismissToast active="第二次" onDismiss={onDismiss} delayMs={DELAY_MS}>
        <span>第二次</span>
      </AutoDismissToast>,
    );

    advance(DELAY_MS - 1);
    expect(onDismiss).not.toHaveBeenCalled();

    advance(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('never dismisses while inactive', () => {
    const { onDismiss } = renderToast({ active: null });

    advance(DELAY_MS * 4);

    expect(onDismiss).not.toHaveBeenCalled();
  });
});
