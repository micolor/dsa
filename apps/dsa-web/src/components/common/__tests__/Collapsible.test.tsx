import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Collapsible } from '../Collapsible';

describe('Collapsible', () => {
  it('keeps internal open state when uncontrolled', () => {
    render(
      <Collapsible title="面板">
        <p>内容</p>
      </Collapsible>,
    );
    const toggle = screen.getByRole('button', { name: '面板' });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('honours defaultOpen when uncontrolled', () => {
    render(
      <Collapsible title="面板" defaultOpen>
        <p>内容</p>
      </Collapsible>,
    );
    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('reports toggle intent without changing its own rendered state when controlled', () => {
    const onOpenChange = vi.fn();
    render(
      <Collapsible title="面板" open={false} onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );
    const toggle = screen.getByRole('button', { name: '面板' });

    fireEvent.click(toggle);

    expect(onOpenChange).toHaveBeenCalledWith(true);
    // 受控模式下由父组件决定是否展开；父组件未回传新值时保持收起。
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('renders expanded content when controlled open is true', () => {
    render(
      <Collapsible title="面板" open onOpenChange={() => undefined}>
        <p>内容</p>
      </Collapsible>,
    );
    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('does not write internal state while controlled', () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <Collapsible title="面板" open={false} onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );

    fireEvent.click(screen.getByRole('button', { name: '面板' }));
    fireEvent.click(screen.getByRole('button', { name: '面板' }));

    // 受控期间点击不得写入内部 state；若写了，切回非受控时会漏出陈旧值。
    rerender(
      <Collapsible title="面板">
        <p>内容</p>
      </Collapsible>,
    );

    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('reports toggle intent and still toggles its own state when uncontrolled', () => {
    const onOpenChange = vi.fn();
    render(
      <Collapsible title="面板" onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );
    const toggle = screen.getByRole('button', { name: '面板' });

    fireEvent.click(toggle);

    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(toggle);

    expect(onOpenChange).toHaveBeenLastCalledWith(false);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('caps the expanded panel at 2000px and adds no scroll container by default', () => {
    render(
      <Collapsible title="面板" defaultOpen>
        <p>内容</p>
      </Collapsible>,
    );

    // 默认行为必须不变：既有消费方（MarketReviewReportView 等）仍走 2000px 上限。
    expect(screen.getByTestId('collapsible-panel')).toHaveClass('max-h-[2000px]');
    expect(screen.getByTestId('collapsible-panel')).not.toHaveClass('max-h-none');
    expect(screen.getByTestId('collapsible-panel-body')).not.toHaveClass('overflow-y-auto');
  });

  it('drops the height cap and gives the panel a scroll container when scrollable is on', () => {
    render(
      <Collapsible title="面板" defaultOpen scrollable>
        <p>内容</p>
      </Collapsible>,
    );

    // opts in 的面板不得再被 2000px 静默裁掉，超长内容改为在面板内滚动。
    const panel = screen.getByTestId('collapsible-panel');
    expect(panel).toHaveClass('max-h-none');
    expect(panel).not.toHaveClass('max-h-[2000px]');
    const body = screen.getByTestId('collapsible-panel-body');
    expect(body).toHaveClass('overflow-y-auto');
    expect(body).toHaveClass('max-h-[70vh]');
  });

  it('still hides the panel when collapsed in scrollable mode', () => {
    render(
      <Collapsible title="面板" scrollable>
        <p>内容</p>
      </Collapsible>,
    );

    // 去掉上限不能把收起态一起放开。
    expect(screen.getByTestId('collapsible-panel')).toHaveClass('max-h-0');
    expect(screen.getByRole('button', { name: '面板' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('reports the resolved next value when controlled, not the stale internal state', () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <Collapsible title="面板" open={false} onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );

    fireEvent.click(screen.getByRole('button', { name: '面板' }));
    expect(onOpenChange).toHaveBeenLastCalledWith(true);

    // 受控展开后点击应上报「收起」；内部 state 始终是 false，不能被当成展开态上报。
    rerender(
      <Collapsible title="面板" open onOpenChange={onOpenChange}>
        <p>内容</p>
      </Collapsible>,
    );

    fireEvent.click(screen.getByRole('button', { name: '面板' }));
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });
});
