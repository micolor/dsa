import type React from 'react';
import type { ParsedApiError } from '../../api/error';
import { Button } from './Button';
import { InlineAlert } from './InlineAlert';

interface ApiErrorAlertProps {
  error: ParsedApiError;
  className?: string;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss?: () => void;
}

/**
 * 页内错误提示：把 `ParsedApiError` 摊平成 `InlineAlert` 的 danger 档。
 *
 * 曾经它有自己的外形（12px 圆角、带框的「关闭」胶囊按钮、自己拼的一套颜色），
 * 与 `InlineAlert` 的 danger 档各写一份，同一页里两种错误提示长得不一样。现在它只是
 * 一个适配器：外形、关闭按钮、可展开原文全部由 `InlineAlert` / `AlertBody` 决定。
 *
 * `dismissLabel` 与 `elevated` 两个 prop 随之删除：前者带 `onDismiss` 才是活的，而全仓
 * 唯一传它的 `ReportNews` 没传 `onDismiss`（那个按钮从来没渲染过）；后者是浮层提示的开关，
 * 浮层一律走 `Toast`。需要浮起来的错误请用 `<Toast error={err} />`。
 */
export const ApiErrorAlert: React.FC<ApiErrorAlertProps> = ({
  error,
  className = '',
  actionLabel,
  onAction,
  onDismiss,
}) => {
  const rawMessage = error.rawMessage.trim();
  const showDetails = rawMessage && rawMessage !== error.message.trim();

  return (
    <InlineAlert
      variant="danger"
      title={error.title}
      message={error.message}
      details={showDetails ? error.rawMessage : undefined}
      onDismiss={onDismiss}
      action={actionLabel && onAction ? (
        <Button type="button" variant="secondary" size="xsm" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : undefined}
      className={className}
    />
  );
};
