import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type React from 'react';
import { ToastHostProvider } from '../ToastHostContext';
import { Toast, useToastSync } from '../../components/common/toastStore';
import type { ParsedApiError } from '../../api/error';

const parsedError: ParsedApiError = {
  title: '保存失败',
  message: '请求被服务端拒绝',
  rawMessage: '{"detail":"rejected"}',
  status: 400,
  category: 'http_error',
};

/** 取某条提示的卡片节点：它自己带 `role="alert"`，容器不带。 */
function cardOf(message: string): HTMLElement {
  const node = within(screen.getByTestId('toast-host')).getByText(message).closest('[role="alert"]');
  if (!node) {
    throw new Error(`找不到「${message}」的提示卡片`);
  }
  return node as HTMLElement;
}

const SyncProbe: React.FC<{ active: string | null }> = ({ active }) => {
  useToastSync('probe', active, (value) => ({ variant: 'warning', message: value }));
  return null;
};

/**
 * 首页窄屏下的真实结构：同一个 `sidebarContent` 同时挂在两处 —— 桌面侧栏那份由 CSS 隐藏
 * （`hidden md:flex`）但 React 照常挂载，抽屉那份随开关挂载 / 卸载 —— 两处各调一次
 * `useToastSync('home-batch-status', …)`。复刻成两个并列的兄弟节点，而不是同一层里的数组，
 * 否则改 children 数量会让 React 把整棵子树重挂，反而掩盖掉要测的那次卸载。
 */
const DuplicatedOwner: React.FC<{ active: string | null }> = ({ active }) => {
  useToastSync('home-batch-status', active, (value) => ({ variant: 'warning', message: value }));
  return null;
};

const DuplicatedOwnerTree: React.FC<{ drawer: boolean }> = ({ drawer }) => (
  <ToastHostProvider>
    <div data-testid="sidebar-slot">
      <DuplicatedOwner active="批量提交完成" />
    </div>
    <div data-testid="drawer-slot">
      {drawer ? <DuplicatedOwner active="批量提交完成" /> : null}
    </div>
  </ToastHostProvider>
);

describe('ToastHost', () => {
  // store 是模块级单例，用例之间必须清空，否则前一条会串到下一条的断言里。
  // 放在 beforeEach：此时上一条用例的组件已被 RTL 卸载。
  beforeEach(() => {
    Toast.clear();
  });

  it('renders store entries into the global host instead of in place', () => {
    render(
      <ToastHostProvider>
        <div data-testid="page-body">页面正文</div>
      </ToastHostProvider>,
    );

    act(() => {
      Toast.success('已保存');
    });

    const host = screen.getByTestId('toast-host');
    expect(within(host).getByText('已保存')).toBeInTheDocument();
    // 关键契约：提示不在页面原位渲染，否则等于没搬。
    const body = screen.getByTestId('page-body');
    expect(body).not.toHaveTextContent('已保存');
  });

  it('keeps multiple toasts as siblings of the same host so they stack in one column', () => {
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      Toast.info('第一条');
      Toast.info('第二条');
    });

    expect(Array.from(screen.getByTestId('toast-host').children).map((child) => child.textContent))
      .toEqual(['第一条', '第二条']);
  });

  it('leaves the host container without an alert role so singular role queries stay unambiguous', () => {
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      Toast.info('只有提示本身带 alert');
    });

    expect(screen.getByRole('alert')).toHaveTextContent('只有提示本身带 alert');
    expect(screen.getByTestId('toast-host')).not.toHaveAttribute('role');
  });

  it('takes the close button as the user dismissing the toast', () => {
    const onDismiss = vi.fn();
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      Toast.show({ variant: 'danger', message: '保存失败', onDismiss });
    });

    fireEvent.click(within(cardOf('保存失败')).getByRole('button'));

    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('保存失败')).not.toBeInTheDocument();
  });

  it('takes the same id as an upsert instead of stacking a second card', () => {
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      Toast.show({ id: 'save-error', message: '第一次失败' });
    });
    act(() => {
      Toast.show({ id: 'save-error', message: '第二次失败' });
    });

    expect(screen.getByTestId('toast-host').children).toHaveLength(1);
    expect(screen.getByText('第二次失败')).toBeInTheDocument();
    expect(screen.queryByText('第一次失败')).not.toBeInTheDocument();
  });

  it('drops the oldest card past the visible cap so the column cannot fill the screen', () => {
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      for (let index = 1; index <= 6; index += 1) {
        Toast.info(`第 ${index} 条`);
      }
    });

    expect(screen.getByTestId('toast-host').children).toHaveLength(5);
    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument();
    expect(screen.getByText('第 6 条')).toBeInTheDocument();
  });

  it('renders title, message and expandable raw text off a parsed API error', () => {
    render(<ToastHostProvider>{null}</ToastHostProvider>);

    act(() => {
      Toast.error(parsedError);
    });

    const card = cardOf('请求被服务端拒绝');
    expect(within(card).getByText('保存失败')).toBeInTheDocument();
    expect(within(card).getByText('{"detail":"rejected"}')).toBeInTheDocument();
  });

  it('renders nothing when no host is mounted: the store is not a second rendering path', () => {
    render(
      <div data-testid="page-body">
        <SyncProbe active="孤立提示" />
      </div>,
    );

    expect(screen.queryByTestId('toast-host')).not.toBeInTheDocument();
    expect(screen.getByTestId('page-body')).not.toHaveTextContent('孤立提示');
  });
});

describe('useToastSync', () => {
  beforeEach(() => {
    Toast.clear();
  });

  it('follows the state it is given, in both directions', () => {
    const view = render(
      <ToastHostProvider>
        <SyncProbe active="仓位已降级" />
      </ToastHostProvider>,
    );
    expect(screen.getByText('仓位已降级')).toBeInTheDocument();

    view.rerender(
      <ToastHostProvider>
        <SyncProbe active={null} />
      </ToastHostProvider>,
    );
    expect(screen.queryByText('仓位已降级')).not.toBeInTheDocument();

    view.rerender(
      <ToastHostProvider>
        <SyncProbe active="又降级了" />
      </ToastHostProvider>,
    );
    expect(screen.getByText('又降级了')).toBeInTheDocument();
  });

  it('takes its own toast down when the component unmounts', () => {
    const view = render(
      <ToastHostProvider>
        <SyncProbe active="卸载前" />
      </ToastHostProvider>,
    );
    expect(screen.getByText('卸载前')).toBeInTheDocument();

    view.rerender(<ToastHostProvider>{null}</ToastHostProvider>);

    expect(screen.queryByText('卸载前')).not.toBeInTheDocument();
  });

  it('keeps the toast up when only one of two components sharing the id unmounts', () => {
    const view = render(<DuplicatedOwnerTree drawer />);
    expect(screen.getByText('批量提交完成')).toBeInTheDocument();

    view.rerender(<DuplicatedOwnerTree drawer={false} />);

    // 抽屉那份卸载后，桌面栏那份还带着同一个 active，提示必须留在屏上。
    expect(screen.getByText('批量提交完成')).toBeInTheDocument();
  });

  it('takes the toast down once the last component sharing the id unmounts', () => {
    const view = render(
      <ToastHostProvider>
        <SyncProbe active="批量提交完成" />
        <SyncProbe active="批量提交完成" />
      </ToastHostProvider>,
    );
    expect(screen.getByText('批量提交完成')).toBeInTheDocument();

    view.rerender(<ToastHostProvider>{null}</ToastHostProvider>);

    expect(screen.queryByText('批量提交完成')).not.toBeInTheDocument();
  });
});
