import type React from 'react';
import { createContext, useContext, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { ToastViewport } from '../components/common/ToastViewport';

type ToastHostContextValue = {
  /** 右上角 toast 容器的 DOM 节点；挂载完成前为 null。 */
  host: HTMLElement | null;
};

const ToastHostContext = createContext<ToastHostContextValue | null>(null);

/**
 * 全站唯一的右上角提示容器。
 *
 * 页面级提示不再各自在页内渲染，而是通过 ``ToastPortal`` 送到这里，于是：
 * 路由切换时提示不会随页面卸载、多个页面的提示会共用同一列堆叠顺序、
 * 也不需要每个页面再搭一遍定位样式。
 *
 * 容器本身的 ``ToastViewport`` 是无 role 的普通 div —— 提示自身带 ``role="alert"``，
 * 容器再带一个会让 ``getByRole('alert')`` 这类单数查询变成"命中多个元素"。
 */
export const ToastHostProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [host, setHost] = useState<HTMLElement | null>(null);

  const value = useMemo<ToastHostContextValue>(() => ({ host }), [host]);

  return (
    <ToastHostContext.Provider value={value}>
      {children}
      <ToastViewport>
        {/* display:contents 让被 portal 进来的提示直接参与外层 flex 列的 gap 排版，
            否则它们会变成这个中间节点的子元素，gap 只作用在一个元素上。 */}
        <div ref={setHost} className="contents" data-testid="toast-host" />
      </ToastViewport>
    </ToastHostContext.Provider>
  );
};

/**
 * 把页面级提示送到右上角容器。
 *
 * ``ToastHostProvider`` 渲染出的框架里必然有容器，正常路径下提示都在右上角。没有
 * Provider 时就地渲染（见下），这是为了让裸渲染单个页面的既有测试保持有效 ——
 * 与 ``useUiLanguage`` 不抛错、退化成中文默认值是同一套约定。
 *
 * 需要自动消失的调用方自己包 ``AutoDismissToast``；不包就是持续显示，由调用方
 * 自己的状态控制何时撤下。撤销窗口这类"到点执行真实副作用"的提示不要包，见
 * ``AutoDismissToast`` 的说明。
 */
export const ToastPortal: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const context = useContext(ToastHostContext);
  // 没有 Provider：就地渲染。这是测试环境与独立渲染组件的兜底路径。
  if (context === null) {
    return <>{children}</>;
  }
  // Provider 已渲染但容器节点还没 attach（仅首帧）：这一帧什么都不渲染，
  // 免得提示先在原位闪一下再跳到右上角。
  if (context.host === null) {
    return null;
  }
  return createPortal(children, context.host);
};

// eslint-disable-next-line react-refresh/only-export-components -- useToastHost is a hook, co-located for context access
export function useToastHost(): ToastHostContextValue | null {
  return useContext(ToastHostContext);
}
