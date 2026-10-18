import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantDock } from '../AssistantDock';

const mockClearCompletionBadge = vi.fn();
const mockStoreState = { completionBadge: false, clearCompletionBadge: mockClearCompletionBadge };

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: typeof mockStoreState) => unknown) => selector(mockStoreState),
}));

// 悬浮助手只负责外壳：同一个 ChatPage 的 dock 形态。这里换成探针，
// 避免在悬浮层测试里再起一整套 ChatPage 的网络与 store 模拟。
vi.mock('../../../pages/ChatPage', () => ({
  default: ({ variant }: { variant?: string }) => (
    <div data-testid="chat-page-stub" data-variant={variant} />
  ),
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
};

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="*"
          element={(
            <>
              <AssistantDock />
              <LocationProbe />
            </>
          )}
        />
      </Routes>
    </MemoryRouter>,
  );

describe('AssistantDock', () => {
  beforeEach(() => {
    mockStoreState.completionBadge = false;
    mockClearCompletionBadge.mockClear();
  });

  it('shows the launcher on every page except the full chat route', () => {
    const { unmount } = renderAt('/portfolio');
    expect(screen.getByRole('button', { name: '打开站内助手' })).toBeInTheDocument();
    unmount();

    renderAt('/chat');
    expect(screen.queryByRole('button', { name: '打开站内助手' })).not.toBeInTheDocument();
  });

  it('opens the same chat workspace in its compact form and closes with Escape', async () => {
    renderAt('/portfolio');

    fireEvent.click(screen.getByRole('button', { name: '打开站内助手' }));

    const stub = await screen.findByTestId('chat-page-stub');
    expect(stub).toHaveAttribute('data-variant', 'dock');
    // 入口按钮与面板头部关闭按钮同名，入口那一颗带 aria-expanded。
    expect(
      screen.getAllByRole('button', { name: '收起站内助手' })
        .some((button) => button.getAttribute('aria-expanded') === 'true'),
    ).toBe(true);

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByTestId('chat-page-stub')).not.toBeInTheDocument();
    });
  });

  it('marks an unread reply and clears the badge when the panel is opened', () => {
    mockStoreState.completionBadge = true;
    renderAt('/portfolio');

    const launcher = screen.getByRole('button', { name: '站内助手有新回复，打开查看' });
    fireEvent.click(launcher);

    expect(mockClearCompletionBadge).toHaveBeenCalled();
  });

  it('expands to the full chat page without keeping a second workspace mounted', async () => {
    renderAt('/portfolio');

    fireEvent.click(screen.getByRole('button', { name: '打开站内助手' }));
    await screen.findByTestId('chat-page-stub');

    fireEvent.click(screen.getByRole('button', { name: '在问股整页中打开' }));

    expect(screen.getByTestId('location')).toHaveTextContent('/chat');
    await waitFor(() => {
      expect(screen.queryByTestId('chat-page-stub')).not.toBeInTheDocument();
    });
  });

  it('closes itself when navigation lands on the full chat route', async () => {
    renderAt('/portfolio');

    fireEvent.click(screen.getByRole('button', { name: '打开站内助手' }));
    await screen.findByTestId('chat-page-stub');

    fireEvent.click(screen.getByRole('button', { name: '在问股整页中打开' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '打开站内助手' })).not.toBeInTheDocument();
    });
  });
});
