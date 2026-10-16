import type React from 'react';
import { cn } from '../../utils/cn';
import { Button } from './Button';
import { AutoDismissToast } from './AutoDismissToast';
import { AlertBody } from './InlineAlert';
import { alertVariantBorder, alertVariantText, type AlertVariant } from './alertVariants';
import type { ToastSpec } from './toastStore';

export interface ToastCardProps {
  /** 内容描述。 */
  spec: ToastSpec;
  /**
   * 请求撤下这条提示（点关闭按钮或自动到点）。
   *
   * 注意这是「请求」，不是 `spec.onDismiss`：谁来摘条目由 store 决定，`spec.onDismiss`
   * 是摘掉之后再通知调用方的回调。两者分工写在这里，免得调用方以为传了 `spec.onDismiss`
   * 就能自己控制显隐。
   */
  onDismiss: () => void;
}

/**
 * 右上角浮层提示的**唯一**外形：材质、形状、内边距、关闭按钮、自动消失语义都在这里决定。
 *
 * 这个组件只由 `ToastHost` 渲染，业务代码不要直接用——要弹提示就调 `Toast.error(...)`
 * 这类入口（见 `toastStore`），否则就绕过了统一的外形与自动消失语义，这正是此前同一列里
 * 出现 16px / 12px 两种圆角、浮层投影与页内投影两种阴影、以及问股页漏了浮层材质的原因。
 *
 * 与页内 `InlineAlert` 的分工：**浮起来的（压住内容、需要被注意到）用浮层，
 * 排在正文流里的用 `InlineAlert`**。两者共用 `AlertBody` 的排版与同一套语义色，
 * 差别只剩玻璃浮层 vs 淡底。
 */
export const ToastCard: React.FC<ToastCardProps> = ({ spec, onDismiss }) => {
  const resolvedVariant: AlertVariant = spec.variant ?? (spec.error ? 'danger' : 'info');
  const resolvedTitle = spec.title ?? spec.error?.title;
  const resolvedMessage = spec.message ?? spec.error?.message ?? '';
  const rawMessage = spec.error?.rawMessage?.trim() ?? '';
  // 与正文完全相同的原文不再折叠展示，否则会出现两遍一字不差的文字。
  const details = rawMessage && rawMessage !== (spec.error?.message ?? '').trim() ? rawMessage : undefined;

  const surface = (
    <div
      role={spec.role ?? 'alert'}
      className={cn(
        'glass-surface-floating pointer-events-auto max-w-full overflow-hidden rounded-2xl border px-4 py-3',
        alertVariantBorder[resolvedVariant],
        alertVariantText[resolvedVariant],
      )}
    >
      <AlertBody
        title={resolvedTitle}
        message={resolvedMessage}
        onDismiss={onDismiss}
        details={details}
        action={spec.action ?? (spec.actionLabel && spec.onAction ? (
          <Button type="button" variant="secondary" size="xsm" onClick={spec.onAction}>
            {spec.actionLabel}
          </Button>
        ) : undefined)}
      />
    </div>
  );

  if (!spec.autoDismissMs) {
    return surface;
  }

  return (
    <AutoDismissToast
      // 传条目本身：内容被 upsert 成新的（引用变了）就重新计时，正是 `AutoDismissToast` 的语义。
      active={spec}
      onDismiss={onDismiss}
      delayMs={spec.autoDismissMs}
    >
      {surface}
    </AutoDismissToast>
  );
};
