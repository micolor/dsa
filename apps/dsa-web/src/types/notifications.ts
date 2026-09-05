/** 单条通知投递回执（camelCase，由 api/notifications 的 snake->camel mapper 构建）。 */
export interface NotificationDeliveryItem {
  id: number;
  routeType: string;
  channel: string;
  attempt: number;
  success: boolean;
  errorCode: string | null;
  retryable: boolean;
  latencyMs: number | null;
  diagnostics: string | null;
  createdAt: string;
}

export interface NotificationDeliveryListResponse {
  items: NotificationDeliveryItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface NotificationDeliveryListQuery {
  routeType?: string;
  channel?: string;
  success?: boolean;
  page?: number;
  pageSize?: number;
}
