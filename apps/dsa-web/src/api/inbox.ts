import apiClient from './index';
import { toCamelCase } from './utils';

export type InboxType = 'daily_brief' | 'event' | 'weekly' | 'opportunity';

export interface InboxItem {
  id: number;
  sessionId: string;
  type: InboxType;
  typeLabel: string;
  title: string;
  preview: string;
  content: string;
  stockCode: string | null;
  createdAt: string | null;
  read: boolean;
  readAt: string | null;
}

export interface InboxResult {
  items: InboxItem[];
  unreadCount: number;
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export interface InboxQuery {
  type?: InboxType;
  unreadOnly?: boolean;
  page?: number;
  pageSize?: number;
  /** Fetch a single message, used by notification deep links. */
  id?: number;
}

export const INBOX_UPDATED_EVENT = 'dsa-inbox-updated';
/** 由侧栏「提醒」入口触发，打开铃铛的「全部消息」抽屉。 */
export const INBOX_OPEN_ALL_EVENT = 'dsa-inbox-open-all';

function notifyInboxUpdated() {
  try {
    window.dispatchEvent(new Event(INBOX_UPDATED_EVENT));
  } catch {
    // window is unavailable in non-browser contexts
  }
}

export const inboxApi = {
  async list(query: InboxQuery = {}): Promise<InboxResult> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/inbox/messages', {
      params: {
        type: query.type,
        unread_only: query.unreadOnly,
        page: query.page ?? 1,
        page_size: query.pageSize ?? 50,
        id: query.id,
      },
    });
    return toCamelCase<InboxResult>(response.data);
  },

  async markRead(messageIds: number[]): Promise<number> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/inbox/read', {
      message_ids: messageIds,
    });
    notifyInboxUpdated();
    return Number(response.data?.marked ?? 0);
  },

  async markUnread(messageIds: number[]): Promise<number> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/inbox/unread', {
      message_ids: messageIds,
    });
    notifyInboxUpdated();
    return Number(response.data?.marked ?? 0);
  },

  async markAllRead(): Promise<number> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/inbox/read-all');
    notifyInboxUpdated();
    return Number(response.data?.marked ?? 0);
  },
};
