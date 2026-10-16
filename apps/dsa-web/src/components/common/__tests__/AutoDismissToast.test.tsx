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

  it('does not dismiss while keyboard focus rests inside it', () => {
    const onDismiss = vi.fn();
    render(
      <AutoDismissToast active="已刷新" onDismiss={onDismiss} delayMs={DELAY_MS}>
        <button type="button">关闭</button>
      </AutoDismissToast>,
    );

    fireEvent.focusIn(screen.getByRole('button'));
    advance(DELAY_MS * 4);

    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('restarts the full countdown after focus leaves the toast', () => {
    const onDismiss = vi.fn();
    render(
      <AutoDismissToast active="已刷新" onDismiss={onDismiss} delayMs={DELAY_MS}>
        <button type="button">关闭</button>
      </AutoDismissToast>,
    );

    // 聚焦前先耗掉大部分倒计时，确认焦点离开后拿到的是完整窗口而不是剩余时间。
    advance(DELAY_MS - 100);
    fireEvent.focusIn(screen.getByRole('button'));
    advance(DELAY_MS * 2);
    fireEvent.focusOut(screen.getByRole('button'), { relatedTarget: null });

    advance(DELAY_MS - 1);
    expect(onDismiss).not.toHaveBeenCalled();

    advance(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('keeps pausing while focus moves between its own controls', () => {
    const onDismiss = vi.fn();
    render(
      <AutoDismissToast active="已刷新" onDismiss={onDismiss} delayMs={DELAY_MS}>
        <button type="button">关闭</button>
        <button type="button">重试</button>
      </AutoDismissToast>,
    );

    fireEvent.focusIn(screen.getByText('关闭'));
    // React 的 onBlur 走 focusout，焦点在提示内部换控件时也会触发；这不是「离开」。
    fireEvent.focusOut(screen.getByText('关闭'), { relatedTarget: screen.getByText('重试') });
    advance(DELAY_MS * 4);

    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('never dismisses while inactive', () => {
    const { onDismiss } = renderToast({ active: null });

    advance(DELAY_MS * 4);

    expect(onDismiss).not.toHaveBeenCalled();
  });
});
