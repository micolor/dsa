import type React from 'react';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { alertVariantBorder, alertVariantTint, alertVariantText, type AlertVariant } from './alertVariants';

/** 关闭按钮的外形只有这一份实现：页内提示与浮层提示共用，不许调用方再自己塞一个 ✕ 进 `action`。 */
export const AlertCloseButton: React.FC<{ onDismiss: () => void; label: string }> = ({ onDismiss, label }) => (
  <button
    type="button"
    onClick={onDismiss}
    aria-label={label}
    className="shrink-0 rounded-lg p-1 opacity-70 transition-opacity hover:opacity-100"
  >
    <X className="h-4 w-4" aria-hidden="true" />
  </button>
);

export interface AlertBodyProps {
  title?: string;
  message: React.ReactNode;
  /** 右侧附加操作（按钮等）与关闭按钮同列。 */
  action?: React.ReactNode;
  onDismiss?: () => void;
  /** 可展开的技术细节（例如错误原文）。 */
  details?: string;
}

/**
 * 提示的正文排版：标题 / 正文 / 可展开细节 / 右侧操作列。
 *
 * 页内提示与浮层提示的差别只有「底色与描边」，正文排版完全共用，避免两套出口
 * 各自漂移出不同的字号、间距和关闭按钮。
 */
export const AlertBody: React.FC<AlertBodyProps> = ({
  title,
  message,
  action,
  onDismiss,
  details,
}) => {
  const { t } = useUiLanguage();

  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0">
        {title ? <p className="text-sm font-semibold">{title}</p> : null}
        <div className={cn('text-sm break-words [overflow-wrap:anywhere]', title ? 'mt-1' : '')}>{message}</div>
        {details ? (
          <details className="mt-3 rounded-lg border border-subtle bg-surface-2 px-3 py-2">
            <summary className="cursor-pointer text-xs opacity-90">{t('common.details')}</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-5 opacity-85">{details}</pre>
          </details>
        ) : null}
      </div>
      {action || onDismiss ? (
        <div className="flex shrink-0 items-start gap-2">
          {action}
          {onDismiss ? <AlertCloseButton onDismiss={onDismiss} label={t('common.close')} /> : null}
        </div>
      ) : null}
    </div>
  );
};

interface InlineAlertProps extends AlertBodyProps {
  variant?: AlertVariant;
  /** Live-region role. `status` is the polite variant for informational state changes; errors keep the default `alert`. */
  role?: 'alert' | 'status';
  className?: string;
  style?: React.CSSProperties;
}

/**
 * 页内提示：排在正文流里的一条淡底提示。
 *
 * 只负责「在内容里」这一种形态。浮在内容之上的提示一律用 `Toast`（玻璃材质 + 浮层投影），
 * 这里不再提供任何能把页内提示伪装成浮层的开关 —— 那正是先前问股页提示发透明的原因。
 */
export const InlineAlert: React.FC<InlineAlertProps> = ({
  title,
  message,
  variant = 'info',
  action,
  onDismiss,
  details,
  role = 'alert',
  className = '',
  style,
}) => {
  return (
    <div
      role={role}
      style={style}
      className={cn(
        'max-w-full overflow-hidden rounded-2xl border px-4 py-3 shadow-soft-card',
        alertVariantBorder[variant],
        alertVariantTint[variant],
        alertVariantText[variant],
        className,
      )}
    >
      <AlertBody title={title} message={message} action={action} onDismiss={onDismiss} details={details} />
    </div>
  );
};
