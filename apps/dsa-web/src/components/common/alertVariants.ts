/**
 * 全站提示的四个语义档位，颜色只有这一处真源，三个出口各取自己需要的部分：
 * 页内提示 = 描边 + 淡底 + 前景色，浮层提示（`Toast`）= 描边 + 前景色（底色由玻璃材质给）。
 *
 * 独立成文件而不是和 `InlineAlert` 放一起：那边只导出组件，否则 React Fast Refresh
 * 会失效（`react-refresh/only-export-components`）。
 */
export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

export const alertVariantText: Record<AlertVariant, string> = {
  info: 'text-cyan',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-[hsl(var(--color-danger-alert-text))]',
};

export const alertVariantBorder: Record<AlertVariant, string> = {
  info: 'border-cyan/20',
  success: 'border-success/20',
  warning: 'border-warning/20',
  danger: 'border-[hsl(var(--color-danger-alert-border)/0.3)]',
};

export const alertVariantTint: Record<AlertVariant, string> = {
  info: 'bg-cyan/10',
  success: 'bg-success/10',
  warning: 'bg-warning/10',
  danger: 'bg-[hsl(var(--color-danger-alert-bg)/0.1)]',
};
