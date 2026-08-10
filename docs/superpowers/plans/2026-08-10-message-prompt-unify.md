# 统一消息提示（方案 A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 DSA Web 前端所有用户反馈统一为三种标准呈现方式（toast / 错误卡片 / 确认框），消灭裸 div 与自渲染容器，并补全 LLM 配置错误的友好识别。

**Architecture:** 复用现有组件库（`InlineAlert` / `ApiErrorAlert` / `ConfirmDialog` / `ToastViewport`），不新增平行实现。把散落的 toast 容器收敛到已闲置的 `ToastViewport`，把 `StockScreeningPage`/`PaperTradingPage` 的裸 Tailwind div 替换为 `InlineAlert`，统一 `Badge` danger 色系，扩展 `api/error.ts` 的 `llm_not_configured` 识别，并把 toast 消息接入 i18n。

**Tech Stack:** React 18 + TypeScript + Vite + Tailwind + vitest。

**注意：** 本项目当前不是 git 仓库（`git rev-parse` 报 not a git repository），故本计划不含 `git commit` 步骤；每个任务以命令验证结束。若需要版本管理，请先 `git init`。

---

## 文件结构

| 文件 | 职责 | 操作 |
|---|---|---|
| `apps/dsa-web/src/api/error.ts` | 错误分类解析（补全 llm_not_configured） | 修改 |
| `apps/dsa-web/src/api/__tests__/error.test.ts` | LLM 错误识别测试 | 新建 |
| `apps/dsa-web/src/components/common/Badge.tsx` | danger 变体色系统一 | 修改 |
| `apps/dsa-web/src/i18n/uiText.ts` | 新增 toast 文案 key | 修改 |
| `apps/dsa-web/src/pages/StockScreeningPage.tsx` | 裸 div → InlineAlert | 修改 |
| `apps/dsa-web/src/pages/PaperTradingPage.tsx` | 裸 div → InlineAlert | 修改 |
| `apps/dsa-web/src/hooks/useSystemConfig.ts` | toast 消息 i18n | 修改 |
| `apps/dsa-web/src/pages/SettingsPage.tsx` | toast 容器迁移到 ToastViewport | 修改 |
| `apps/dsa-web/src/pages/ChatPage.tsx` | sendToast 迁移 + i18n | 修改 |

---

### Task 1: 补全 `api/error.ts` 的 LLM 配置错误识别（TDD）

**Files:**
- Modify: `apps/dsa-web/src/api/error.ts:376-392`
- Test: `apps/dsa-web/src/api/__tests__/error.test.ts`（新建）

- [ ] **Step 1: 写失败的测试**

创建 `apps/dsa-web/src/api/__tests__/error.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import { createApiError, getParsedApiError } from '../error';

function buildError(rawMessage: string, status = 400) {
  return createApiError(
    {
      title: '请求失败',
      message: rawMessage,
      rawMessage,
      status,
      category: 'http_error',
    },
    { response: { status, data: { detail: rawMessage }, statusText: 'Bad Request' } },
  );
}

describe('parseApiError - LLM 配置错误识别', () => {
  it('识别 "LLM Provider NOT provided"（无 provider 前缀）', () => {
    const parsed = getParsedApiError(buildError(
      'All LLM models failed (tried 1 model(s)). Last error: BadRequestError: '
      + 'litellm.BadRequestError: LLM Provider NOT provided. '
      + 'Pass in the LLM provider you are trying to call. You passed model=Qwen/Qwen3-235B-A22B-Thinking-2507',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('识别 "No API key provided"', () => {
    const parsed = getParsedApiError(buildError(
      'Authentication Error: No API key provided.',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('保持原有 "All LLM models failed ... last error: none" 分支', () => {
    const parsed = getParsedApiError(buildError(
      'All LLM models failed (tried 1 model(s)). Last error: None',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('不影响其他错误分类（上游超时）', () => {
    const parsed = getParsedApiError(buildError(
      'Service timed out after 30s',
    ));
    expect(parsed.category).toBe('upstream_timeout');
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd apps/dsa-web && npx vitest run src/api/__tests__/error.test.ts`
Expected: 前两个用例 FAIL（category 仍为 `http_error`），后两个按现有逻辑 PASS。

- [ ] **Step 3: 实现最小修改**

修改 `apps/dsa-web/src/api/error.ts` 的 `noConfiguredLlm` 分支（当前 376-392 行），替换为：

```ts
  const noConfiguredLlmHints = [
    'llm provider not provided',
    'provider not provided',
    'no api key provided',
    'api key not provided',
    'no authentication credentials',
  ];
  const noConfiguredLlm = (
    includesAny(matchText, ['all llm models failed']) && includesAny(matchText, ['last error: none'])
  ) || includesAny(matchText, [
    'no llm configured',
    'no effective primary model configured',
    'litellm_model not configured',
    'ai analysis will be unavailable',
    ...noConfiguredLlmHints,
  ]) || (
    includesAny(matchText, ['all llm models failed']) && includesAny(matchText, noConfiguredLlmHints)
  );
```

> 说明：`noConfiguredLlmHints` 只收录精确的 litellm 无凭据关键词，未收录宽泛的 `authentication error`，避免误伤登录/鉴权类错误。

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd apps/dsa-web && npx vitest run src/api/__tests__/error.test.ts`
Expected: 4 个用例全部 PASS。

---

### Task 2: 统一 `Badge` danger 色系

**Files:**
- Modify: `apps/dsa-web/src/components/common/Badge.tsx:19,27`

- [ ] **Step 1: 修改 danger 色系**

`variantStyles.danger` 由 `'border-danger/20 bg-danger/10 text-danger'` 改为与 `InlineAlert`/`ApiErrorAlert` 一致的 `color-danger-alert-*` 色系：

```ts
  danger: 'border-[hsl(var(--color-danger-alert-border)/0.3)] bg-[hsl(var(--color-danger-alert-bg)/0.1)] text-[hsl(var(--color-danger-alert-text))]',
```

`glowStyles.danger` 由 `'shadow-danger/20'` 改为：

```ts
  danger: 'shadow-[hsl(var(--color-danger-alert-border)/0.2)]',
```

- [ ] **Step 2: 验证编译**

Run: `cd apps/dsa-web && npx tsc -b --noEmit`
Expected: 无类型错误（后续 Task 7 统一跑完整 lint + build）。

---

### Task 3: 补充 i18n 文案

**Files:**
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh 对象 line ~2 起，en 对象 line ~959 起）

- [ ] **Step 1: 在 zh 对象新增 key**

在 `zh` 对象的 `settings.*` 区块（`settings.actionSuccess` 附近，约 line 655）新增：

```ts
  'settings.noChangesToSave': '当前没有可保存的修改。',
  'settings.configUpdated': '配置已更新',
  'settings.configUpdatedWarningPrefix': '；警告：',
```

在 `zh` 对象的 `chat.*` 区块新增：

```ts
  'chat.sendSuccess': '发送成功',
  'chat.sendFailed': '发送失败',
  'chat.sentToChannel': '已发送到通知渠道',
```

- [ ] **Step 2: 在 en 对象新增对应 key**

在 `en` 对象（`Record<UiTextKey, string>`）对应位置新增：

```ts
  'settings.noChangesToSave': 'There are no changes to save.',
  'settings.configUpdated': 'Configuration updated',
  'settings.configUpdatedWarningPrefix': '; Warning: ',
  'chat.sendSuccess': 'Sent',
  'chat.sendFailed': 'Send failed',
  'chat.sentToChannel': 'Sent to notification channel',
```

- [ ] **Step 3: 验证类型**

Run: `cd apps/dsa-web && npx tsc -b --noEmit`
Expected: `UiTextKey = keyof typeof zh`，en 若漏 key 会报类型错误；确认无报错。

---

### Task 4: 消灭裸 div（StockScreeningPage / PaperTradingPage）

**Files:**
- Modify: `apps/dsa-web/src/pages/StockScreeningPage.tsx:1221-1225, 1332-1338`
- Modify: `apps/dsa-web/src/pages/PaperTradingPage.tsx:132-144`

- [ ] **Step 1: StockScreeningPage 替换热点加载错误**

`StockScreeningPage.tsx` 已 import `InlineAlert`（line 38）。将 1221-1225 行的裸 `<p>` 替换为：

```tsx
        {hotspotsExpanded && hotspotError ? (
          <InlineAlert variant="warning" message={hotspotError} className="mb-3" />
        ) : null}
```

将 1332-1338 行的裸 `<p>`（hotspotDetailError）替换为：

```tsx
            {hotspotDetailError ? (
              <InlineAlert variant="warning" message={hotspotDetailError} className="mb-3" />
            ) : null}
```

> 注：1338 行之后的 `details` 折叠块（"详情数据已降级，展开查看原因"）保留原样——`InlineAlert` 不支持折叠，语义不同，不在本次范围。

- [ ] **Step 2: PaperTradingPage 替换成功 notice**

`PaperTradingPage.tsx` 需新增 import（line 6）：

```ts
import { ApiErrorAlert, Badge, EmptyState, InlineAlert } from '../components/common';
```

将 132-144 行的裸 `<div>`（notice）替换为：

```tsx
      {notice ? (
        <InlineAlert
          variant="success"
          message={notice}
          action={(
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="shrink-0 text-xs opacity-80 transition-opacity hover:opacity-100"
              aria-label={text.retry}
            >
              ×
            </button>
          )}
        />
      ) : null}
```

- [ ] **Step 3: 验证编译**

Run: `cd apps/dsa-web && npx tsc -b --noEmit`
Expected: 无类型错误。

---

### Task 5: SettingsPage toast 迁移到 ToastViewport + useSystemConfig i18n

**Files:**
- Modify: `apps/dsa-web/src/hooks/useSystemConfig.ts:304,309,347-350`
- Modify: `apps/dsa-web/src/pages/SettingsPage.tsx:10,1866-1879`

- [ ] **Step 1: useSystemConfig toast 消息 i18n**

新增 import（useSystemConfig.ts 顶部）：

```ts
import { useUiLanguage } from '../contexts/UiLanguageContext';
```

在 `useSystemConfig()` 函数体开头（`const [toast, setToast] = ...` 附近）加：

```ts
  const { t } = useUiLanguage();
```

修改 toast 消息（保持原逻辑）：

- line 304：`setToast({ type: 'success', message: '当前没有可保存的修改。' });` → `setToast({ type: 'success', message: t('settings.noChangesToSave') });`
- line 309：同上替换。
- line 347-350：

```ts
      const warningText = updateResult.warnings?.length
        ? `${t('settings.configUpdatedWarningPrefix')}${updateResult.warnings.join('；')}`
        : '';
      setToast({ type: 'success', message: `${t('settings.configUpdated')}${warningText}` });
```

在 `save` 的 `useCallback` 依赖数组（374-380 行）末尾追加 `t`。

- [ ] **Step 2: SettingsPage 渲染迁移**

`SettingsPage.tsx` 修改 import（line 10）：

```ts
import { ApiErrorAlert, Button, ConfirmDialog, EmptyState, InlineAlert, ToastViewport } from '../components/common';
```

将 1866-1879 行的自渲染容器替换为：

```tsx
      {toast ? (
        <ToastViewport>
          {toast.type === 'success'
            ? <InlineAlert variant="success" title={t('settings.actionSuccess')} message={toast.message} />
            : <ApiErrorAlert error={toast.error} />}
        </ToastViewport>
      ) : null}
```

> `SettingsAlert` 组件本身保留（line 532 仍以 inline 模式使用 `smokeSuccess`），仅不再使用其 `presentation="toast"` 模式。

- [ ] **Step 3: 验证编译**

Run: `cd apps/dsa-web && npx tsc -b --noEmit`
Expected: 无类型错误。

---

### Task 6: ChatPage sendToast 迁移 + i18n

**Files:**
- Modify: `apps/dsa-web/src/pages/ChatPage.tsx:9,1131,1134,1241-1286`

- [ ] **Step 1: 新增 ToastViewport import**

`ChatPage.tsx` line 9 的 common import 追加 `ToastViewport`：

```ts
import { ApiErrorAlert, Badge, Button, ConfirmDialog, EmptyState, InlineAlert, ListItemRow, ScrollArea, ToastViewport, Tooltip } from '../components/common';
```

- [ ] **Step 2: 顶部容器只保留 introToast，并简化其样式**

将 1241-1286 行的"Floating toast overlays"容器改为只含 introToast，去掉特殊半透明背景覆盖：

```tsx
          {/* Intro guide toast (page-local guidance, stays at top) */}
          <div className="pointer-events-none absolute inset-x-0 top-3 z-30 flex flex-col items-center gap-2 px-4">
            {introToastVisible ? (
              <InlineAlert
                variant="info"
                title={t(agentStatus?.backend === 'codex_app_server' ? 'chat.introCodex' : 'chat.introDefault')}
                message="输入股票代码或名称，选择技能后即可开始分析。"
                action={(
                  <button
                    type="button"
                    onClick={dismissIntroToast}
                    className="ml-3 self-start text-xs opacity-70 transition-opacity hover:opacity-100"
                    aria-label="关闭提示"
                  >
                    ✕
                  </button>
                )}
              />
            ) : null}
          </div>
```

> 删除了原 `style={{ backgroundColor: 'hsl(var(--primary) / 0.4)', backdropFilter: 'blur(12px)' }}` 与 `w-full max-w-md rounded-xl px-3 py-2 text-xs` 覆盖类，视觉归一到标准 `InlineAlert`。

- [ ] **Step 3: 在组件 JSX 根部渲染 sendToast 到 ToastViewport**

在 ChatPage 组件 JSX 最外层（与页面主布局同级的根 fragment 内，例如页面根 `</div>` 之前）添加：

```tsx
      <ToastViewport>
        {sendToast ? (
          <InlineAlert
            variant={sendToast.type === 'success' ? 'success' : 'danger'}
            title={sendToast.type === 'success' ? t('chat.sendSuccess') : t('chat.sendFailed')}
            message={sendToast.message}
          />
        ) : null}
      </ToastViewport>
```

- [ ] **Step 4: 调用点消息 i18n**

line 1131：`showSendFeedback({ type: 'success', message: '已发送到通知渠道' }, 3000);` → `showSendFeedback({ type: 'success', message: t('chat.sentToChannel') }, 3000);`

line 1134-1136：`showSendFeedback({ type: 'error', message: parsed.message || '发送失败' }, 5000);` → `showSendFeedback({ type: 'error', message: parsed.message || t('chat.sendFailed') }, 5000);`

- [ ] **Step 5: 验证编译**

Run: `cd apps/dsa-web && npx tsc -b --noEmit`
Expected: 无类型错误。

---

### Task 7: 全量验证

- [ ] **Step 1: Lint**

Run: `cd apps/dsa-web && npm run lint`
Expected: 无 error（仅允许既有 warning）。

- [ ] **Step 2: 单元测试**

Run: `cd apps/dsa-web && npx vitest run src/api/__tests__/error.test.ts`
Expected: 全部 PASS。

- [ ] **Step 3: 构建**

Run: `cd apps/dsa-web && npm run build`
Expected: `tsc -b && vite build` 成功，产物写入 `static/`。

- [ ] **Step 4: 手动验证（可选，需运行中的服务）**

- 在 Web 设置页保存配置：确认右下角出现标准 toast（成功/失败）。
- 未配置 LLM key 时触发分析/问股：确认展示"系统没有配置可用的 LLM 模型"友好提示，而非原始 litellm 报错。

---

## Self-Review 备注

- **Spec 覆盖**：Task 1（spec 第 4 点）、Task 2（spec 第 3 点）、Task 3（spec 第 5 点 i18n）、Task 4（spec 第 2 点）、Task 5/6（spec 第 1 点）、Task 7（spec 验证节）。无遗漏。
- **占位符**：无 TBD/TODO，所有步骤含具体代码与命令。
- **类型一致性**：`noConfiguredLlmHints`、`t()`、`ToastViewport`、`InlineAlert` 各任务间命名一致。
- **不做项**：不新建 ToastProvider、不动 ConfirmDialog/EmptyState/Loading、不改后端、不删 SettingsAlert。
