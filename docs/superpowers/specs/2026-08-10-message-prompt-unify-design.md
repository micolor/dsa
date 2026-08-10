# 统一消息提示的方式与样式（方案 A）

- 日期：2026-08-10
- 范围：`apps/dsa-web/` 前端 + `src/api/error.ts` 错误识别
- 目标：所有用户反馈按类型统一到三种标准呈现方式，消灭散落实现，补全 LLM 配置错误的友好识别

## 背景

前端已有统一组件基础：`InlineAlert`（约 49 处）、`ApiErrorAlert`（约 32 处）、`ConfirmDialog`。但仍存在以下不统一点：

1. `ToastViewport` 组件已实现但从未被使用；页面各自自渲染 toast 容器，三种定位（SettingsPage 右下角、ChatPage 顶部居中、PaperTradingPage 裸 div）。
2. `StockScreeningPage`、`PaperTradingPage` 用裸 Tailwind div 自绘警告/成功通知，绕过统一组件。
3. `Badge` 的 `danger` 变体颜色与 `InlineAlert`/`ApiErrorAlert` 的 danger 色系不一致。
4. toast 成功消息硬编码中文，未走 i18n。
5. `api/error.ts` 的 `llm_not_configured` 识别有缺口：`All LLM models failed ... Last error: BadRequestError: LLM Provider NOT provided` 未被识别，前端裸显示原始 litellm 报错（"LLM Provider NOT provided"、"model=Qwen/Qwen3-" 截断等）。

## 反馈类型 → 呈现方式映射

| 反馈类型 | 呈现方式 | 说明 |
|---|---|---|
| 操作反馈（成功/短提示） | 右下角 Toast | `ToastViewport` 容器 + `InlineAlert` 标准变体，自动关闭约 3.2s，可手动关闭 |
| 错误 | `ApiErrorAlert` 卡片 | 可展开 rawMessage 详情（现状已统一，保持） |
| 确认 | `ConfirmDialog` modal | 现状已统一，不动 |

## 改动清单

### 1. Toast 容器与视觉统一

- 启用 `ToastViewport`（`components/common/ToastViewport.tsx`，右下角 fixed，宽度 360px）。
- `pages/SettingsPage.tsx`：`useSystemConfig` 的 toast 渲染从自渲染 `fixed bottom-5 right-5 w-[320px]`（1866-1878 行）迁到 `ToastViewport`；success 分支由 `SettingsAlert presentation="toast"`（渐变顶部条）改为标准 `InlineAlert variant="success"`。自动关闭沿用现有 3.2s 逻辑。
- `pages/ChatPage.tsx`：`sendToast`（发送成功/失败）从顶部居中绝对定位迁到 `ToastViewport`，去掉半透明 blur 背景 inline style 覆盖，用标准 `InlineAlert` 变体。`introToast` 首次引导提示保留页面顶部原位，去掉特殊背景覆盖，改用标准 `InlineAlert variant="info"`。
- 结果：`SettingsAlert` 的 `presentation="toast"` 模式不再被调用；组件本身保留（`LoginPage` 仍用 inline 模式，且本设计不删除任何现有组件）。

### 2. 消灭裸 div 自绘

- `pages/StockScreeningPage.tsx`（约 1222、1332、1338 行）裸 warning div → `InlineAlert variant="warning"`。
- `pages/PaperTradingPage.tsx`（约 132-143 行）成功 notice 裸 div → `InlineAlert variant="success"`。

### 3. Badge danger 色系统一

- `components/common/Badge.tsx`：`danger` 变体从 `border-danger/20 bg-danger/10 text-danger` 改为与 `InlineAlert`/`ApiErrorAlert` 一致的 `color-danger-alert-*` 色系（`border-[hsl(var(--color-danger-alert-border)/0.3)] bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]`），`glowStyles` 相应对齐。

### 4. 错误识别补全（`apps/dsa-web/src/api/error.ts`）

扩展 `parseApiError` 的 `llm_not_configured` 分支（当前 376-392 行）：

- 新增关键词组，命中即归类 `llm_not_configured`：
  - `llm provider not provided`
  - `provider not provided`
  - `no api key provided`
  - `api key not provided`
  - `no authentication credentials` / `authentication error`
- 放宽 `all llm models failed` 分支：当前要求同时命中 `last error: none`；新增"last error 含上述关键词"也归类 `llm_not_configured`。
- 映射结果（沿用现有标题/文案）：标题"系统没有配置可用的 LLM 模型"，提示"请先在系统设置中配置主模型、可用渠道或相关 API Key 后再重试"，rawMessage 保留在可展开详情中。
- 覆盖用户当前遇到的错误：`All LLM models failed (tried 1 model(s)). Last error: BadRequestError: litellm.BadRequestError: LLM Provider NOT provided. ... You passed model=Qwen/Qwen3-...`

### 5. toast 消息 i18n

- `hooks/useSystemConfig.ts`：`'配置已更新'`、`'当前没有可保存的修改。'` 等 toast 消息接入 `useUiLanguage` 的 `t()`。
- `pages/ChatPage.tsx`：`'发送成功'`、`'发送失败'` 接入 `t()`。
- `i18n/uiText.ts` 补充对应 zh/en 文案。

### 6. 测试

- 新增 `apps/dsa-web/src/api/__tests__/error.test.ts`，覆盖：
  - `LLM Provider NOT provided` 归类 `llm_not_configured`。
  - `All LLM models failed` + `last error: BadRequestError ... LLM Provider NOT provided` 归类 `llm_not_configured`。
  - `No API key provided` 归类 `llm_not_configured`。
  - 现有非 LLM 错误（如超时、网络）不受影响。
- 既有组件测试保持通过。

## 不做（YAGNI）

- 不新建全局 `ToastProvider` / `useToast()`（方案 B 已排除）。
- 不改 `ConfirmDialog` / `EmptyState` / `Loading`（已统一）。
- 不改后端错误返回格式。
- 不删除 `SettingsAlert` 组件（仅不再使用其 toast 模式）。

## 验证

- `cd apps/dsa-web && npm run lint`
- `cd apps/dsa-web && npm run build`
- `cd apps/dsa-web && npx vitest run src/api/__tests__/error.test.ts`
- 手动验证：未配置 LLM key 场景下触发分析/问股，确认前端展示"系统没有配置可用的 LLM 模型"友好提示，而非原始 litellm 报错。
