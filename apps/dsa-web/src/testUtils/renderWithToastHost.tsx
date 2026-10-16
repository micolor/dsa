import type React from 'react';
import { render } from '@testing-library/react';
import type { RenderOptions, RenderResult } from '@testing-library/react';
import { ToastHostProvider } from '../contexts/ToastHostContext';

/**
 * 渲染页面组件的测试专用入口：在待测组件外面挂上全站提示宿主。
 *
 * 与 `App` 的挂法一致——浮层提示只由 `ToastHost` 渲染，页面自己不画浮层。所以断言提示文案
 * 的测试必须带上宿主，否则 `Toast.*` / `useToastSync` 的提示一条都不会出现（store 是全局的，
 * 宿主不在就等于没有出口）。
 *
 * 用法：`import { renderWithToastHost as render } from '../../testUtils/renderWithToastHost';`
 * 调用方仍按 `render` 的用法写，`rerender` 会自动补回宿主。
 */
export function renderWithToastHost(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
): RenderResult {
  const result = render(<ToastHostProvider>{ui}</ToastHostProvider>, options);
  return {
    ...result,
    rerender: (next: React.ReactNode) =>
      result.rerender(<ToastHostProvider>{next}</ToastHostProvider>),
  };
}
