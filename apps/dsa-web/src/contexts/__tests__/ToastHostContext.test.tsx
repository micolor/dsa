import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ToastHostProvider, ToastPortal, useToastHost } from '../ToastHostContext';

const HostProbe: React.FC = () => {
  const context = useToastHost();
  return <span data-testid="host-role">{context?.host?.getAttribute('role') ?? 'none'}</span>;
};

describe('ToastHostContext', () => {
  it('renders portal children into the global host instead of in place', () => {
    render(
      <ToastHostProvider>
        <div data-testid="page-body">
          <ToastPortal>
            <p>提示内容</p>
          </ToastPortal>
        </div>
      </ToastHostProvider>,
    );

    const host = screen.getByTestId('toast-host');
    expect(host).toContainElement(screen.getByText('提示内容'));
    // 关键契约：提示不在页面原位渲染，否则等于没搬。
    expect(screen.getByTestId('page-body')).not.toContainElement(screen.getByText('提示内容'));
  });

  it('keeps multiple toasts as siblings of the same host so they stack in one column', () => {
    render(
      <ToastHostProvider>
        <ToastPortal>
          <p>第一条</p>
        </ToastPortal>
        <ToastPortal>
          <p>第二条</p>
        </ToastPortal>
      </ToastHostProvider>,
    );

    const host = screen.getByTestId('toast-host');
    expect(Array.from(host.children).map((child) => child.textContent)).toEqual(['第一条', '第二条']);
  });

  it('leaves the host container without an alert role so singular role queries stay unambiguous', () => {
    render(
      <ToastHostProvider>
        <ToastPortal>
          <div role="alert">只有提示本身带 alert</div>
        </ToastPortal>
      </ToastHostProvider>,
    );

    // 容器若带 role，这里会抛"命中多个元素"。
    expect(screen.getByRole('alert')).toHaveTextContent('只有提示本身带 alert');
    expect(screen.getByTestId('toast-host')).not.toHaveAttribute('role');
  });

  it('renders children in place when there is no provider', () => {
    render(
      <div data-testid="page-body">
        <ToastPortal>
          <p>兜底提示</p>
        </ToastPortal>
      </div>,
    );

    expect(screen.getByTestId('page-body')).toContainElement(screen.getByText('兜底提示'));
    expect(screen.queryByTestId('toast-host')).not.toBeInTheDocument();
  });

  it('exposes the host node through useToastHost so callers can detect a missing provider', () => {
    render(
      <ToastHostProvider>
        <HostProbe />
      </ToastHostProvider>,
    );

    expect(screen.getByTestId('host-role')).toHaveTextContent('none');
  });

  it('reports no context outside the provider', () => {
    render(<HostProbe />);

    expect(screen.getByTestId('host-role')).toHaveTextContent('none');
  });
});
