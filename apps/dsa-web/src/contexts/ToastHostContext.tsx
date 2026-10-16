import type React from 'react';
import { ToastCard } from '../components/common/ToastCard';
import { ToastViewport } from '../components/common/ToastViewport';
import { Toast, useToasts } from '../components/common/toastStore';

/**
 * 右上角的提示列。渲染的是 store 里当前在屏的那些条目，不接收 children。
 *
 * 提示身份由 store 里的 `id` 决定，`Toast.dismiss` 负责摘条目并回调它自己的 `onDismiss`，
 * 所以这里只做「读 + 渲染 + 转发关闭请求」三件事。
 *
 * 容器本身是无 role 的普通 div —— 提示自己带 `role="alert"`，容器再带一个会让
 * `getByRole('alert')` 这类单数查询变成「命中多个元素」。
 */
const ToastHost: React.FC = () => {
  const entries = useToasts();

  return (
    <ToastViewport>
      {/* display:contents 让卡片直接参与外层 flex 列的 gap 排版，
          否则它们会变成这个中间节点的子元素，gap 只作用在一个元素上。 */}
      <div className="contents" data-testid="toast-host">
        {entries.map((entry) => (
          <ToastCard key={entry.id} spec={entry} onDismiss={() => Toast.dismiss(entry.id)} />
        ))}
      </div>
    </ToastViewport>
  );
};

/**
 * 全站唯一的提示宿主，挂在 `App` 根部。
 *
 * 页面不渲染浮层，只调 `Toast.*` / `useToastSync`（见 `components/common/toastStore`），
 * 于是路由切换时提示不会随页面卸载、多个来源的提示共用同一列堆叠顺序，也不需要每个页面
 * 再搭一遍定位样式。
 *
 * 这个宿主必须挂在**所有**会出提示的页面之外，登录页与加载兜底也在内：store 是全局的，
 * 宿主不在就意味着提示被静默丢弃（`App.test.tsx` 里有一条断言守着这件事）。
 */
export const ToastHostProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <>
    {children}
    <ToastHost />
  </>
);
