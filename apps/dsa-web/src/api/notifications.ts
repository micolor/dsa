import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  NotificationDeliveryItem,
  NotificationDeliveryListQuery,
  NotificationDeliveryListResponse,
} from '../types/notifications';

function toDeliveryListParams(query: NotificationDeliveryListQuery = {}): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {};
  if (query.routeType) params.route_type = query.routeType;
  if (query.channel) params.channel = query.channel;
  if (query.success !== undefined) params.success = query.success;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  return params;
}

function mapDeliveryItem(data: Record<string, unknown>): NotificationDeliveryItem {
  // 复用既有 snake->camel 工具：route_type->routeType / latency_ms->latencyMs / created_at->createdAt / error_code->errorCode。
  const item = toCamelCase<NotificationDeliveryItem>(data);
  item.id = Number(item.id ?? 0);
  item.routeType = String(item.routeType ?? 'default');
  item.channel = String(item.channel ?? '');
  item.attempt = Number(item.attempt ?? 1);
  item.success = Boolean(item.success);
  item.errorCode = (item.errorCode ?? null) as string | null;
  item.retryable = Boolean(item.retryable);
  item.latencyMs = item.latencyMs == null ? null : Number(item.latencyMs);
  item.diagnostics = item.diagnostics == null ? null : String(item.diagnostics);
  item.createdAt = String(item.createdAt ?? '');
  return item;
}

function mapDeliveryListResponse(data: Record<string, unknown>): NotificationDeliveryListResponse {
  const rawItems = Array.isArray(data.items) ? data.items : [];
  const items = rawItems.map((it) => mapDeliveryItem((it ?? {}) as Record<string, unknown>));
  return {
    items,
    total: Number(data.total ?? 0),
    page: Number(data.page ?? 1),
    pageSize: Number(data.page_size ?? data.pageSize ?? 20),
  };
}

export const notificationsApi = {
  async getNotificationDeliveries(query: NotificationDeliveryListQuery = {}): Promise<NotificationDeliveryListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/notifications/deliveries', {
      params: toDeliveryListParams(query),
    });
    return mapDeliveryListResponse(response.data);
  },
};
