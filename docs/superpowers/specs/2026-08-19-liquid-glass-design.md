# Liquid Glass 液态玻璃全站视觉改造（方案 A+B）

- 日期：2026-08-19
- 范围：`apps/dsa-web/` 前端
- 目标：全站（暗/亮双主题）统一改为类苹果 Liquid Glass 风格——半透明表面 + 背景模糊 + 顶部镜面高光 + 底部投影 + 圆润形态，同时保证行情数据的可读性
- 路线：**A（设计令牌层全局换肤）为主 + B（玻璃容器规则改造关键承载层）为辅**

## 1. 视觉语言与均衡策略

### 1.1 核心机制

玻璃质感由四要素构成：

1. **半透明表面**：背景色带 alpha（`hsla(...)`），透出背后光斑
2. **背景模糊**：`backdrop-filter: blur(...) saturate(...)`，让背后内容糊化、色彩提纯
3. **顶部镜面高光**：表面顶部一条 1px 白→透明线性渐变，模拟玻璃受光
4. **底部投影 + 内高光**：环境投影 + `inset 0 1px 0` 内高光，形成悬浮深度

模糊的"素材"来自背后增强版彩色光斑背景，玻璃才有通透感。

### 1.2 均衡分工（Glass / Solid 边界）

| 层级 | 处理 | 说明 |
|---|---|---|
| **承载层** | 真玻璃 | 侧边栏、顶栏、Card/SectionCard/StatCard、Dialog/Drawer/ConfirmDialog、Toolbar/StickyActionBar、Toast、首页面板、历史栏 → `backdrop-filter` + 高光 |
| **内容层** | 继承半透明底色，**不叠 blur** | 表格行/单元格、图表内、Markdown 报告正文、小字号状态文本、Input 输入区、Badge |

规则：**玻璃容器不互相嵌套**（避免多层 blur 的性能与视觉脏污）；内容层只取半透明底色，保持高对比。

## 2. 全局背景层（玻璃的底衬）

### 2.1 基底渐变

- 暗色：深蓝紫渐变，示例 `linear-gradient(160deg, #0d101f 0%, #151a3a 45%, #0e1226 100%)`
- 亮色：浅灰蓝渐变，示例 `linear-gradient(160deg, #eef1fa 0%, #f6f3ff 45%, #edf6fb 100%)`

作为 `body`/App 根容器的固定背景。

### 2.2 光斑层

- **增强 `ParticleBackground`**：粒子半径增大、画笔加高斯模糊（柔和光点）、色彩更饱和（沿用青/紫/绿系），透明度受控，形成玻璃背后的"光"
- **叠加 2~3 个固定 CSS `radial-gradient` 光斑**（`position: fixed`，低 alpha），确保每块玻璃背后都有可糊的明暗层次
- 暗色下光斑更亮、alpha 更高；亮色下更柔和
- 遵循 `prefers-reduced-motion` 时减弱光斑动画与位移

## 3. 设计令牌改造（`src/index.css` + `tailwind.config.js`）

所有颜色走令牌，改动集中在 `index.css` 的 `:root` / `.dark`（及亮色基础值），组件代码几乎不动。

### 3.1 令牌变更表

| 令牌 | 现状 | 改后（暗色示例） |
|---|---|---|
| `--card` / `--popover` / `--elevated` / `--hover` | 实心（纯 HSL） | **保持纯 HSL 不变**：全库 46 处 `bg-card/N` 透明度修饰符依赖其无内联 alpha（内联 alpha 会编译成 `hsl(A / a / a)` 双重 alpha 失效）。玻璃透明度由 `--glass-bg` / `--glass-bg-strong` 承担 |
| `--surface-1/2/3` | 实心色 | 保持结构，玻璃表面统一走 `--glass-bg` 族 |
| `--background` | 纯色 | 渐变色相（配合背景层），`--bg-base` 同步 |
| `--border` | 单色 | 玻璃边框 `hsla(0 0% 100% / 0.14)`（暗色） |
| `--shadow-soft-card` 等 | 单层投影 | 双层：`inset 0 1px 0 高光` + 底部环境投影 |
| 新增 | — | `--glass-blur`（默认 24px）、`--glass-saturate`（默认 1.6）、`--glass-highlight`（高光色）、`--glass-shadow`（投影色）、`--glass-bg` / `--glass-bg-strong`（玻璃底色，暗/亮各一套）、`--glass-border`（玻璃边框色） |

- 亮色主题令牌同步配一套：卡片 `hsla(210 40% 98% / 0.6)`、边框 `hsla(230 30% 20% / 0.1)` 等
- 现有 `--home-action-ai-bg` 等零散半透明变量统一并入玻璃令牌，减少重复

### 3.2 传播方式

`--card` 等令牌保持纯 HSL 保证 `bg-card/N` 兼容；玻璃半透明底色与模糊由第 4 节 `.glass-surface` 规则在承载层容器上显式应用（组件 className 加 `glass-surface`/`glass-surface-strong`）。已带玻璃质的现有类（`.terminal-card`、`.glass-card`、`.glass-panel`）改为复用同一套 `--glass-*` 令牌，视觉统一。

## 4. 玻璃容器规则与组件改造清单

### 4.1 `.glass-surface` 工具（`index.css`）

```css
.glass-surface {
  position: relative;
  -webkit-backdrop-filter: blur(var(--glass-blur, 24px)) saturate(var(--glass-saturate, 1.6));
  backdrop-filter: blur(var(--glass-blur, 24px)) saturate(var(--glass-saturate, 1.6));
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),
    0 12px 30px var(--glass-shadow);
}
.glass-surface::before { /* 顶部镜面高光带 */
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-highlight), transparent);
  pointer-events: none;
}
```

变体按层调节参数（示例）：

| 容器 | blur | 底色 alpha（暗色） |
|---|---|---|
| 侧边栏 / 顶栏 | 24px | 0.50 |
| Card / SectionCard | 16px | 0.55 |
| Dialog / Drawer | 32px | 0.70 |
| Toolbar / StickyActionBar | 20px | 0.55 |
| Toast | 24px | 0.70 |

### 4.2 改造清单

- **改造为玻璃**：`Shell` 侧边栏、顶部栏、`Card`/`SectionCard`/`StatCard`、`Dialog`/`Drawer`/`ConfirmDialog`、`Toolbar`/`StickyActionBar`、`ToastViewport`、首页面板与历史栏
- **保持可读（不叠 blur）**：表格、图表、Markdown 正文、Input、Badge
- **交互态**：hover 提高 alpha/blur 并升高高光；选中态沿用现有 primary 高光边框语义

## 5. 性能、兼容与验证

### 5.1 性能与兼容

- `backdrop-filter` 现代浏览器（Chrome/Edge/Firefox/Safari 新版）均支持，保留 `-webkit-` 前缀
- **避免嵌套多层 blur**：玻璃容器内不再套玻璃容器（第 1.2 节规则）
- 粒子 canvas 限制帧率（现有实现上降低粒子数/位移幅度），光斑用 CSS 渐变替代部分 canvas 负载
- `prefers-reduced-motion`：降级光斑动画、模糊可保留（视觉静态）

### 5.2 验证矩阵

| 项 | 方式 |
|---|---|
| 现有测试 | `npm test`（vitest）语义不变应全绿 |
| lint / 构建 | `npm run lint` + `npm run build` |
| 视觉回归 | 浏览器截图对比：首页 Dashboard、侧边栏、弹窗、表格页（暗/亮两主题） |

### 5.3 回滚方式

改动集中在 `src/index.css`、`tailwind.config.js`、`ParticleBackground.tsx` 及少量容器组件；回滚 = 恢复这些文件的 git 历史版本。

## 6. 明确不做（YAGNI）

- 不引入新 UI 组件库 / 不重写通用组件
- 不删除现有任何组件
- 不迁移设计系统文件结构
- 不改动任何业务逻辑、API、i18n 文案
