import type React from 'react';
import { cn } from '../../utils/cn';

type InlineAlertVariant = 'info' | 'success' | 'warning' | 'danger';

interface InlineAlertProps {
  title?: string;
  message: React.ReactNode;
  variant?: InlineAlertVariant;
  action?: React.ReactNode;
  /** Render on a solid elevated surface instead of a translucent color tint. Used for floating toasts over content. */
  elevated?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const variantStyles: Record<InlineAlertVariant, string> = {
  info: 'border-cyan/20 bg-cyan/10 text-cyan',
  success: 'border-success/20 bg-success/10 text-success',
  warning: 'border-warning/20 bg-warning/10 text-warning',
  danger: 'border-[hsl(var(--color-danger-alert-border)/0.3)] bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]',
};

const elevatedVariantStyles: Record<InlineAlertVariant, string> = {
  info: 'text-cyan',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-[hsl(var(--color-danger-alert-text))]',
};

/**
 * iPhone「液态玻璃」表面：中性磨砂玻璃 + 强背模糊/饱和度提升 + 顶部
 * specular 高光 + 表面顶部渐变反光 + 深柔投影。状态由彩色文字表达。
 */
const liquidGlassSurface =
  'relative overflow-hidden border border-white/15 bg-elevated/70 backdrop-blur-2xl backdrop-saturate-150 '
  + 'shadow-[0_12px_40px_rgba(3,8,20,0.18),inset_0_1px_0_rgba(255,255,255,0.14)] '
  + 'before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-px '
  + 'before:bg-gradient-to-r before:from-transparent before:via-white/60 before:to-transparent before:content-[""] '
  + 'after:pointer-events-none after:absolute after:inset-x-0 after:top-0 after:h-[48%] '
  + 'after:bg-gradient-to-b after:from-white/10 after:to-transparent after:content-[""]';

export const InlineAlert: React.FC<InlineAlertProps> = ({
  title,
  message,
  variant = 'info',
  action,
  elevated = false,
  className = '',
  style,
}) => {
  return (
    <div
      role="alert"
      style={style}
      className={cn(
        'max-w-full overflow-hidden rounded-2xl border px-4 py-3',
        elevated
          ? cn(elevatedVariantStyles[variant], liquidGlassSurface)
          : cn(variantStyles[variant], 'shadow-soft-card'),
        className,
      )}
    >
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          {title ? <p className="text-sm font-semibold">{title}</p> : null}
          <div className={cn('text-sm break-words [overflow-wrap:anywhere]', title ? 'mt-1 opacity-90' : 'opacity-90')}>
            {message}
          </div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
};
