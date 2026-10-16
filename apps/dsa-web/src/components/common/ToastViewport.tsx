import type React from 'react';
import { cn } from '../../utils/cn';

interface ToastViewportProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * 提示列的定位层取 `z-[110]`：要压在抽屉之上 —— 报告抽屉是 100、首页的运行流抽屉是 80，
 * 抽屉整块盖住右半屏，提示在它们下面等于「store 里在屏上，用户既看不见也点不到」；
 * 但仍留在气泡提示（120）与模态对话框（140）之下：那两者是跟着用户当前操作冒出来的，
 * 该盖住提示，这也是改前的位置关系（提示 50 < 气泡 120）。
 *
 * 注意只有挂在根层级的元素才按这两个数字比大小：页面头部那些 `z-[120]` 的策略菜单写在
 * `relative z-30` 的 header 里，整个子树被压到 30，本来就到不了提示这一层，不用为它调整。
 *
 * 位置仍是右上角（全站统一的锚点）：提示会短暂盖住页面顶部的操作区，这是右上角锚点
 * 在工具栏密集页面上的固有代价 —— 换成右下角会改盖问股页的输入区、设置页底部的保存行。
 */
export const ToastViewport: React.FC<ToastViewportProps> = ({ children, className = '' }) => {
  return (
    <div className={cn('pointer-events-none fixed right-5 top-5 z-[110] flex w-[360px] max-w-[calc(100vw-24px)] flex-col gap-3', className)}>
      {children}
    </div>
  );
};
