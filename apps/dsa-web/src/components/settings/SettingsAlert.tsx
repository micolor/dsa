import type React from 'react';
import { Button, InlineAlert } from '../common';

interface SettingsAlertProps {
  title: string;
  message: string;
  variant?: 'error' | 'success' | 'warning';
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}

const variantMap: Record<NonNullable<SettingsAlertProps['variant']>, 'danger' | 'success' | 'warning'> = {
  error: 'danger',
  success: 'success',
  warning: 'warning',
};

/**
 * 设置模块的页内提示：只做 `error/success/warning` → `danger/success/warning` 的词汇映射，
 * 外形全部交给 `InlineAlert`。
 *
 * `presentation="toast"` 已删除。它当时在 `InlineAlert` 之上又叠了一层
 * `glass-surface-floating` + 一条青紫渐变高光带，但语义色的淡底与 `shadow-soft-card`
 * 都是 utility、层级高于 `.glass-surface-floating` 所在的 components 层，于是那层浮层材质
 * 被自己的淡底和页内投影盖掉 —— 设置页的 toast 因此比别处矮一截、也没浮起来。浮层提示现在
 * 一律用 `Toast`，这个组件只留在正文流里。
 */
export const SettingsAlert: React.FC<SettingsAlertProps> = ({
  title,
  message,
  variant = 'error',
  actionLabel,
  onAction,
  className = '',
}) => {
  return (
    <InlineAlert
      title={title}
      message={message}
      variant={variantMap[variant]}
      className={className}
      action={actionLabel && onAction ? (
        <Button
          type="button"
          variant="secondary"
          size="xsm"
          onClick={onAction}
        >
          {actionLabel}
        </Button>
      ) : undefined}
    />
  );
};
