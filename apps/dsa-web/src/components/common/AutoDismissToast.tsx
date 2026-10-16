import { useEffect, useRef, useState, type FocusEvent, type ReactNode } from 'react';

interface AutoDismissToastProps {
  /**
   * 提示当前的状态对象：真值表示可见，falsy 表示已关闭。
   *
   * 传状态本身而不是 `Boolean(状态)`，是为了保留原有的「换了一条新提示就重新计时」
   * 语义——值（引用或内容）变化会重启倒计时。
   */
  active: unknown;
  onDismiss: () => void;
  delayMs: number;
  children: ReactNode;
}

/**
 * 自动消失的提示，鼠标悬停或键盘焦点停留在其上时暂停计时。
 *
 * 既用于右上角的全局 toast，也用于卡片内就地显示的结果提示（例如修改密码成功），
 * 两者共享同一套计时语义。暂停后**重新计时**（而不是接着走剩余时间）：
 * 看提示的人因此总有完整一段时间读完，行为也更好预测。计时器由本组件持有，调用方
 * 不再自己 setTimeout。
 *
 * 键盘焦点同样要暂停：提示里有「关闭」按钮，错误提示还可能有操作按钮与 `details`
 * 折叠区，只暂停悬停的话，用键盘 Tab 进来的人在读或按的时候提示会自己消失
 * （WCAG 2.2.1 的可调时限要求能停住这类计时）。
 *
 * 注意：撤销窗口这类「到点就执行动作」的倒计时不要用它——那类计时器的到期动作是
 * 删除等真实副作用，文案也向用户承诺了固定窗口，暂停会改变动作发生的时间。
 */
export const AutoDismissToast: React.FC<AutoDismissToastProps> = ({
  active,
  onDismiss,
  delayMs,
  children,
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  // 回调放进 ref：调用方常传内联箭头函数，把它列进下面的依赖会让每次重渲染都重置倒计时。
  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    // React 的 onBlur 由 focusout 实现，焦点在子元素之间移动时也会触发；
    // 焦点仍落在提示内部就不算离开，否则在「关闭」和操作按钮之间 Tab 一下就会重新计时。
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setIsFocused(false);
    }
  };

  useEffect(() => {
    if (!active || isHovered || isFocused) {
      return undefined;
    }
    const timer = window.setTimeout(() => onDismissRef.current(), delayMs);
    return () => window.clearTimeout(timer);
  }, [active, delayMs, isFocused, isHovered]);

  return (
    <div
      className="pointer-events-auto"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setIsFocused(true)}
      onBlur={handleBlur}
    >
      {children}
    </div>
  );
};
