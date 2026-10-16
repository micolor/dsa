import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import type { ParsedApiError } from '../../api/error';
import type { AlertVariant } from './alertVariants';

/**
 * 一条浮层提示的内容描述，不含身份。身份由调用方决定：见 `ToastSpec.id` / `useToastSync`。
 */
export interface ToastSpec {
  /** 错误走这个入口：标题 / 正文 / 可展开原文都从 `ParsedApiError` 取，`title` 可覆盖标题。 */
  error?: ParsedApiError;
  title?: string;
  message?: ReactNode;
  /** 缺省：带 `error` 为 `danger`，否则为 `info`。 */
  variant?: AlertVariant;
  /** 传了才自动消失（毫秒）。鼠标悬停或键盘焦点停留在其上时暂停计时，离开后重新计时。 */
  autoDismissMs?: number;
  /** 右侧附加操作，与关闭按钮同列。 */
  action?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
  /** 用户关掉这条提示时回调（点关闭按钮或自动消失）。通常用来清掉驱动它的那份状态。 */
  onDismiss?: () => void;
  role?: 'alert' | 'status';
}

/** 带身份的提示：`id` 相同的 `show` 是覆盖而不是追加。 */
export interface ToastEntry extends ToastSpec {
  id: string;
}

/** 同时最多挂几条。超出时挤掉最早的一条，避免事件密集时把屏幕铺满。 */
const MAX_VISIBLE = 5;

let entries: ToastEntry[] = [];
let seq = 0;
const listeners = new Set<() => void>();

function subscribeToast(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 快照必须是稳定引用，否则 `useSyncExternalStore` 会每次渲染都重订阅。 */
function getToastSnapshot(): ToastEntry[] {
  return entries;
}

/** 订阅当前在屏的提示，供 `ToastHost` 渲染。宿主之外不需要直接用。 */
export function useToasts(): ToastEntry[] {
  return useSyncExternalStore(subscribeToast, getToastSnapshot);
}

function commit(next: ToastEntry[]): void {
  entries = next;
  for (const listener of listeners) {
    listener();
  }
}

/**
 * 同一个 `id` 当前有几个组件在盯着。首页窄屏下同一个 `sidebarContent` 会同时挂在两处 ——
 * 桌面侧栏那份被 CSS 隐藏（`hidden md:flex`）但 React 照常挂载，抽屉那份随开关挂载 / 卸载 ——
 * 两份 `HomeStockWorkspace` 都用 `home-batch-status` / `home-watchlist-message` 注册。
 * 卸载清理若无条件 `remove(id)`，关掉抽屉就会把另一份还在展示的同一条提示一起撤掉。
 *
 * 只给「卸载」这一路计数，是因为 `active` 那一路**目前不必**计数：两处拿的是同一份 props
 * （`HomePage.tsx` 里 `sidebarContent` 是一个 `useMemo` 节点，`watchlistMessage` / `batchStatus`
 * 都从同一处 state 传下去），所以两处的 `active` 不可能一个有一个没有。
 * 哪天同一个 `id` 被两个各有独立状态的组件用上（不再是同一份 props），
 * 那么「A 把自己那份清空会把 B 还在展示的提示一起撤掉」就要一起修：到那时
 * `active` 为空也得走 `takeOwnership` 的同一套计数，而不是无条件 `remove(id)`。
 */
const owners = new Map<string, number>();

/**
 * 认领一个 `id`，返回释放函数：只有最后一个持有者释放时才真的撤下提示。
 *
 * 计数的是「谁在盯着这个 id」而不是「在屏上有没有这条提示」，所以 `Toast.dismiss` /
 * `Toast.clear` 不参与计数：用户手动关掉一条提示之后，驱动它的状态没变，本来也不会重弹。
 */
function takeOwnership(id: string): () => void {
  owners.set(id, (owners.get(id) ?? 0) + 1);
  return () => {
    const remaining = (owners.get(id) ?? 1) - 1;
    if (remaining > 0) {
      owners.set(id, remaining);
      return;
    }
    owners.delete(id);
    remove(id);
  };
}

/** 从 store 里撤下一条，**不**触发它的 `onDismiss`。用于「驱动它的状态自己没了」这类情形。 */
function remove(id: string): void {
  if (!entries.some((entry) => entry.id === id)) {
    return;
  }
  commit(entries.filter((entry) => entry.id !== id));
}

/**
 * 显示一条提示，返回它的 `id`。
 *
 * 带 `id` 且已在屏上时是**覆盖**：内容换成新的、位置不动。这样同一条业务提示
 * （例如「保存失败」）反复触发不会堆成一列，同时「换内容就重新计时」的语义还在，
 * 因为条目的引用变了，`AutoDismissToast` 的 `active` 也就变了。
 */
function show(spec: ToastSpec & { id?: string }): string {
  const { id = `toast-${++seq}`, ...rest } = spec;
  const entry: ToastEntry = { ...rest, id };
  const index = entries.findIndex((item) => item.id === id);
  const next = index === -1
    ? [...entries, entry]
    : entries.map((item, i) => (i === index ? entry : item));
  commit(next.length > MAX_VISIBLE ? next.slice(next.length - MAX_VISIBLE) : next);
  return id;
}

/** 用户主动撤下一条提示：先摘掉，再回调它的 `onDismiss`。 */
function dismiss(id: string): void {
  const entry = entries.find((item) => item.id === id);
  if (!entry) {
    return;
  }
  remove(id);
  entry.onDismiss?.();
}

/** `Toast.error` 这类简写：传字符串就是一条纯文本提示。 */
type ToastInput = string | (ToastSpec & { id?: string });

/** `Toast.error` 还接受解析好的 API 错误，见那里的说明。 */
type ToastErrorInput = ToastInput | ParsedApiError;

function normalize(input: ToastInput, variant: AlertVariant): ToastSpec & { id?: string } {
  return typeof input === 'string' ? { variant, message: input } : { variant, ...input };
}

/**
 * `ParsedApiError` 与 `ToastSpec` 的标题、正文同名字段，光靠结构分不开，
 * 用 ApiError 专有的 `category` / `rawMessage` 认。
 */
function isParsedApiError(value: ToastSpec | ParsedApiError): value is ParsedApiError {
  return 'category' in value && 'rawMessage' in value;
}

/**
 * 全站统一的提示入口。**浮层提示只从这里出**，页面不再自己渲染浮层。
 *
 * 用法分两种，都落在这一个模块里：
 *
 * - 事件驱动（点一下弹一条）：直接调 `Toast.success('已保存')` / `Toast.error('保存失败')`。
 *   简写只给文案；要标题、附加操作、自动消失就传对象：`Toast.error({ title, message, autoDismissMs })`。
 * - 状态驱动（提示跟着某份状态在不在）：用 `useToastSync(id, active, spec)`。它不渲染任何东西，
 *   只把 `active` 有值这件事同步成一条 keyed 提示，`active` 变空或组件卸载时自动撤下——
 *   于是「状态清了提示跟着没」这条契约不用每个调用方各写一遍。
 *
 * 为什么要有这个模块：此前浮层提示有四套并行封装（`InlineAlert elevated`、
 * `ApiErrorAlert elevated`、`SettingsAlert presentation="toast"`，以及各页手写的
 * `<div className="…">`），各自决定玻璃底、圆角、投影和关闭按钮，同一列里能同时出现
 * 16px 与 12px 两种圆角、浮层投影与页内投影两种阴影；问股页的两条提示干脆漏了浮层材质，
 * 只剩 10% 淡底，在深色页面上几乎透明。现在材质与关闭/自动消失语义由 `ToastCard` 一处决定，
 * 通道由本模块一处决定，调用方只描述内容。
 *
 * 与页内 `InlineAlert` 的分工：**浮起来的（压住内容、需要被注意到）用这里，
 * 排在正文流里的用 `InlineAlert`**。两者共用 `AlertBody` 的排版与同一套语义色。
 */
export const Toast = {
  show,
  /**
   * 失败提示。三种传法：纯文案 `Toast.error('保存失败')`、带标题或附加操作的
   * `Toast.error({ title, message, autoDismissMs })`、以及解析好的接口错误
   * `Toast.error(getParsedApiError(err))` —— 最后一种走 `error` 通道，于是标题、正文、
   * 可展开的错误原文都按 `ParsedApiError` 来，不会被当成普通文案丢掉原文。
   */
  error: (input: ToastErrorInput): string => {
    if (typeof input === 'string') {
      return show({ variant: 'danger', message: input });
    }
    return show(isParsedApiError(input) ? { error: input } : { variant: 'danger', ...input });
  },
  success: (input: ToastInput): string => show(normalize(input, 'success')),
  warning: (input: ToastInput): string => show(normalize(input, 'warning')),
  info: (input: ToastInput): string => show(normalize(input, 'info')),
  /** 用户主动关掉一条（会回调它的 `onDismiss`）。 */
  dismiss,
  /** 清空全部，**不**回调各条的 `onDismiss`：这是「重置」，不是「用户关闭」。 */
  clear: (): void => commit([]),
};

/**
 * 把一份状态同步成右上角的一条提示。
 *
 * `active` 有值就 upsert 一条 `id` 提示，变空或组件卸载就撤下。传状态对象本身（而不是
 * `Boolean(状态)`）以保留「换了一条新提示就重新计时」的语义，与 `AutoDismissToast` 一致。
 *
 * `spec` 是**函数**：只在提示真的要显示时才求值，于是调用方不必为了类型安全先写一串
 * `active ? … : …` 兜底——`active` 在这里已经被收窄成非空。也因此提示不在屏上时，
 * 内部那段 `renderXxx(...)` 一次都不会白跑。
 *
 * `onDismiss` 写进 `spec`，用来在用户关掉提示时清掉驱动它的状态；不传也没关系，
 * 提示自己会消失，只是状态还在（下一次状态变化仍会重新弹出）。
 */
export function useToastSync<T>(
  id: string,
  active: T | null | undefined,
  spec: (active: T) => ToastSpec,
): void {
  // spec 十有八九是内联箭头函数，列进依赖会让每次渲染都重弹一次，所以「要不要弹」只看
  // active。最新 spec 经 ref 交给下面的同步 effect，并且只能在 effect 里写（渲染期写 ref
  // 会被 `react-hooks/refs` 拦下）：这个 effect 声明在前，同一次提交里先落到最新 spec，
  // 再决定弹不弹。
  const specRef = useRef(spec);
  useEffect(() => {
    specRef.current = spec;
  });

  // 卸载时收走自己那条：「组件已经没了」是状态本身表达不了的。同 id 有多份时只减一份计数，
  // 最后一份走了才撤（见 takeOwnership）。
  useEffect(() => takeOwnership(id), [id]);

  useEffect(() => {
    if (!active) {
      remove(id);
      return;
    }
    show({ ...specRef.current(active), id });
  }, [id, active]);
}
