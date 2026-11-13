import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ProactiveInboxBell } from '../ProactiveInboxBell';

const { listInbox, markRead, markAllRead, markUnread } = vi.hoisted(() => ({
  listInbox: vi.fn(),
  markRead: vi.fn().mockResolvedValue(1),
  markAllRead: vi.fn().mockResolvedValue(1),
  markUnread: vi.fn().mockResolvedValue(1),
}));

vi.mock('../../../api/inbox', () => ({
  INBOX_UPDATED_EVENT: 'dsa-inbox-updated',
  INBOX_OPEN_ALL_EVENT: 'dsa-inbox-open-all',
  inboxApi: {
    list: listInbox,
    markRead,
    markAllRead,
    markUnread,
  },
}));

vi.mock('../../../api/systemConfig', () => ({
  systemConfigApi: { addToWatchlist: vi.fn().mockResolvedValue([]) },
}));

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    sessionId: '',
    type: 'event',
    typeLabel: '事件',
    title: '长青科技（001324）跌破 MA20',
    preview: '现价 18.51 < MA20 18.76。',
    content: '## 长青科技（001324）跌破 MA20\n\n现价 18.51 < MA20 18.76。',
    stockCode: '001324',
    createdAt: '2026-11-05T14:32:00+08:00',
    read: false,
    readAt: null,
    ...overrides,
  };
}

describe('ProactiveInboxBell', () => {
  beforeEach(() => {
    listInbox.mockReset();
    markRead.mockReset().mockResolvedValue(1);
    markAllRead.mockReset().mockResolvedValue(1);
    markUnread.mockReset().mockResolvedValue(1);
    listInbox.mockResolvedValue({
      items: [item()],
      unreadCount: 1,
      total: 1,
      page: 1,
      pageSize: 20,
      hasMore: false,
    });
  });

  it('shows the unread badge and opens the inbox panel', async () => {
    render(
      <MemoryRouter>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('inbox-unread-badge')).toHaveTextContent('1');
    fireEvent.click(screen.getByRole('button', { name: '主动提醒' }));
    expect(await screen.findByText('长青科技（001324）跌破 MA20')).toBeInTheDocument();
    expect(listInbox).toHaveBeenCalledWith({ unreadOnly: true, pageSize: 8 });
  });

  it('shows only unread messages in the dropdown and can open the full list', async () => {
    listInbox.mockImplementation((query: { unreadOnly?: boolean; id?: number } = {}) => Promise.resolve({
      items: query.unreadOnly === true ? [item()] : [item()],
      unreadCount: 1,
      total: 1,
      page: 1,
      pageSize: query.unreadOnly ? 8 : 20,
      hasMore: false,
    }));

    render(
      <MemoryRouter>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '主动提醒' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看全部消息' }));

    expect(await screen.findByText('全部主动提醒')).toBeInTheDocument();
    expect(listInbox).toHaveBeenCalledWith({ type: undefined, page: 1, pageSize: 20 });

    // 全屏列表点击消息仍以半屏详情抽屉打开。
    fireEvent.click(screen.getByText('长青科技（001324）跌破 MA20'));
    expect(await screen.findByTestId('inbox-detail-markdown')).toHaveTextContent('MA20');
  });

  it('marks an item read when opened and renders the detail', async () => {
    render(
      <MemoryRouter>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '主动提醒' }));
    fireEvent.click(await screen.findByText('长青科技（001324）跌破 MA20'));

    await waitFor(() => expect(markRead).toHaveBeenCalledWith([1]));
    expect(await screen.findByTestId('inbox-detail-markdown')).toHaveTextContent('MA20');
  });

  it('opens the matching message from a notification deep link', async () => {
    listInbox.mockImplementation((query: { id?: number } = {}) => Promise.resolve({
      items: query.id === 1 ? [item()] : [item()],
      unreadCount: 1,
      total: 1,
      page: 1,
      pageSize: 20,
      hasMore: false,
    }));

    render(
      <MemoryRouter initialEntries={['/?inbox=1']}>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('inbox-detail-markdown')).toHaveTextContent('MA20');
    expect(listInbox).toHaveBeenCalledWith({ id: 1, pageSize: 1 });
    await waitFor(() => expect(markRead).toHaveBeenCalledWith([1]));
  });

  it('opens the all-messages drawer from the sidebar nav event', async () => {
    render(
      <MemoryRouter>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    await screen.findByTestId('inbox-unread-badge');
    fireEvent(window, new Event('dsa-inbox-open-all'));

    expect(await screen.findByText('长青科技（001324）跌破 MA20')).toBeInTheDocument();
    expect(listInbox).toHaveBeenCalledWith({ type: undefined, page: 1, pageSize: 20 });
  });

  it('marks all items read from the panel header', async () => {
    render(
      <MemoryRouter>
        <ProactiveInboxBell />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '主动提醒' }));
    fireEvent.click(await screen.findByRole('button', { name: '全部已读' }));

    await waitFor(() => expect(markAllRead).toHaveBeenCalledTimes(1));
  });
});
