import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';

const mockLogout = vi.fn().mockResolvedValue(undefined);
const { mockInboxList } = vi.hoisted(() => ({
  mockInboxList: vi.fn().mockResolvedValue({ items: [], unreadCount: 0, total: 0, page: 1, pageSize: 1, hasMore: false }),
}));

const completionBadgeState = { value: true };

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: true,
    logout: mockLogout,
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

vi.mock('../../../api/inbox', () => ({
  INBOX_OPEN_ALL_EVENT: 'dsa-inbox-open-all',
  INBOX_UPDATED_EVENT: 'dsa-inbox-updated',
  inboxApi: {
    list: (...args: unknown[]) => mockInboxList(...args),
  },
}));

describe('SidebarNav', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockInboxList.mockClear();
    mockInboxList.mockResolvedValue({
      items: [],
      unreadCount: 0,
      total: 0,
      page: 1,
      pageSize: 1,
      hasMore: false,
    });
  });

  it('renders a single research entry for screening, signals and backtest', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '研究' })).toHaveAttribute('href', '/research');
    expect(screen.queryByRole('link', { name: '选股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'AI 建议' })).not.toBeInTheDocument();
  });

  it('groups navigation entries into sections', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByText('工作台')).toBeInTheDocument();
    expect(screen.getAllByText('研究').length).toBeGreaterThan(0);
    expect(screen.getByText('交易')).toBeInTheDocument();
    expect(screen.getByText('自动化')).toBeInTheDocument();
    expect(screen.getByText('系统')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '研究' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '模拟盘' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提醒' })).toBeInTheDocument();
  });

  it('shows the unread count on the reminders entry', async () => {
    mockInboxList.mockResolvedValueOnce({
      items: [],
      unreadCount: 3,
      total: 3,
      page: 1,
      pageSize: 1,
      hasMore: false,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const reminders = screen.getByRole('button', { name: '提醒' });
    expect(await within(reminders).findByText('3')).toBeInTheDocument();
  });

  it('shows the shared completion badge only when chat completion is pending', () => {
    completionBadgeState.value = true;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('chat-completion-badge')).toBeInTheDocument();
    expect(screen.getByLabelText('问股有新消息')).toBeInTheDocument();

    completionBadgeState.value = false;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
  });

  it('renders the alerts navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/alerts']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const alertsLink = screen.getByRole('link', { name: '告警' });
    expect(alertsLink).toHaveAttribute('href', '/alerts');
    expect(alertsLink).toHaveClass('font-medium');
  });

  it('marks the research entry active on research sub-routes', () => {
    render(
      <MemoryRouter initialEntries={['/research/signals']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const researchLink = screen.getByRole('link', { name: '研究' });
    expect(researchLink).toHaveAttribute('href', '/research');
    expect(researchLink).toHaveClass('font-medium');
  });

  it('opens the logout confirmation and confirms logout', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));

    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));
    expect(mockLogout).toHaveBeenCalled();
  });
});
