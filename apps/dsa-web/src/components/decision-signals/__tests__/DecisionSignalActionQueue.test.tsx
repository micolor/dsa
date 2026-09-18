import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DecisionSignalActionQueue } from '../DecisionSignalActionQueue';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import type { DecisionSignalItem } from '../../../types/decisionSignals';

const signal = (
  id: number,
  stockName: string,
  action: DecisionSignalItem['action'],
  planQuality: DecisionSignalItem['planQuality'],
): DecisionSignalItem => ({
  id,
  stockCode: String(id).padStart(6, '0'),
  stockName,
  action,
  planQuality,
  status: 'active',
  market: 'cn',
  sourceType: 'analysis',
  decisionProfile: 'balanced',
  createdAt: '2026-06-17T09:30:00Z',
} as DecisionSignalItem);

type RenderQueueOptions = {
  selectedId?: number | null;
  onSelect?: (item: DecisionSignalItem) => void;
};

const renderQueue = (items: DecisionSignalItem[], options: RenderQueueOptions = {}) => {
  // jsdom 的 navigator.language 是 en-US，UiLanguageProvider 会因此解析成 en；
  // 与本目录其他测试一致，先固定语言再从断言里读中文文案。
  window.localStorage.setItem('dsa.uiLanguage', 'zh');
  return render(
    <UiLanguageProvider>
      <DecisionSignalActionQueue
        items={items}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onSelect={options.onSelect ?? vi.fn()}
        selectedId={options.selectedId ?? null}
      />
    </UiLanguageProvider>,
  );
};

describe('DecisionSignalActionQueue', () => {
  it('renders the three groups with their items', () => {
    const onSelect = vi.fn();
    renderQueue(
      [
        signal(1, '贵州茅台', 'buy', 'complete'),
        signal(2, '宁德时代', 'buy', 'minimal'),
        signal(3, '腾讯控股', 'watch', 'complete'),
      ],
      { onSelect },
    );

    expect(screen.getByRole('heading', { name: '可以动手' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '需要确认' })).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('宁德时代')).toBeInTheDocument();

    // 点第二张卡片必须回传第二张卡片自己的信号。只断言「回调被调用」放不出
    // onSelect={() => onSelect(items[0])} 这类串档错误，所以这里断言 id。
    fireEvent.click(screen.getByRole('button', { name: '查看 宁德时代 AI 建议详情' }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe(2);
  });

  it('collapses the watch group by default and expands it on demand', () => {
    renderQueue([signal(3, '腾讯控股', 'watch', 'complete')]);

    // 「观察」档走受控模式 + 条件挂载：折叠按钮本身始终在，断言的是它的可访问展开状态。
    const watchToggle = screen.getByRole('button', { name: /^观察/ });
    expect(watchToggle).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(watchToggle);

    expect(watchToggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps the collapsed watch group out of the tab order and mounts it once expanded', () => {
    renderQueue([
      signal(1, '贵州茅台', 'buy', 'complete'),
      signal(3, '腾讯控股', 'watch', 'complete'),
    ]);

    // 收起的内容不能只是 max-h-0 + opacity-0：那样卡片按钮仍在 Tab 顺序里，
    // 键盘用户会 Tab 进看不见的卡片（焦点表现为「消失」）。这里断言的是不可达。
    const watchToggle = screen.getByRole('button', { name: /^观察/ });
    expect(watchToggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: '查看 腾讯控股 AI 建议详情' })).not.toBeInTheDocument();

    fireEvent.click(watchToggle);

    // 展开后重新可达。
    expect(screen.getByRole('button', { name: '查看 腾讯控股 AI 建议详情' })).toBeInTheDocument();
    expect(watchToggle).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(watchToggle);

    expect(screen.queryByRole('button', { name: '查看 腾讯控股 AI 建议详情' })).not.toBeInTheDocument();
  });

  it('renders the per-group item count on both the visible and the collapsed header', () => {
    renderQueue([
      signal(1, '贵州茅台', 'buy', 'complete'),
      signal(2, '宁德时代', 'add', 'complete'),
      signal(3, '腾讯控股', 'buy', 'minimal'),
      signal(4, '美团', 'watch', 'complete'),
    ]);

    const actionableSection = screen.getByRole('heading', { name: '可以动手' }).closest('section') as HTMLElement;
    const reviewSection = screen.getByRole('heading', { name: '需要确认' }).closest('section') as HTMLElement;

    // 平铺档的条数在标题里、折叠档的条数拼在 Collapsible 标题里，是两条独立
    // 渲染路径；各自取数才能钉住「档内条数」而不是只钉住能匹配的前缀文案。
    expect(within(actionableSection).getByText('2 条')).toBeInTheDocument();
    expect(within(reviewSection).getByText('1 条')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '观察 · 1 条' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('highlights only the selected card', () => {
    renderQueue(
      [signal(1, '贵州茅台', 'buy', 'complete'), signal(2, '宁德时代', 'buy', 'complete')],
      { selectedId: 2 },
    );

    // 选中态只体现在卡片边框/底色上，卡片没有 aria-selected 之类可断言的属性，
    // 因此这里按 DecisionSignalCard 实际渲染出的选中修饰类取数。
    expect(screen.getByRole('button', { name: '查看 宁德时代 AI 建议详情' })).toHaveClass('border-cyan/50');
    expect(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).not.toHaveClass('border-cyan/50');
  });

  it('leaves every card unselected when selectedId matches no item', () => {
    renderQueue(
      [signal(1, '贵州茅台', 'buy', 'complete'), signal(2, '宁德时代', 'buy', 'complete')],
      { selectedId: 999 },
    );

    expect(screen.getByRole('button', { name: '查看 贵州茅台 AI 建议详情' })).not.toHaveClass('border-cyan/50');
    expect(screen.getByRole('button', { name: '查看 宁德时代 AI 建议详情' })).not.toHaveClass('border-cyan/50');
  });

  it('shows the whole-queue empty state when there is nothing to do', () => {
    renderQueue([]);
    expect(screen.getByText('暂无待处理信号')).toBeInTheDocument();
  });

  it('shows a per-group empty state for groups without signals', () => {
    renderQueue([signal(1, '贵州茅台', 'buy', 'complete')]);
    const reviewSection = screen.getByRole('heading', { name: '需要确认' }).closest('section');
    expect(reviewSection).not.toBeNull();
    expect(within(reviewSection as HTMLElement).getByText('本组暂无信号。')).toBeInTheDocument();
  });

  it('surfaces a retryable error alert', () => {
    const onRetry = vi.fn();
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    render(
      <UiLanguageProvider>
        <DecisionSignalActionQueue
          items={[]}
          loading={false}
          // ApiErrorAlert 会读 error.rawMessage.trim()，漏掉该字段会在运行时抛错，
          // 因此夹具必须带上它（空串即可，会让「详情」折叠块不渲染）。
          error={{ title: 'x', message: '待处理信号加载失败', rawMessage: '' } as never}
          onRetry={onRetry}
          onSelect={vi.fn()}
          selectedId={null}
        />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
