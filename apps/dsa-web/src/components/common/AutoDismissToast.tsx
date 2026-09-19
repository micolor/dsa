import { useEffect, useRef, useState, type ReactNode } from 'react';

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
 * 自动消失的提示，鼠标悬停其上时暂停计时。
 *
 * 既用于右上角的全局 toast，也用于卡片内就地显示的结果提示（例如修改密码成功），
 * 两者共享同一套计时语义。悬停暂停、移开后**重新计时**（而不是接着走剩余时间）：
 * 看提示的人因此总有完整一段时间读完，行为也更好预测。计时器由本组件持有，调用方
 * 不再自己 setTimeout。
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
  // 回调放进 ref：调用方常传内联箭头函数，把它列进下面的依赖会让每次重渲染都重置倒计时。
  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (!active || isHovered) {
      return undefined;
    }
    const timer = window.setTimeout(() => onDismissRef.current(), delayMs);
    return () => window.clearTimeout(timer);
  }, [active, delayMs, isHovered]);

  return (
    <div
      className="pointer-events-auto"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {children}
    </div>
  );
};
