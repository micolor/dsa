# Liquid Glass 液态玻璃全站视觉改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `apps/dsa-web` 全站（暗/亮双主题）改造成统一的类苹果 Liquid Glass 风格——承载层半透明 + 背景模糊 + 顶部镜面高光，内容层保持可读。

**Architecture:** 以现有 CSS 设计令牌体系为基底。新增一套 `--glass-*` 令牌（`--glass-blur`/`--glass-saturate`/`--glass-bg`/`--glass-bg-strong`/`--glass-border`/`--glass-highlight`/`--glass-shadow`），加统一 `.glass-surface` / `.glass-surface-strong` 工具类（backdrop-filter + 顶部高光伪元素）。承载层容器通过 className 挂工具类获得玻璃质感；`--card` 等核心令牌**保持纯 HSL 不变**（全库 46 处 `bg-card/N` 透明度修饰符依赖此，内联 alpha 会编译成双重 alpha 失效）。背景层增强 body 的 theme-aware 光斑渐变，让玻璃有内容可糊。

**Tech Stack:** React 19 + Vite 7 + Tailwind CSS v4（`@config` 走 tailwind.config.js）+ Vitest。

**前置约束（务必先读）：**
- 本仓库 `AGENTS.md`：commit message 用英文、**不加** `Co-Authored-By`；`--card`/`--popover`/`--elevated`/`--hover` 严禁改成带 alpha 的 HSL。
- 每次任务结束必须 `npm test`（vitest，可用 `-t <pattern>` 过滤）+ `npm run build`，确认零回归再 commit。
- 玻璃容器**不互相嵌套**（避免多层 backdrop-filter 的性能与视觉脏污）。

---

### Task 1: 玻璃令牌与工具类（`src/index.css`）

**Files:**
- Modify: `apps/dsa-web/src/index.css`（`:root` 亮色块 + `.dark` 暗色块 + Utilities 区）

- [ ] **Step 1: 在 `:root` 亮色块末尾新增玻璃令牌**

在 `:root` 块内（`--home-rail-shadow` 等变量之后）追加：

```css
/* Liquid Glass tokens (Light theme) */
--glass-blur: 24px;
--glass-saturate: 1.6;
--glass-bg: hsl(214 40% 98% / 0.55);
--glass-bg-strong: hsl(214 40% 98% / 0.72);
--glass-border: hsl(230 30% 20% / 0.12);
--glass-highlight: hsl(0 0% 100% / 0.8);
--glass-shadow: hsl(230 25% 40% / 0.18);

/* 背景光斑（Light theme，玻璃的底衬） */
--bg-orb-1: hsl(247 84% 58% / 0.16);
--bg-orb-2: hsl(247 84% 66% / 0.14);
--bg-orb-3: hsl(190 95% 45% / 0.1);
--bg-orb-4: hsl(152 69% 45% / 0.08);
```

- [ ] **Step 2: 在 `.dark` 暗色块末尾新增玻璃令牌**

在 `.dark` 块内末尾（`--home-surface-button-border-hover` 之后）追加：

```css
/* Liquid Glass tokens (Dark theme) */
--glass-blur: 24px;
--glass-saturate: 1.6;
--glass-bg: hsl(230 26% 12% / 0.5);
--glass-bg-strong: hsl(230 26% 14% / 0.68);
--glass-border: hsl(0 0% 100% / 0.14);
--glass-highlight: hsl(0 0% 100% / 0.6);
--glass-shadow: hsl(228 30% 4% / 0.5);

/* 背景光斑（Dark theme，玻璃的底衬，暗色更强） */
--bg-orb-1: hsl(247 84% 58% / 0.28);
--bg-orb-2: hsl(247 84% 72% / 0.24);
--bg-orb-3: hsl(190 95% 45% / 0.16);
--bg-orb-4: hsl(152 69% 45% / 0.12);
```

- [ ] **Step 3: 在 Utilities 区新增 `.glass-surface` 工具类**

在 `/* ============ Glassmorphism Panels ============ */` 小节内、`.glass-panel-lg` 之后插入：

```css
/* Liquid Glass 统一表面工具（承载层：侧边栏/顶栏/卡片/弹窗/工具栏/Toast） */
.glass-surface {
  position: relative;
  border: 1px solid var(--glass-border);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  background: var(--glass-bg);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),
    0 12px 30px var(--glass-shadow);
}
/* 顶部镜面高光带 */
.glass-surface::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-highlight), transparent);
  pointer-events: none;
}
/* 更强玻璃：弹窗/抽屉等浮层，更实底 + 更大模糊 */
.glass-surface-strong {
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(calc(var(--glass-blur) + 8px)) saturate(var(--glass-saturate));
  backdrop-filter: blur(calc(var(--glass-blur) + 8px)) saturate(var(--glass-saturate));
}
```

- [ ] **Step 4: 验证构建**

Run: `cd apps/dsa-web && npm run build`
Expected: `tsc -b && vite build` 成功，CSS 编译无错。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/index.css docs/superpowers/specs/2026-08-19-liquid-glass-design.md
git commit -m "feat(web): add liquid glass design tokens and surface utility"
```

---

### Task 2: 全局背景层增强（玻璃的底衬）

**Files:**
- Modify: `apps/dsa-web/src/index.css`（`body` 背景块，约 579 行）
- Modify: `apps/dsa-web/src/components/common/ParticleBackground.tsx`

- [ ] **Step 1: 替换 `body` 背景为 theme-aware 光斑**

把 body 的 `background:` 四层光斑替换为使用新令牌（结构保持 `radial-gradient` + 基底色）：

```css
body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
  background:
    radial-gradient(66rem 48rem at 12% -8%, var(--bg-orb-1), transparent 60%),
    radial-gradient(58rem 44rem at 108% 112%, var(--bg-orb-2), transparent 60%),
    radial-gradient(46rem 36rem at 80% 8%, var(--bg-orb-3), transparent 58%),
    radial-gradient(40rem 32rem at 55% 120%, var(--bg-orb-4), transparent 60%),
    hsl(var(--background));
  color: hsl(var(--foreground));
}
```

- [ ] **Step 2: 增强 `ParticleBackground` 为柔和光点（径向渐变 + reduced-motion 静态）**

替换 `drawParticle` 函数（柔光光点，替代硬边小圆点）：

```ts
function drawParticle(ctx: CanvasRenderingContext2D, particle: Particle) {
  const grad = ctx.createRadialGradient(
    particle.x,
    particle.y,
    0,
    particle.x,
    particle.y,
    particle.radius * 3,
  );
  grad.addColorStop(0, `rgba(${particle.color}, ${particle.baseAlpha})`);
  grad.addColorStop(1, `rgba(${particle.color}, 0)`);
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(particle.x, particle.y, particle.radius * 3, 0, Math.PI * 2);
  ctx.fill();
}
```

把粒子参数调柔和：`radius: Math.random() * 2.0 + 1.0` → `radius: Math.random() * 3.0 + 2.0`；`baseAlpha` 由 `Math.random() * 0.6 + 0.2` → `Math.random() * 0.35 + 0.15`。

在 `useEffect` 开头加 reduced-motion 检测，动画循环改为仅首帧静态绘制：

```ts
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
```

`animate()` 内：`if (prefersReducedMotion) return;` 放在 `requestAnimationFrame(animate)` 调用之前（首次 `animate()` 仍会执行一次完整绘制，之后不再循环）。并把 `drawLines`（粒子连线 + 鼠标牵引）整体保留不动。

- [ ] **Step 3: 验证构建 + 现有测试**

Run: `npm run build`
Expected: 成功。
Run: `npm test -t ParticleBackground 2>/dev/null || npm test`
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add apps/dsa-web/src/index.css apps/dsa-web/src/components/common/ParticleBackground.tsx
git commit -m "feat(web): enrich background orbs and soften particle background for glass"
```

---

### Task 3: 统一现有玻璃类到新令牌 + 镜面高光

**Files:**
- Modify: `apps/dsa-web/src/index.css`（`.terminal-card` / `.gradient-border-card-inner` / `.glass-card` / `.glass-panel` / `.glass-panel-lg` 约 1027-1210 行）

- [ ] **Step 1: `.terminal-card` 与 `.gradient-border-card-inner` 挂高光伪元素**

`.terminal-card` 与 `.gradient-border-card-inner` 已有 `backdrop-filter`，改用它俩共用新令牌 + 高光。在 `.terminal-card` 块内 background 的 `linear-gradient(...)` 之后追加 `box-shadow` 覆盖为双层（含 `inset 0 1px 0 var(--glass-highlight)`），并追加 `::before` 高光带（与 `.glass-surface::before` 相同）：

```css
.terminal-card::before,
.gradient-border-card-inner::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-highlight), transparent);
  pointer-events: none;
  z-index: 1;
}
```

`.terminal-card` 的 `box-shadow: var(--terminal-card-shadow)` 改为：
```css
box-shadow:
  inset 0 1px 0 var(--glass-highlight),
  var(--terminal-card-shadow);
```

- [ ] **Step 2: `.glass-card` 改用新令牌**

`.glass-card` 块（1200-1215 行附近）的 `background` 与 `box-shadow` 中的硬编码 `hsl(...)` 替换为令牌：

```css
.glass-card {
  position: relative;
  overflow: hidden;
  border-radius: 1.25rem;
  border: 1px solid var(--glass-border);
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),
    0 20px 48px var(--glass-shadow);
}
```

（`.glass-card::after` 保留原样。）

- [ ] **Step 3: `.glass-panel` / `.glass-panel-lg` 收敛到令牌**

将 `@apply` 里的 `bg-card/70 border-border/60` 改为：

```css
.glass-panel {
  @apply rounded-2xl shadow-soft-card transition-all;
  border: 1px solid var(--glass-border);
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-saturate));
}
```

`.glass-panel-lg` 同理（rounded-3xl）。

- [ ] **Step 4: 验证构建**

Run: `npm run build`
Expected: 成功，无 CSS 语法报错。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/index.css
git commit -m "feat(web): unify existing glass classes onto liquid glass tokens"
```

---

### Task 4: Shell 承载层玻璃（顶栏 / 导航 / 任务中心）

**Files:**
- Modify: `apps/dsa-web/src/components/layout/Shell.tsx`（header 84-88 行、菜单面板 106 行、菜单按钮 98 行）
- Modify: `apps/dsa-web/src/components/layout/SidebarNav.tsx`（如有实底容器）
- Modify: `apps/dsa-web/src/components/tasks/FloatingTaskPanel.tsx`（71 行按钮、94 行面板）

- [ ] **Step 1: Shell header 统一为玻璃**

`header` 的 className（84-88 行）改为：

```tsx
className={
  'sticky top-1 z-40 glass-surface transition-[border-color] duration-200 ' +
  (scrolled
    ? 'border-b border-border/40'
    : 'border-b border-transparent')
}
```

注意：`.glass-surface` 自带 1px 边框与高光，`border-b` 覆盖用 `border-border/40`/`transparent` 控制滚动态下划线即可。

- [ ] **Step 2: 导航弹出面板玻璃**

菜单面板（106 行）`bg-card/95 ... backdrop-blur-2xl` → 用 `glass-surface-strong`，其余保留：

```tsx
className="absolute left-2 top-full mt-1.5 w-60 max-h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl glass-surface-strong p-2.5 shadow-soft-card"
```

- [ ] **Step 3: 任务中心浮层玻璃**

`FloatingTaskPanel.tsx`：71 行按钮 `bg-card/60 ... backdrop-blur-xl` 保持（按钮体积小、保持原玻璃）；94 行面板容器追加 `glass-surface-strong overflow-hidden rounded-2xl`：

```tsx
<div className="w-[34rem] max-h-[min(24rem,calc(100vh-12rem))] overflow-hidden rounded-2xl glass-surface-strong">
```

- [ ] **Step 4: 验证构建 + Shell 测试**

Run: `npm run build`
Run: `npm test -t "Shell"`（`src/components/layout/__tests__/Shell.test.tsx`）
Expected: 构建成功；Shell 测试全绿。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/layout/Shell.tsx apps/dsa-web/src/components/tasks/FloatingTaskPanel.tsx
git commit -m "feat(web): apply liquid glass to shell, nav, and task center"
```

---

### Task 5: 浮层玻璃（Dialog / Drawer / ConfirmDialog / Toast）

**Files:**
- Modify: `apps/dsa-web/src/components/common/Dialog.tsx`（59 行面板 `bg-card`）
- Modify: `apps/dsa-web/src/components/common/Drawer.tsx`（84 行面板 `bg-card`）
- Modify: `apps/dsa-web/src/components/common/ConfirmDialog.tsx`（40 行遮罩 + 面板）
- Modify: `apps/dsa-web/src/components/common/ToastViewport.tsx`（容器留白；Toast 内容由调用方传类）

- [ ] **Step 1: Dialog 面板玻璃**

`Dialog.tsx` 59 行面板 className：`... rounded-t-2xl border border-border/80 bg-card shadow-soft-card-strong ...` → 去掉 `bg-card border-border/80`，替换为 `glass-surface-strong`（保留 rounded、flex、宽高类）：

```tsx
className={`relative flex w-full flex-col overflow-hidden rounded-t-2xl glass-surface-strong shadow-soft-card-strong sm:rounded-2xl ${widthClassName} ${maxHeightClassName}`}
```

- [ ] **Step 2: Drawer 面板玻璃**

`Drawer.tsx` 84 行：`relative flex w-full flex-col bg-card` → `relative flex w-full flex-col glass-surface-strong`（保留其余抽屉类名）。

- [ ] **Step 3: ConfirmDialog 遮罩与面板玻璃**

`ConfirmDialog.tsx`：遮罩 40 行 `bg-black/60 backdrop-blur-sm` 保留（遮罩层不叠玻璃）；定位其内部面板容器（dialog 内容卡片，用 `bg-card` 或实底），替换为 `glass-surface-strong`。若面板已用 `glass-card`/`terminal-card`，改为 `glass-surface-strong`。

- [ ] **Step 4: Toast 玻璃**

`ToastViewport.tsx` 容器保持 `pointer-events-none` 布局类不动。在项目内查找 toast 内容类（`InlineAlert` 的 presentation 或调用处 `rounded-*` 容器），给 toast 浮条加 `glass-surface-strong`（例如 `ApiErrorAlert` / 各页面 toast 外层 `fixed` 容器）。至少保证：搜索 `InlineAlert` 组件内是否有自绘底色，若有 `bg-card`/实底则换 `glass-surface-strong`。

- [ ] **Step 5: 验证构建 + 组件测试**

Run: `npm run build`
Run: `npm test -t "Dialog|Drawer|ConfirmDialog|Toast" 2>/dev/null || npm test`
Expected: 全绿。若测试断言了具体类名（如 `toHaveClass('bg-card')`），同步更新断言为 `glass-surface-strong`——先 grep 确认再改。

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/src/components/common/Dialog.tsx apps/dsa-web/src/components/common/Drawer.tsx apps/dsa-web/src/components/common/ConfirmDialog.tsx apps/dsa-web/src/components/common/ToastViewport.tsx
git commit -m "feat(web): apply liquid glass to dialogs, drawers, and toasts"
```

---

### Task 6: 工具栏 / 操作条 / 统计卡 / 任务面板 / 首页面板玻璃

**Files:**
- Modify: `apps/dsa-web/src/components/common/Toolbar.tsx`（12 行 `glass-panel` 已收敛，无需改）
- Modify: `apps/dsa-web/src/components/common/StickyActionBar.tsx`（11 行 `bg-card/85`）
- Modify: `apps/dsa-web/src/components/common/StatCard.tsx`（36 行 `glass-card` 已收敛，无需改）
- Modify: `apps/dsa-web/src/components/tasks/TaskPanel.tsx`（198 行 `bg-card/95`）
- Modify: `apps/dsa-web/src/index.css`（`.home-panel-card` / `.home-rail-card` 已走 `.terminal-card` 变量，核对高光伪元素已覆盖）

- [ ] **Step 1: StickyActionBar 玻璃**

`StickyActionBar.tsx` 11 行：`bg-card/85` → `glass-surface-strong`（保留 sticky/rounded/p-3/shadow）：

```tsx
<div className={cn('sticky bottom-4 z-20 rounded-2xl glass-surface-strong p-3', className)}>
```

- [ ] **Step 2: TaskPanel 玻璃**

`TaskPanel.tsx` 198 行：`home-panel-card bg-card/95 shrink-0 overflow-hidden` → `home-panel-card glass-surface shrink-0 overflow-hidden`。

- [ ] **Step 3: 首页面板核对**

`.home-panel-card` / `.home-rail-card` 通过 `--terminal-card-*` 变量复用 `.terminal-card` 的玻璃底色与 `backdrop-filter`；确认 Task 3 加的高光伪元素在 `.home-panel-card` 元素上生效（`position: relative` 需在元素上，若 `.home-panel-card` 未设 `position: relative`，在 `.home-panel-card { position: relative; }` 补充）。

- [ ] **Step 4: 验证构建 + 相关测试**

Run: `npm run build`
Run: `npm test -t "StickyActionBar|TaskPanel|StatCard|Toolbar" 2>/dev/null || npm test`
Expected: 全绿；若有类名断言同步更新。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/common/StickyActionBar.tsx apps/dsa-web/src/components/tasks/TaskPanel.tsx apps/dsa-web/src/index.css
git commit -m "feat(web): apply liquid glass to action bars and task panels"
```

---

### Task 7: 内容层可读性保底 + 亮暗主题 + reduced-motion

**Files:**
- Read-only 检查: `apps/dsa-web/src/components/common/Input.tsx`、表格容器、Markdown 报告样式
- Modify: `apps/dsa-web/src/index.css`（reduced-motion 小节约 1012 行）

- [ ] **Step 1: 内容层确认不叠 blur**

grep 检查表格行/单元格、Markdown prose、Input 内是否有 `backdrop-filter`/`backdrop-blur`。若内容层出现 blur，降级为纯半透明底色（用 `--glass-bg` 做背景即可，不叠 `backdrop-filter`）。Input 的 `.input-surface`（724 行已有 `backdrop-blur(10px)`）保留——输入区小面积模糊不伤可读。

- [ ] **Step 2: 亮色主题可读性抽查**

确认亮色下 `--glass-bg: hsl(214 40% 98% / 0.55)` 上文字对比度足够（`--foreground: 228 35% 12%`）。若不足，`--glass-bg` 亮色提升到 0.62、`--glass-bg-strong` 到 0.78。

- [ ] **Step 3: reduced-motion 补玻璃相关降级**

在 `@media (prefers-reduced-motion: reduce)` 块内追加：

```css
.glass-surface::before,
.terminal-card::before,
.gradient-border-card-inner::before {
  display: none; /* 静态下隐藏镜面高光位移感 */
}
```

- [ ] **Step 4: 全量测试 + lint + 构建**

Run: `npm test`
Expected: 全绿。
Run: `npm run lint`
Expected: 无 error（存量 warning 可保留）。
Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/index.css
git commit -m "fix(web): guard content readability and reduced-motion for glass"
```

---

### Task 8: 视觉回归 + 文档

**Files:**
- Create: 截图产物（放 `apps/dsa-web/.liquid-glass-shots/`，不入库）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 段，扁平格式）

- [ ] **Step 1: 浏览器截图对比**

用 Chrome DevTools 截图：首页 Dashboard、侧边栏展开、一个 Dialog、一个表格页——暗/亮两主题各一张，与 git HEAD 前的版本对比，确认承载层玻璃、高光、背景光斑到位，内容层可读。

- [ ] **Step 2: 更新 CHANGELOG**

`docs/CHANGELOG.md` 的 `[Unreleased]` 段**追加一行**（扁平格式，禁标题）：

```text
- [改进] Web 前端整体改造为液态玻璃（Liquid Glass）风格：半透明玻璃承载层、背景光斑、顶部镜面高光；暗/亮双主题同步；内容层保持可读
```

- [ ] **Step 3: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: add changelog entry for liquid glass redesign"
```

---

## Self-Review 记录

- **Spec 覆盖**：① 均衡策略→Task 4-6 承载层玻璃 / Task 7 内容层保底；② 背景底衬→Task 2；③ 令牌改造→Task 1/3；④ 容器规则→Task 1 工具类 + Task 4-6 应用；⑤ 性能与验证→Task 7 全量 + Task 8 截图。
- **占位符检查**：无 TBD/TODO；所有步骤含具体类名、路径、代码块。
- **类型一致性**：`.glass-surface` / `.glass-surface-strong` 命名全程一致；`--glass-*` 令牌在 Task 1 定义、Task 3/4/5/6 使用，无未定义引用。
