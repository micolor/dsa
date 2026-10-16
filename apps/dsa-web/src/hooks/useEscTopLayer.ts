import { useCallback, useRef } from 'react';

/**
 * 所有「按 Esc 收起自己」的浮层：模态浮层带 `aria-modal="true"`，非模态弹出层（日期选择器、
 * 下拉、候选列表）自己挂 `data-esc-layer="true"`。两者共用同一套排序，见下。
 */
const ESC_LAYER_SELECTOR = '[aria-modal="true"], [data-esc-layer="true"]';

/**
 * 浮层叠浮层时的 Esc 让位规则：Esc 只归最上面那层。
 *
 * 为什么需要它：这些层大多在 document 上各挂一个 keydown（模态浮层为了「焦点在不在我里面都
 * 能关」，弹出层同理）。document 上的监听按注册顺序执行，而注册顺序跟「谁在上面」没有关系 ——
 * 下层往往是先打开的，也就先注册。于是「在抽屉里点删除 → 弹出确认框 → 按 Esc」会一次把两层一起
 * 收掉：用户只是想从确认框里退出来，却连刚打开、可能已经翻了一半的详情一起丢了。交易弹窗里
 * 点开日期选择器或「方向」下拉后按 Esc 会连整个表单一起关掉，也是这一条。
 *
 * 为什么把非模态弹出层也算作一层：Esc 的语义是「收起最上面那个能收的东西」，与「这一层有没有
 * 阻断页面其它部分」无关。日期选择器盖在弹窗上时，它就是最上面那层，Esc 该收的是它。
 *
 * 判据不看某个具体开关：模态认 `aria-modal`（全站所有模态都带），弹出层认 `data-esc-layer`，
 * 以后再加一层也不必回来改这里。
 *
 * 「算作一层」的门槛是「这一层自己收得住 Esc」：既要有自己的 Esc 处理，又要能被焦点触发到。
 * `Tooltip` 的气泡按此**不算**——它不可聚焦、`role="tooltip"`，还能由悬停打开，标上就会变成
 * 一个「Esc 收不掉、却把下层拦住」的东西（鼠标悬在信息图标上时弹窗的 Esc 会直接失效）。
 * 它的关闭路径仍绑在触发元素自己的 `onKeyDown` 上，只在触发元素持有焦点时响应。
 *
 * 用法：把 `modalRef` 挂到代表「这一层」的那个节点上，Esc 处理里先问 `isCovered()`。
 * 单层时它恒为 `false`，所以对没有发生叠加的调用点是惰性的 —— 只有真的叠了两层、
 * 也就是原本会一次关两层的时候，行为才改变。
 */
export function useEscTopLayer(): {
  modalRef: (node: HTMLElement | null) => void;
  isCovered: () => boolean;
} {
  const selfRef = useRef<HTMLElement | null>(null);

  const modalRef = useCallback((node: HTMLElement | null) => {
    selfRef.current = node;
  }, []);

  const isCovered = useCallback(() => {
    const self = selfRef.current;
    const layers = Array.from(document.querySelectorAll<HTMLElement>(ESC_LAYER_SELECTOR));
    if (!self) {
      // 还没认领到自己的节点（Esc 只会在挂载之后发生，这里只是兜底）：
      // 只在确实不止一层时才让位，和认领之后保持同一套判断。
      return layers.length > 1;
    }
    // 「谁在上面」的判据是文档顺序：这几层要么 portal 到 body 末尾、要么就在页面树里，
    // 后出现的那个既是后打开的，也画在上面（子节点也算在后 —— 子节点本来就该盖住容器）。
    // 不用 z-index 判，是因为各调用点自己定 z-index，跨文件比较反而更脆。
    // 唯一要排除的是「自己」，以及「自己是在它内部的那一层」——容器不算压在子节点上面。
    return layers.some(
      (layer) =>
        layer !== self
        && !layer.contains(self)
        && Boolean(self.compareDocumentPosition(layer) & Node.DOCUMENT_POSITION_FOLLOWING),
    );
  }, []);

  return { modalRef, isCovered };
}
