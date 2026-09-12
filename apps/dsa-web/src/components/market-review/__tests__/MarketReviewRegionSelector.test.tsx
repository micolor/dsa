import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { MarketReviewRegionSelector } from '../MarketReviewRegionSelector';
import { serializeMarketReviewRegions } from '../../../utils/marketReviewRegion';

describe('MarketReviewRegionSelector', () => {
  beforeEach(() => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
  });

  it('serializes canonical UI selections at the HTTP boundary', () => {
    expect(serializeMarketReviewRegions(['kr', 'jp'])).toBe('jp,kr');
    expect(serializeMarketReviewRegions(['cn', 'hk', 'us', 'jp', 'kr'])).toBe('both');
  });

  it('keeps the runtime-resolved server default opaque and emits a canonical override', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector onChange={onChange} />
      </UiLanguageProvider>,
    );

    // 触发按钮不再挂 aria-label，它的可访问名就是可见文案；下面按可见文案查询，
    // 同时锁住 WCAG 2.5.3 Label in Name：语音控制用户按可见文字能激活该控件。
    const trigger = screen.getByRole('button', { name: '服务器默认' });
    expect(trigger).toHaveTextContent('服务器默认');
    expect(trigger).not.toHaveTextContent('A 股');
    fireEvent.click(trigger);
    expect(screen.getByText('由服务器在提交时决定')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /A 股/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /美股/ })).not.toBeChecked();

    fireEvent.click(screen.getByRole('checkbox', { name: /日股/ }));
    expect(onChange).toHaveBeenLastCalledWith(['jp']);
  });

  it('supports all markets and restoring the server default', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector
          value={['us']}
          onChange={onChange}
        />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '美股' }));
    fireEvent.click(screen.getByRole('button', { name: '全部市场' }));
    expect(onChange).toHaveBeenLastCalledWith(['cn', 'hk', 'us', 'jp', 'kr']);

    fireEvent.click(screen.getByRole('button', { name: /服务器默认/ }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it('keeps at least one market selected in override mode', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} onChange={onChange} />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '美股' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /美股/ }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes an open menu and blocks every option when disabled', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} onChange={onChange} />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '美股' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    const cnCheckbox = screen.getByRole('checkbox', { name: /A 股/ });

    rerender(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} disabled onChange={onChange} />
      </UiLanguageProvider>,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '美股' })).toBeDisabled();
    fireEvent.click(cnCheckbox);
    expect(onChange).not.toHaveBeenCalled();
  });
});
