import { render } from '@testing-library/react';
import type React from 'react';
import { useEffect } from 'react';
import { describe, expect, it } from 'vitest';
import { useEscTopLayer } from '../useEscTopLayer';

/**
 * `useEscTopLayer` 的直接单测。此前它只由各调用点的行为用例间接覆盖（抽屉/弹窗/菜单那条链），
 * 而它的判据本身——文档顺序、排除自己、容器不算压住子层、未认领时的兜底——没有一处被单独钉住。
 * 这里不经过任何 UI 事件，直接把 hook 返回的 `isCovered` 捞出来调，断言的是判据本身。
 */
type ProbeApi = { isCovered: () => boolean };

const Probe: React.FC<{
  capture: (api: ProbeApi) => void;
  /** 这一层怎么标记；`null` 表示不标记（模拟页面上的普通节点）。 */
  marker?: 'aria-modal' | 'data-esc-layer' | null;
  /** false 模拟「节点还没认领」：`modalRef` 没挂上，走 `!self` 兜底分支。 */
  claim?: boolean;
  children?: React.ReactNode;
}> = ({ capture, marker = 'aria-modal', claim = true, children }) => {
  const { modalRef, isCovered } = useEscTopLayer();
  // 在 effect 里交出判据：RTL 的 render 包在 act 内，render 返回前 effect 已跑完；
  // 交付的是 `isCovered` 本身（引用稳定），测试里再调它取的是**当时**的 DOM 状态。
  useEffect(() => {
    capture({ isCovered });
  }, [capture, isCovered]);
  const markerProps =
    marker === 'aria-modal'
      ? { 'aria-modal': true as const }
      : marker === 'data-esc-layer'
        ? { 'data-esc-layer': 'true' }
        : {};
  return (
    <div {...markerProps} ref={claim ? modalRef : undefined}>
      {children}
    </div>
  );
};

/** 造一个只在测试内使用的抓手；不改 props、不改 hook 参数，避开 react-hooks/immutability。 */
const makeCapture = () => {
  let api: ProbeApi = { isCovered: () => false };
  return {
    get api() {
      return api;
    },
    capture: (next: ProbeApi) => {
      api = next;
    },
  };
};

describe('useEscTopLayer', () => {
  it('is inert while it is the only layer on screen', () => {
    // 单层时 `isCovered()` 恒为 false，未发生叠加的调用点行为不变，靠的就是这条。
    const lower = makeCapture();
    render(<Probe capture={lower.capture} />);

    expect(lower.api.isCovered()).toBe(false);
  });

  it('yields to the layer that comes after it in document order', () => {
    // 契约核心：谁在上面按**文档顺序**判，后出现的那个既是后打开的也画在上面。
    const lower = makeCapture();
    const upper = makeCapture();
    render(
      <>
        <Probe capture={lower.capture} />
        <Probe capture={upper.capture} />
      </>,
    );

    expect(lower.api.isCovered()).toBe(true);
    expect(upper.api.isCovered()).toBe(false);
  });

  it('counts data-esc-layer as a layer just like aria-modal', () => {
    // 「`data-esc-layer="true"` 与 `aria-modal="true"` 等价地算一层」是整条契约的前提，
    // 两种标记混着叠时排序不能失效。
    const modal = makeCapture();
    const popup = makeCapture();
    render(
      <>
        <Probe capture={modal.capture} />
        <Probe capture={popup.capture} marker="data-esc-layer" />
      </>,
    );

    expect(modal.api.isCovered()).toBe(true);
    expect(popup.api.isCovered()).toBe(false);
  });

  it('does not treat a container as covering its own child layer', () => {
    // 容器不算压在子节点上面（`!layer.contains(self)`）：子层仍是最上面那层，该自己收 Esc。
    // 反过来子层算压在容器上（后出现的一方在上面），所以容器要让位。
    const outer = makeCapture();
    const inner = makeCapture();
    render(
      <Probe capture={outer.capture}>
        <Probe capture={inner.capture} />
      </Probe>,
    );

    expect(inner.api.isCovered()).toBe(false);
    expect(outer.api.isCovered()).toBe(true);
  });

  it('falls back to "more than one layer on screen" when the node is not claimed yet', () => {
    // 兜底分支（`!self`）：Esc 只会在挂载之后发生，这里防的是 ref 还没落上的那一刻，
    // 判断要与认领之后同源——屏上不止一层才让位。
    const unclaimed = makeCapture();
    render(<Probe capture={unclaimed.capture} claim={false} />);
    expect(unclaimed.api.isCovered()).toBe(false);

    const second = makeCapture();
    render(<Probe capture={second.capture} claim={false} />);
    expect(unclaimed.api.isCovered()).toBe(true);
  });

  it('ignores nodes that carry neither marker', () => {
    // 普通节点不算一层：页面正文、输入框之类不会被误判成压在上面的浮层。
    const onlyLayer = makeCapture();
    render(
      <>
        <div data-testid="plain" />
        <Probe capture={onlyLayer.capture} />
      </>,
    );

    expect(onlyLayer.api.isCovered()).toBe(false);
  });
});
