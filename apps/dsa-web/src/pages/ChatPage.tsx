import React, { useState, useRef, useEffect, useCallback, memo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, ChevronDown, Copy, Download, RotateCcw, RotateCw, SlidersHorizontal, X, ZoomIn, ZoomOut } from 'lucide-react';
import { cn } from '../utils/cn';
import { agentApi } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import { Badge, Button, ConfirmDialog, Dialog, EmptyState, InlineAlert, ListItemRow, ScrollArea, Tooltip, useToastSync } from '../components/common';
import { createParsedApiError, getParsedApiError } from '../api/error';
import { applyActionProposal } from '../utils/actionProposal';
import type { ActionProposal } from '../types/actionProposal';
import type { AgentStatusResponse, SkillInfo } from '../api/agent';
import { DashboardStateBlock } from '../components/dashboard';
import {
  useAgentChatStore,
  type Message,
  type ProgressStep,
} from '../stores/agentChatStore';
import { downloadSession, formatSessionAsMarkdown } from '../utils/chatExport';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  buildFollowUpPrompt,
  parseFollowUpRecordId,
  resolveChatFollowUpContext,
  sanitizeFollowUpStockCode,
  sanitizeFollowUpStockName,
} from '../utils/chatFollowUp';
import { isNearBottom } from '../utils/chatScroll';
import { getReportText } from '../utils/reportLanguage';
import { extractStockCodesFromMessage } from '../utils/chatStockCode';
import { findMatchingStockCode, includesStockCode, normalizeStockCode } from '../utils/stockCode';
import { useStockIndex } from '../hooks/useStockIndex';
import type { StockIndexItem } from '../types/stockIndex';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';

// Quick question examples shown on empty state
type ActiveStockContext = Pick<ChatFollowUpContext, 'stock_code' | 'stock_name'>;

type ActionProposalStatus = 'pending' | 'applying' | 'applied' | 'error' | 'cancelled';

// 这两个常量是后端契约的副本，必须同步改：
//   CHAT_IMAGE_MAX_BYTES ← api/v1/endpoints/agent.py 的 CHAT_IMAGE_MAX_BYTES
//   CHAT_IMAGE_MIME      ← src/services/image_stock_extractor.py 的 ALLOWED_MIME
// （agent.py 从那里 import 它，所以 MIME 的真身在服务层。）
// tests/chat_image_contract.test.ts 会直接读这两个 Python 源文件比对，漂移会让前端测试变红。
const CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024;
const CHAT_IMAGE_MIME = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

// 已发送图片查看器的缩放档位；放大用 CSS zoom（真正的布局缩放），所以放大后
// 外层容器能滚起来，滚动就等于平移，不需要额外的拖拽实现。
const IMAGE_VIEWER_ZOOM_MIN = 0.5;
const IMAGE_VIEWER_ZOOM_MAX = 4;
const IMAGE_VIEWER_ZOOM_STEP = 0.25;
const IMAGE_VIEWER_BUTTON_CLASS =
  'inline-flex h-9 items-center justify-center rounded-xl border border-border/70 bg-card/80 px-3 text-xs font-medium text-secondary-text transition-colors hover:bg-hover hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40';

const QUICK_QUESTIONS: Array<{
  label: string;
  skill: string;
  stockContext?: ActiveStockContext;
}> = [
  { label: '用缠论分析茅台', skill: 'chan_theory', stockContext: { stock_code: '600519', stock_name: '贵州茅台' } },
  { label: '波浪理论看宁德时代', skill: 'wave_theory', stockContext: { stock_code: '300750', stock_name: '宁德时代' } },
  { label: '分析比亚迪趋势', skill: 'bull_trend', stockContext: { stock_code: '002594', stock_name: '比亚迪' } },
  { label: '用箱体震荡分析 A 股中芯国际 688981', skill: 'box_oscillation', stockContext: { stock_code: '688981', stock_name: '中芯国际' } },
  { label: '分析腾讯 hk00700', skill: 'bull_trend', stockContext: { stock_code: 'HK00700', stock_name: '腾讯控股' } },
  { label: '用情绪周期分析东方财富', skill: 'emotion_cycle', stockContext: { stock_code: '300059', stock_name: '东方财富' } },
];

// 单条 AI 消息的 Markdown 正文。包一层 memo：运行中的 SSE progress 事件不会改变
// 已完成消息的 content（store 里 messages 引用不变），因此这些昂贵的 Markdown
// 子树在进度事件期间跳过重建，避免整个对话区每次进度更新都重新解析渲染。
const MarkdownBody: React.FC<{ content: string }> = memo(({ content }) => (
  <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
));
MarkdownBody.displayName = 'MarkdownBody';

const MAX_SELECTED_SKILLS = 3;
const CONTEXT_COMPRESSION_CONFIG_KEY = 'AGENT_CONTEXT_COMPRESSION_ENABLED';
const STRONG_COMPARE_STOCK_MESSAGE_RE = /比较|对比|\bvs\b|和[^，。,.!?！？]{0,40}比/i;
const WEAK_COMPARE_STOCK_MESSAGE_RE = /差异(?!化)|区别|不同|相比|对照|比一比/;
const CHOICE_COMPARE_STOCK_MESSAGE_RE = /哪个|哪只|哪一个|谁更|更值得|更适合|怎么选|选哪|二选一/;
const LINKED_COMPARE_STOCK_MESSAGE_RE = /(?:和|与|跟|同)[^，。,.!?！？]{0,40}(?:差异(?!化)|区别|不同|相比|对照|比一比)/;
const SWITCH_STOCK_MESSAGE_RE = /换成|改看|分析|看看|研究|诊断/;

type ActiveStockResolution = {
  context: ActiveStockContext;
  useForCurrentSend: boolean;
};

const resolveUniqueStockNameContext = (
  message: string,
  index: StockIndexItem[],
): ActiveStockContext | null => {
  const normalizedMessage = message.trim().toLocaleLowerCase();
  if (!normalizedMessage) return null;

  const matches = new Map<string, ActiveStockContext>();
  for (const item of index) {
    if (!item.active) continue;
    const terms = [item.nameZh, item.nameEn, ...(item.aliases || [])]
      .map((term) => term?.trim())
      .filter((term): term is string => Boolean(term))
      .filter((term) => /[\u3400-\u9fff]/.test(term) ? term.length >= 2 : term.length >= 3);
    if (!terms.some((term) => normalizedMessage.includes(term.toLocaleLowerCase()))) {
      continue;
    }
    const stockCode = normalizeStockCode(item.canonicalCode);
    matches.set(stockCode, { stock_code: stockCode, stock_name: item.nameZh || null });
  }

  return matches.size === 1 ? [...matches.values()][0] : null;
};

const getMessageSkillNames = (msg: Message): string[] => {
  if (msg.skillNames?.length) return msg.skillNames;
  if (msg.skillName) return [msg.skillName];
  if (msg.skills?.length) return msg.skills;
  if (msg.skill) return [msg.skill];
  return [];
};

const getMessageSkillLabel = (msg: Message): string => getMessageSkillNames(msg).join('、');

const isStageDoneSuccessful = (status?: string): boolean => {
  if (!status) return true;
  const normalized = status.trim().toLowerCase();
  return ['completed', 'success', 'succeeded', 'done'].includes(normalized);
};

// 兜底阶段名：后端每个阶段事件都会带用户可见的 message，这里只在事件缺字段时
// 使用。不能回显 step.stage —— 那是 technical / skill_xxx 之类的内部英文 id。
const STAGE_FALLBACK_LABEL = '阶段';
// 引导提示的自动消失时长，与其它页面的 toast 保持同一量级。
const INTRO_TOAST_DURATION_MS = 4500;

const getStageDoneLabel = (step: ProgressStep): string => {
  if (step.message) return step.message;
  if (isStageDoneSuccessful(step.status)) return `${STAGE_FALLBACK_LABEL}完成`;
  return `${STAGE_FALLBACK_LABEL}未完成`;
};

const getPipelineBudgetSkippedLabel = (step: ProgressStep): string => {
  if (step.message) return step.message;
  return `${STAGE_FALLBACK_LABEL}因剩余预算不足被跳过`;
};

const isCompareStockMessage = (
  message: string,
  stockCodes: string[],
  currentStockCode?: string | null,
): boolean => {
  if (STRONG_COMPARE_STOCK_MESSAGE_RE.test(message)) {
    return true;
  }
  const current = currentStockCode ? normalizeStockCode(currentStockCode) : null;
  const newStockCodes = current
    ? stockCodes.filter((code) => code !== current)
    : stockCodes;
  if (newStockCodes.length >= 2) {
    return true;
  }
  if (CHOICE_COMPARE_STOCK_MESSAGE_RE.test(message) && stockCodes.length >= 2) {
    return true;
  }
  if (!WEAK_COMPARE_STOCK_MESSAGE_RE.test(message)) {
    return false;
  }
  if (stockCodes.length >= 2) {
    return true;
  }
  if (!currentStockCode) {
    return false;
  }
  const hasNewStock = stockCodes.some((code) => code !== current);
  return hasNewStock && LINKED_COMPARE_STOCK_MESSAGE_RE.test(message);
};

const resolveActiveStockContextFromMessage = (
  message: string,
  currentContext: ActiveStockContext | null,
): ActiveStockResolution | null => {
  const stockCodes = extractStockCodesFromMessage(message);
  const stockCode = stockCodes[0] ?? null;
  if (!stockCode) {
    return null;
  }

  const isCompare = isCompareStockMessage(message, stockCodes, currentContext?.stock_code);
  const isSwitch = SWITCH_STOCK_MESSAGE_RE.test(message);
  const currentStockCode = currentContext?.stock_code
    ? normalizeStockCode(currentContext.stock_code)
    : null;
  const newStockCodes = currentStockCode
    ? stockCodes.filter((code) => code !== currentStockCode)
    : stockCodes;
  // Explicit switches can mention the old stock; use the single new code when present.
  const targetStockCode = isSwitch && newStockCodes.length === 1
    ? newStockCodes[0]
    : stockCode;
  const isDifferentStock = currentStockCode !== targetStockCode;

  // Compare messages and implicit follow-ups must not rewrite the active stock context.
  if (isCompare || (currentContext && !isSwitch)) {
    return null;
  }

  return {
    context: {
      stock_code: targetStockCode,
      stock_name: currentContext && !isDifferentStock
        ? currentContext.stock_name
        : null,
    },
    // Only explicit switches should affect the context sent with the current request.
    useForCurrentSend: isSwitch && isDifferentStock,
  };
};

// 带图那一轮持久化的用户消息**就是**注入后的文本（`【图片内容】…【用户问题】…`，哨兵由后端
// chat_image_context._render_block 固定在最前）。刷新/切会话后从库里还原时，不能把整段当成
// 用户自己敲的话，也不能拿块里列的代码去推断活跃标的（设计 §9.1）——哨兵是后端契约的副本。
const IMAGE_BLOCK_SENTINEL = '【图片内容】';
const INJECTED_QUESTION_MARKER = '【用户问题】';

/** 注入块里用户真正打过的那部分：`【用户问题】` 之后（用户没打字时块里没有这一行）。 */
const getUserAuthoredText = (content: string): string => {
  if (!content.startsWith(IMAGE_BLOCK_SENTINEL)) {
    return content;
  }
  const markerIndex = content.indexOf(INJECTED_QUESTION_MARKER);
  return markerIndex === -1 ? '' : content.slice(markerIndex + INJECTED_QUESTION_MARKER.length);
};

const restoreActiveStockContextFromMessages = (messages: Message[]): ActiveStockContext | null => {
  let restoredContext: ActiveStockContext | null = null;
  for (const message of messages) {
    if (message.role !== 'user') {
      continue;
    }
    // 只看用户那部分：块里列出 ≥2 个代码时，整段解析会让会话在刷新后静默切进「对比模式」。
    const resolution = resolveActiveStockContextFromMessage(getUserAuthoredText(message.content), restoredContext);
    if (resolution) {
      restoredContext = resolution.context;
    }
  }
  return restoredContext;
};

const ChatPage: React.FC = () => {
  const { t } = useUiLanguage();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [input, setInput] = useState('');
  const [pendingImage, setPendingImage] = useState<{ dataUrl: string; base64: string; mime: string } | null>(null);
  const [readingImage, setReadingImage] = useState(false);
  // 被点开查看的已发送图片（dataUrl）；null 表示查看器关着。
  const [viewImage, setViewImage] = useState<string | null>(null);
  // 查看器里的缩放倍率与旋转角（度）。每次打开都从 1 / 0 重新开始。
  const [viewZoom, setViewZoom] = useState(1);
  const [viewRotation, setViewRotation] = useState(0);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [defaultSkillIds, setDefaultSkillIds] = useState<string[]>([]);
  const [showSkillDesc, setShowSkillDesc] = useState<string | null>(null);
  const [mobileSkillPickerOpen, setMobileSkillPickerOpen] = useState(false);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [expandedImageBlocks, setExpandedImageBlocks] = useState<Set<string>>(new Set());
  // 待确认删除的历史对话（会话 id + 标题，标题用于确认框里点名）。null 表示确认框关着。
  const [pendingDeleteSession, setPendingDeleteSession] = useState<{ id: string; title: string } | null>(null);
  // 删除失败的具体原因（服务端拒绝、网络错误等）；成功即清空。
  const [deleteSessionError, setDeleteSessionError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [isFollowUpContextLoading, setIsFollowUpContextLoading] = useState(false);
  const [sendToast, setSendToast] = useState<{
    type: 'success' | 'error';
    /* 覆盖默认标题（默认是「发送成功」/「发送失败」）。图片替换之类的提示不是发送结果，
       沿用默认标题会自相矛盾，所以这类提示自带标题。 */
    title?: string;
    message: string;
    durationMs: number;
  } | null>(null);
  const [introToastVisible, setIntroToastVisible] = useState(false);
  const introToastShownRef = useRef(false);
  const [contextCompressionEnabled, setContextCompressionEnabled] = useState(false);
  const [contextCompressionLoaded, setContextCompressionLoaded] = useState(false);
  const [contextCompressionSaving, setContextCompressionSaving] = useState(false);
  const [contextCompressionConfigVersion, setContextCompressionConfigVersion] = useState('');
  const [contextCompressionMaskToken, setContextCompressionMaskToken] = useState('******');
  const [contextCompressionError, setContextCompressionError] = useState<string | null>(null);
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [sessionMessagesLoading, setSessionMessagesLoading] = useState(false);
  const [watchlistCodes, setWatchlistCodes] = useState<string[]>([]);
  // 自选列表没读到时不能把 codes=[] 当成「不在自选里」这个结论展示给用户。
  const [watchlistLoadFailed, setWatchlistLoadFailed] = useState(false);
  const [isWatchlistActioning, setIsWatchlistActioning] = useState(false);
  const [watchlistMessage, setWatchlistMessage] = useState<string | null>(null);
  const [activeStockCode, setActiveStockCode] = useState<string | null>(null);
  const [activeStockContext, setActiveStockContext] = useState<ActiveStockContext | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatusResponse | null>(null);
  const [agentStatusError, setAgentStatusError] = useState<string | null>(null);
  const [agentStatusChecking, setAgentStatusChecking] = useState(true);
  // 一条助手消息可以带多张卡片，因此状态按卡片键（`${msg.id}#${index}`）记，不能只按 msg.id。
  const [actionProposalStatus, setActionProposalStatus] = useState<Record<string, ActionProposalStatus>>({});
  // 提交失败的具体原因（服务端拒绝的原因、网络错误等），按卡片键记；没有解析结果时用通用文案。
  const [actionProposalError, setActionProposalError] = useState<Record<string, string>>({});
  const { index: stockIndex } = useStockIndex(
    agentStatus?.backend === 'codex_app_server',
  );
  const watchlistMessageTimerRef = useRef<number | null>(null);
  const copyResetTimerRef = useRef<Partial<Record<string, number>>>({});
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isMountedRef = useRef(true);
  const followUpHydrationTokenRef = useRef(0);
  const followUpContextRef = useRef<ChatFollowUpContext | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const pendingScrollBehaviorRef = useRef<ScrollBehavior>('auto');
  const agentStatusRequestIdRef = useRef(0);

  // Get localized text (default to Chinese)
  const text = getReportText('zh');

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = copyResetTimerRef.current;
    return () => {
      Object.values(timers).forEach((timerId) => {
        if (timerId !== undefined) {
          window.clearTimeout(timerId);
        }
      });
    };
  }, []);

  // Set page title
  useEffect(() => {
    document.title = t('chat.pageTitle');
  }, [t]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const codes = await systemConfigApi.getWatchlist();
      if (isMountedRef.current) {
        setWatchlistCodes(codes);
        setWatchlistLoadFailed(false);
      }
    } catch {
      // 静默失败会让「加入自选 / 从自选删除」变成一个没有依据的断言：codes 还是空数组，
      // 按钮就按「不在自选里」渲染，点击也按这个错误前提去写。这里改成显式失败态。
      if (isMountedRef.current) {
        setWatchlistLoadFailed(true);
      }
    }
  }, []);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  const stockInWatchlist = useCallback(
    (stockCode: string) => includesStockCode(watchlistCodes, stockCode),
    [watchlistCodes],
  );

  const handleToggleWatchlist = useCallback(
    async (stockCode: string) => {
      if (!stockCode || isWatchlistActioning) return;
      setIsWatchlistActioning(true);
      setWatchlistMessage(null);
      try {
        const existingStockCode = findMatchingStockCode(watchlistCodes, stockCode);
        if (existingStockCode) {
          const codes = await systemConfigApi.removeFromWatchlist(existingStockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(t('chat.removedFromWatchlist').replace('{code}', stockCode));
          }
        } else {
          const codes = await systemConfigApi.addToWatchlist(stockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(t('chat.addedToWatchlist').replace('{code}', stockCode));
          }
        }
      } catch {
        if (isMountedRef.current) {
          setWatchlistMessage(t('chat.watchlistActionFailed'));
        }
      } finally {
        if (isMountedRef.current) {
          setIsWatchlistActioning(false);
          if (watchlistMessageTimerRef.current !== null) {
            window.clearTimeout(watchlistMessageTimerRef.current);
          }
          watchlistMessageTimerRef.current = window.setTimeout(() => {
            if (isMountedRef.current) {
              setWatchlistMessage(null);
            }
          }, 3000);
        }
      }
    },
    // t 必须进依赖：回调里三处提示文案都走 t()，漏掉它会让切换语言后仍弹上一个语言的提示。
    [isWatchlistActioning, watchlistCodes, t],
  );

  const handleApplyActionProposal = useCallback(
    async (cardKey: string, proposal: ActionProposal) => {
      setActionProposalStatus((s) => ({ ...s, [cardKey]: 'applying' }));
      try {
        await applyActionProposal(proposal);
        if (isMountedRef.current) {
          setActionProposalStatus((s) => ({ ...s, [cardKey]: 'applied' }));
        }
        // 自选类提案改的是本页顶部那个「加入自选 / 从自选删除」按钮的依据（watchlistCodes），
        // 它只在挂载与手动切换时更新，不重载的话确认完卡片按钮仍按旧快照渲染成相反的意思。
        // 只对自选两个 kind 重取：交易/告警不影响这份状态，多打一次配置接口没有理由。
        if (proposal.kind === 'watchlist_add' || proposal.kind === 'watchlist_remove') {
          await loadWatchlist();
        }
      } catch (err) {
        console.error('[action proposal] apply failed', err);
        if (isMountedRef.current) {
          setActionProposalStatus((s) => ({ ...s, [cardKey]: 'error' }));
          setActionProposalError((s) => ({ ...s, [cardKey]: getParsedApiError(err).message }));
        }
      }
    },
    [loadWatchlist],
  );

  const handleCancelActionProposal = useCallback((cardKey: string) => {
    setActionProposalStatus((s) => ({ ...s, [cardKey]: 'cancelled' }));
  }, []);

  const {
    messages,
    selectedSkillIds: sessionSelectedSkillIds,
    loading,
    progressSteps,
    sessionId,
    sessions,
    sessionsLoading,
    chatError,
    stopping,
    terminalStatus,
    stopError,
    setSelectedSkillIds,
    loadSessions,
    loadInitialSession,
    switchSession,
    stopStream,
    startStream,
    clearCompletionBadge,
  } = useAgentChatStore();
  const selectedSkillIds = sessionSelectedSkillIds ?? defaultSkillIds;

  useEffect(() => {
    if (activeStockContext || messages.length === 0) {
      return;
    }
    const restoredContext = restoreActiveStockContextFromMessages(messages);
    if (!restoredContext) {
      return;
    }
    setActiveStockContext(restoredContext);
    setActiveStockCode(restoredContext.stock_code);
  }, [activeStockContext, messages, sessionId]);

  const syncScrollState = useCallback(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const nearBottom = isNearBottom({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight,
      scrollHeight: viewport.scrollHeight,
    });
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToBottom((prev) => (nearBottom ? false : prev));
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const requestScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    shouldStickToBottomRef.current = true;
    pendingScrollBehaviorRef.current = behavior;
    setShowJumpToBottom(false);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    syncScrollState();
  }, [syncScrollState]);

  useEffect(() => {
    syncScrollState();
  }, [syncScrollState, sessionId]);

  // 打开页面/切换会话时瞬时定位到最新消息，避免从历史顶部平滑滚下去。
  // 放在自动滚动 effect 之前，把 behavior 覆盖为 auto，覆盖挂载/清空渲染时被置为 smooth 的值。
  const lastJumpedSessionRef = useRef<string | null>(null);
  useEffect(() => {
    if (lastJumpedSessionRef.current === sessionId) {
      return;
    }
    if (messages.length === 0) {
      return;
    }
    lastJumpedSessionRef.current = sessionId;
    requestScrollToBottom('auto');
    scrollToBottom('auto');
  }, [sessionId, messages.length, requestScrollToBottom, scrollToBottom]);

  useEffect(() => {
    const behavior = pendingScrollBehaviorRef.current;
    const shouldAutoScroll = shouldStickToBottomRef.current;
    if (!shouldAutoScroll) {
      if (messages.length > 0 || progressSteps.length > 0 || loading) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, scrollToBottom]);

  useEffect(() => {
    if (!loading) {
      pendingScrollBehaviorRef.current = 'smooth';
    }
  }, [loading]);

  useEffect(() => {
    clearCompletionBadge();
  }, [clearCompletionBadge]);

  useEffect(() => {
    setSessionMessagesLoading(true);
    void Promise.resolve(loadInitialSession()).finally(() => {
      setSessionMessagesLoading(false);
    });
  }, [loadInitialSession]);

  useEffect(() => {
    agentApi.getSkills()
      .then((res) => {
        setSkills(res.skills);
        const defaultId =
          res.default_skill_id ||
          res.skills[0]?.id ||
          '';
        setDefaultSkillIds(defaultId ? [defaultId] : []);
      })
      .catch((error) => {
        console.error('Failed to load chat skills:', error);
      });
  }, []);

  const loadAgentStatus = useCallback(async () => {
    const requestId = agentStatusRequestIdRef.current + 1;
    agentStatusRequestIdRef.current = requestId;
    setAgentStatusChecking(true);
    try {
      const status = await agentApi.getStatus();
      if (!isMountedRef.current || agentStatusRequestIdRef.current !== requestId) return;
      setAgentStatus(status);
      setAgentStatusError(null);
    } catch (error: unknown) {
      if (!isMountedRef.current || agentStatusRequestIdRef.current !== requestId) return;
      setAgentStatus(null);
      setAgentStatusError(getParsedApiError(error).message);
    } finally {
      if (isMountedRef.current && agentStatusRequestIdRef.current === requestId) {
        setAgentStatusChecking(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadAgentStatus();
  }, [loadAgentStatus]);

  useEffect(() => {
    let active = true;

    void systemConfigApi.getConfig(false)
      .then((config) => {
        if (!active) {
          return;
        }
        const enabledItem = config.items.find((item) => item.key === CONTEXT_COMPRESSION_CONFIG_KEY);
        setContextCompressionEnabled(String(enabledItem?.value ?? '').trim().toLowerCase() === 'true');
        setContextCompressionConfigVersion(config.configVersion);
        setContextCompressionMaskToken(config.maskToken || '******');
        setContextCompressionLoaded(true);
        setContextCompressionError(null);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        const parsed = getParsedApiError(error);
        setContextCompressionLoaded(false);
        setContextCompressionError(parsed.message || '无法读取上下文压缩配置');
        console.error('Failed to load context compression setting:', error);
      });

    return () => {
      active = false;
    };
  }, []);

  const updateContextCompressionEnabled = useCallback(
    async (nextEnabled: boolean) => {
      if (!contextCompressionLoaded || contextCompressionSaving) {
        return;
      }

      const previousEnabled = contextCompressionEnabled;
      setContextCompressionEnabled(nextEnabled);
      setContextCompressionSaving(true);
      setContextCompressionError(null);

      try {
        const result = await systemConfigApi.update({
          configVersion: contextCompressionConfigVersion,
          maskToken: contextCompressionMaskToken,
          reloadNow: true,
          items: [
            {
              key: CONTEXT_COMPRESSION_CONFIG_KEY,
              value: nextEnabled ? 'true' : 'false',
            },
          ],
        });
        setContextCompressionConfigVersion(result.configVersion || contextCompressionConfigVersion);
      } catch (error) {
        const parsed = getParsedApiError(error);
        setContextCompressionEnabled(previousEnabled);
        setContextCompressionError(parsed.message || '上下文压缩设置保存失败');
      } finally {
        setContextCompressionSaving(false);
      }
    },
    [
      contextCompressionConfigVersion,
      contextCompressionEnabled,
      contextCompressionLoaded,
      contextCompressionMaskToken,
      contextCompressionSaving,
    ],
  );

  const availableSkillIds = new Set(skills.map((skill) => skill.id));
  const quickQuestions = QUICK_QUESTIONS.filter((question) => availableSkillIds.size === 0 || availableSkillIds.has(question.skill));
  const selectedSkillIdSet = new Set(selectedSkillIds);
  const skillLimitReached = selectedSkillIds.length >= MAX_SELECTED_SKILLS;
  const agentConfirmedUnavailable = Boolean(agentStatus && !agentStatus.available);
  const agentAvailable = Boolean(agentStatus?.available) && !agentStatusChecking;
  const agentUnavailableMessage = agentStatus?.errorCode === 'agent_mode_disabled'
    ? t('chat.agentModeDisabled')
    : agentStatus?.errorCode === 'platform_unsupported'
      ? t('chat.agentPlatformUnsupported')
      : agentStatus?.backend === 'codex_app_server'
        ? t('chat.codexUnavailableMessage')
        : t('chat.defaultUnavailableMessage');
  const agentUnavailableError = agentConfirmedUnavailable
    ? createParsedApiError({
        title: t('chat.agentBackendUnavailableTitle'),
        message: agentUnavailableMessage,
        rawMessage: `${agentStatus?.errorCode || 'capability_unsupported'}: ${agentStatus?.message || ''}`,
        category: 'upstream_network',
      })
    : null;

  const getSkillNames = useCallback(
    (skillIds: string[]) => skillIds.map((id) => skills.find((s) => s.id === id)?.name || id),
    [skills],
  );

  const normalizeSelectedSkillIds = useCallback((skillIds: string[]) => {
    const normalized: string[] = [];
    for (const skillId of skillIds) {
      const cleaned = skillId.trim();
      if (cleaned && !normalized.includes(cleaned)) {
        normalized.push(cleaned);
      }
    }
    return normalized.slice(0, MAX_SELECTED_SKILLS);
  }, []);

  const toggleSkillSelection = useCallback((skillId: string) => {
    if (selectedSkillIds.includes(skillId)) {
      setSelectedSkillIds(selectedSkillIds.filter((id) => id !== skillId));
      return;
    }
    if (selectedSkillIds.length < MAX_SELECTED_SKILLS) {
      setSelectedSkillIds([...selectedSkillIds, skillId]);
    }
  }, [selectedSkillIds, setSelectedSkillIds]);

  const clearAllCopyTimers = useCallback(() => {
    Object.values(copyResetTimerRef.current).forEach((timerId) => {
      if (timerId !== undefined) {
        window.clearTimeout(timerId);
      }
    });
    copyResetTimerRef.current = {};
  }, []);

  const handleStartNewChat = useCallback(() => {
    clearAllCopyTimers();
    followUpContextRef.current = null;
    setActiveStockContext(null);
    setActiveStockCode(null);
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    setSidebarOpen(false);
  }, [requestScrollToBottom, clearAllCopyTimers]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    if (targetSessionId === sessionId) {
      setSidebarOpen(false);
      return;
    }
    clearAllCopyTimers();
    followUpContextRef.current = null;
    setActiveStockContext(null);
    setActiveStockCode(null);
    requestScrollToBottom('auto');
    setSessionMessagesLoading(true);
    void Promise.resolve(switchSession(targetSessionId)).finally(() => {
      setSessionMessagesLoading(false);
    });
    setSidebarOpen(false);
  }, [requestScrollToBottom, sessionId, switchSession, clearAllCopyTimers]);

  /**
   * 提交一条历史对话的删除。删除是不可逆的，所以入口只有确认框的「删除」按钮：
   * 确认后直接提交，不再有撤销窗口（此前的 6 秒延迟删除会让「已宣告删除」与
   * 「实际还没删」两件事在界面上不一致，且被下一次删除顶替时会静默丢失）。
   */
  const confirmDeleteSession = useCallback(() => {
    const target = pendingDeleteSession;
    setPendingDeleteSession(null);
    if (!target) {
      return;
    }
    agentApi
      .deleteChatSession(target.id)
      .then(() => {
        if (!isMountedRef.current) {
          return;
        }
        setDeleteSessionError(null);
        loadSessions();
        // 删掉的正是当前打开的会话时，右侧对话区不能留着一条已经不存在的会话。
        if (target.id === sessionId) {
          handleStartNewChat();
        }
      })
      .catch((error: unknown) => {
        console.error('Failed to delete chat session:', error);
        if (isMountedRef.current) {
          setDeleteSessionError(getParsedApiError(error).message);
        }
      });
  }, [pendingDeleteSession, sessionId, loadSessions, handleStartNewChat]);

  // Handle follow-up from report page: ?stock=600519&name=贵州茅台&recordId=xxx
  useEffect(() => {
    const stock = sanitizeFollowUpStockCode(searchParams.get('stock'));
    const name = sanitizeFollowUpStockName(searchParams.get('name'));
    const recordId = parseFollowUpRecordId(searchParams.get('recordId'));

    if (!stock) {
      setSearchParams({}, { replace: true });
      return;
    }

    const hydrationToken = ++followUpHydrationTokenRef.current;
    setInput(buildFollowUpPrompt(stock, name));
    setActiveStockCode(stock);
    setActiveStockContext({
      stock_code: stock,
      stock_name: name,
    });
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };
    if (recordId !== undefined) {
      setIsFollowUpContextLoading(true);
    }
    void resolveChatFollowUpContext({
      stockCode: stock,
      stockName: name,
      recordId,
    }).then((context) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken) {
        return;
      }
      followUpContextRef.current = context;
    }).finally(() => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setIsFollowUpContextLoading(false);
      }
    });
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  const showIntroToast = useCallback(() => {
    if (introToastShownRef.current) return;
    introToastShownRef.current = true;
    setIntroToastVisible(true);
  }, []);

  const dismissIntroToast = useCallback(() => {
    setIntroToastVisible(false);
  }, []);

  const handleSend = useCallback(
    async (
      overrideMessage?: string,
      overrideSkillIds?: string[],
      overrideStockContext?: ActiveStockContext,
    ) => {
      const msgText = (overrideMessage ?? input).trim();
      // 只贴图不打字是受支持的一轮（后端为此把 message 改成可选），所以不能在这里用空文本拦掉。
      if ((!msgText && !pendingImage) || loading || !agentAvailable || !agentStatus) return;
      showIntroToast();
      if (overrideMessage !== undefined) {
        setInput(msgText);
      }
      const requestedSkillIds = overrideSkillIds ?? sessionSelectedSkillIds;
      const usedSkillIds = normalizeSelectedSkillIds(
        requestedSkillIds ?? selectedSkillIds,
      );
      const usedSkillNames = usedSkillIds.length > 0 ? getSkillNames(usedSkillIds) : ['通用'];
      const codexStockContext = agentStatus?.backend === 'codex_app_server'
        ? overrideStockContext
        : undefined;

      let nextActiveStockContext = codexStockContext ?? activeStockContext;
      let useActiveContextForThisSend = Boolean(codexStockContext);
      const stockResolution = codexStockContext
        ? null
        : resolveActiveStockContextFromMessage(msgText, activeStockContext);
      if (stockResolution) {
        nextActiveStockContext = stockResolution.context;
        useActiveContextForThisSend = stockResolution.useForCurrentSend;
      } else if (
        agentStatus?.backend === 'codex_app_server'
        && !codexStockContext
        && (!nextActiveStockContext || SWITCH_STOCK_MESSAGE_RE.test(msgText))
      ) {
        const nameContext = resolveUniqueStockNameContext(msgText, stockIndex);
        if (nameContext) {
          nextActiveStockContext = nameContext;
          useActiveContextForThisSend = true;
        }
      }
      const contextForSend = useActiveContextForThisSend
        ? nextActiveStockContext
        : followUpContextRef.current ?? nextActiveStockContext ?? undefined;

      const payload = {
        message: msgText,
        session_id: sessionId,
        ...(requestedSkillIds !== null
          ? { skills: normalizeSelectedSkillIds(requestedSkillIds) }
          : {}),
        // 有图才带这两个字段（后端要求成对）
        ...(pendingImage ? { image_base64: pendingImage.base64, image_mime: pendingImage.mime } : {}),
        context: contextForSend ?? undefined,
      };
      // 后端在流开始之前完成读图，所以 accepted 到达即意味着读图阶段结束。
      if (pendingImage) setReadingImage(true);
      await startStream(payload, {
        skillNames: usedSkillNames,
        skillName: usedSkillNames.join('、'),
        // 只走 meta：后端没有这个字段，混进 payload 会被 Pydantic 静默忽略。
        imageDataUrl: pendingImage?.dataUrl,
        onAccepted: () => {
          setReadingImage(false);
          followUpHydrationTokenRef.current += 1;
          followUpContextRef.current = null;
          setIsFollowUpContextLoading(false);
          if (nextActiveStockContext) {
            setActiveStockContext(nextActiveStockContext);
            setActiveStockCode(nextActiveStockContext.stock_code);
          }
          setInput('');
          // chip 只代表「还没发出去的图」。这一轮已被后端接受（消息气泡里也已经带着这张图），
          // 再留一个缩略图在 composer 里会被读成「还排着队」，所以和清空输入框一起清掉。
          setPendingImage(null);
          setMobileSkillPickerOpen(false);
          requestScrollToBottom('smooth');
        },
      });
    },
    [activeStockContext, agentAvailable, agentStatus, getSkillNames, input, loading, normalizeSelectedSkillIds, pendingImage, requestScrollToBottom, selectedSkillIds, sessionId, sessionSelectedSkillIds, showIntroToast, startStream, stockIndex],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickQuestion = (q: (typeof QUICK_QUESTIONS)[0]) => {
    setSelectedSkillIds([q.skill]);
    handleSend(q.label, [q.skill], q.stockContext);
  };

  // 自动消失的计时交给 AutoDismissToast：同一提示换新时它会重新计时。
  const showSendFeedback = useCallback(
    (nextToast: { type: 'success' | 'error'; title?: string; message: string }, durationMs: number) => {
      setSendToast({ ...nextToast, durationMs });
    },
    [],
  );

  const dismissSendToast = useCallback(() => {
    setSendToast(null);
  }, []);

  // 图片只在这一层预览、暂存在待发送状态里；随 payload 发出去是发送路径的事。
  const handleImageFile = useCallback((file: File, ignoredCount = 0) => {
    if (!CHAT_IMAGE_MIME.includes(file.type)) {
      showSendFeedback({ type: 'error', message: t('chat.imageTypeUnsupported', { type: file.type || '(未知)' }) }, 4000);
      return;
    }
    if (file.size > CHAT_IMAGE_MAX_BYTES) {
      showSendFeedback({ type: 'error', message: t('chat.imageTooLarge', { limit: CHAT_IMAGE_MAX_BYTES / 1024 / 1024 }) }, 4000);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const base64 = dataUrl.split(',', 2)[1] || '';
      const replacedOld = pendingImage !== null;
      setPendingImage({ dataUrl, base64, mime: file.type });
      // 每轮只带 1 张，所以「替换」和「多选丢弃」都得说一声，不能静默。同一 tick 里
      // 只有最后一条提示会留下，因此多选丢弃优先——它是更需要被解释的那个。
      if (ignoredCount > 0) {
        showSendFeedback({ type: 'error', title: t('chat.someImagesIgnoredTitle'), message: t('chat.someImagesIgnoredMessage', { count: ignoredCount }) }, 4000);
      } else if (replacedOld) {
        showSendFeedback({ type: 'success', title: t('chat.imageReplacedTitle'), message: t('chat.imageReplacedMessage') }, 3000);
      }
    };
    reader.readAsDataURL(file);
  }, [pendingImage, showSendFeedback, t]);

  // 打开查看器时先把缩放/旋转复位，免得上一张的视角带到下一张。
  const openImageViewer = useCallback((dataUrl: string) => {
    setViewZoom(1);
    setViewRotation(0);
    setViewImage(dataUrl);
  }, []);

  // 拖放：与附件按钮、隐藏 input 同一套 gate —— 流式期间或 agent 不可用时不能换图，
  // 否则会挂上一个本轮发不出去、却静默作用于下一轮的 chip。取 files[0] 的写法与
  // IntelligentImport.tsx 的 onDrop 相同（那边同样先判 disabled/isLoading 再取文件），
  // 剩下的张数交给 handleImageFile 提示；MIME 也由它按白名单校验。
  const handleImageDrop = useCallback((e: React.DragEvent) => {
    // 先拦默认行为：落点不在 composer 上时，浏览器会直接打开文件、把 SPA 卸载掉。
    e.preventDefault();
    if (loading || !agentAvailable) return;
    const files = e.dataTransfer?.files;
    const f = files?.[0];
    if (f) handleImageFile(f, (files?.length ?? 1) - 1);
  }, [agentAvailable, handleImageFile, loading]);

  // 拖到 composer 之外（消息列表、侧栏）松手会命中浏览器默认行为：直接打开那个文件，
  // SPA 被卸载、已经输入的内容随之丢失。全应用没有 window 级兜底，这里补上。
  // 只 preventDefault，不 stopPropagation —— composer 自己的 onDrop 仍会收到文件。
  useEffect(() => {
    const preventDefaultDrag = (e: DragEvent) => e.preventDefault();
    window.addEventListener('dragover', preventDefaultDrag);
    window.addEventListener('drop', preventDefaultDrag);
    return () => {
      window.removeEventListener('dragover', preventDefaultDrag);
      window.removeEventListener('drop', preventDefaultDrag);
    };
  }, []);

  // 横幅还在 ⟹ 有请求在飞：成功路径在 accepted 那一刻就清了，而那时 loading 仍为 true，
  // 所以这条 effect 不会抢跑（accepted 早于流结束）。
  // 反过来，loading 转 false 说明这一轮已经结束 —— 读图失败、点停止、切会话/新建对话
  // 这三条路径都不一定设 chatError（abort 在 store 里是静默的），只表现为 loading 转 false，
  // 所以横幅必须跟着消失，否则会永久挂在输入框上方。
  useEffect(() => {
    if (!loading) setReadingImage(false);
  }, [loading]);

  const toggleThinking = (msgId: string) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  const toggleImageBlock = (msgId: string) => {
    setExpandedImageBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  // 还原后的带图轮次，内容就是注入块。默认折叠成一行，否则用户会看到一整段
  // `【图片内容】…【用户问题】…`，像是自己敲的。展开才给看原文。
  const renderUserMessageContent = (msg: Message) => {
    const lines = msg.content
      .split('\n')
      .map((line, i) => (
        <p key={i} className="mb-1 last:mb-0 leading-relaxed">
          {line || '\u00A0'}
        </p>
      ));

    if (!msg.content.startsWith(IMAGE_BLOCK_SENTINEL)) {
      return lines;
    }

    const isExpanded = expandedImageBlocks.has(msg.id);
    return (
      <>
        <button
          type="button"
          onClick={() => toggleImageBlock(msg.id)}
          aria-expanded={isExpanded}
          className="flex items-center gap-1.5 text-xs text-muted-text hover:text-secondary-text transition-colors"
        >
          <svg
            className={`w-3 h-3 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 5l7 7-7 7"
            />
          </svg>
          {t('chat.imageBlockCollapsed')}
        </button>
        {isExpanded && <div className="mt-2 animate-fade-in">{lines}</div>}
      </>
    );
  };

  const copyMessageToClipboard = async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessages((prev) => new Set(prev).add(msgId));
      const existingTimer = copyResetTimerRef.current[msgId];
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      copyResetTimerRef.current[msgId] = window.setTimeout(() => {
        setCopiedMessages((prev) => {
          const next = new Set(prev);
          next.delete(msgId);
          return next;
        });
        delete copyResetTimerRef.current[msgId];
      }, 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  const downloadMessageAsMarkdown = useCallback((msg: Message) => {
    const skillLabel = getMessageSkillLabel(msg);
    const heading = msg.role === 'user' ? '# 用户消息' : `# AI 回复${skillLabel ? ` · ${skillLabel}` : ''}`;
    const content = [heading, '', msg.content].join('\n');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${msg.role === 'user' ? 'user' : 'assistant'}-message-${msg.id}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, []);

  const getCurrentStage = (steps: ProgressStep[]): string => {
    if (steps.length === 0) return t('chat.connecting');
    const last = steps[steps.length - 1];
    if (last.type === 'thinking') return last.message || 'AI 正在思考...';
    if (last.type === 'tool_start')
      return `${last.display_name || last.tool}...`;
    if (last.type === 'tool_done')
      return `${last.display_name || last.tool} 完成`;
    if (last.type === 'stage_start')
      return last.message || `${STAGE_FALLBACK_LABEL}进行中...`;
    if (last.type === 'stage_done')
      return getStageDoneLabel(last);
    if (last.type === 'pipeline_timeout')
      return last.message || `${STAGE_FALLBACK_LABEL}超时`;
    if (last.type === 'pipeline_budget_skipped')
      return getPipelineBudgetSkippedLabel(last);
    if (last.type === 'generating')
      return last.message || '正在生成最终分析...';
    return '处理中...';
  };

  const renderThinkingBlock = (msg: Message) => {
    if (!msg.thinkingSteps || msg.thinkingSteps.length === 0) return null;
    const isExpanded = expandedThinking.has(msg.id);
    const toolSteps = msg.thinkingSteps.filter((s) => s.type === 'tool_done');
    const totalDuration = toolSteps.reduce(
      (sum, s) => sum + (s.duration || 0),
      0,
    );
    const summary = `${toolSteps.length} 个工具调用 · ${totalDuration.toFixed(1)}s`;

    return (
      <button
        onClick={() => toggleThinking(msg.id)}
        className="flex items-center gap-2 text-xs text-muted-text hover:text-secondary-text transition-colors mb-2 w-full text-left"
      >
        <svg
          className={`w-3 h-3 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className="flex items-center gap-1.5">
          <span className="opacity-60">{t('chat.thinking')}</span>
          <span className="text-muted-text/50">·</span>
          <span className="opacity-50">{summary}</span>
        </span>
      </button>
    );
  };

  const renderThinkingDetails = (steps: ProgressStep[]) => (
    <div className="mb-3 pl-5 border-l border-border/40 space-y-1.5 animate-fade-in">
      {steps.map((step, idx) => {
        let statusClass = 'chat-progress-item-muted';
        let iconClass = 'chat-progress-dot-muted';
        let text = '';
        if (step.type === 'thinking') {
          text = step.message || `第 ${step.step} 步：思考`;
          statusClass = 'chat-progress-item-thinking';
          iconClass = 'chat-progress-dot-thinking';
        } else if (step.type === 'tool_start') {
          text = `${step.display_name || step.tool}...`;
          statusClass = 'chat-progress-item-tool';
          iconClass = 'chat-progress-dot-tool';
        } else if (step.type === 'tool_done') {
          text = `${step.display_name || step.tool} (${step.duration}s)`;
          statusClass = step.success ? 'chat-progress-item-success' : 'chat-progress-item-danger';
          iconClass = step.success ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
        } else if (step.type === 'stage_start') {
          text = step.message || `${STAGE_FALLBACK_LABEL}进行中...`;
          statusClass = 'chat-progress-item-thinking';
          iconClass = 'chat-progress-dot-thinking';
        } else if (step.type === 'stage_done') {
          const isSuccess = isStageDoneSuccessful(step.status);
          text = getStageDoneLabel(step);
          statusClass = isSuccess ? 'chat-progress-item-success' : 'chat-progress-item-danger';
          iconClass = isSuccess ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
        } else if (step.type === 'pipeline_timeout') {
          text = step.message || `${STAGE_FALLBACK_LABEL}超时`;
          statusClass = 'chat-progress-item-danger';
          iconClass = 'chat-progress-dot-danger';
        } else if (step.type === 'pipeline_budget_skipped') {
          text = getPipelineBudgetSkippedLabel(step);
          statusClass = 'chat-progress-item-muted';
          iconClass = 'chat-progress-dot-muted';
        } else if (step.type === 'generating') {
          text = step.message || '生成分析';
          statusClass = 'chat-progress-item-generating';
          iconClass = 'chat-progress-dot-generating';
        } else {
          text = step.message || step.type;
        }
        return (
          <div
            key={idx}
            className={cn('chat-progress-item', statusClass)}
          >
            <span className={cn('chat-progress-dot', iconClass)} />
            <span className="leading-relaxed">{text}</span>
          </div>
        );
      })}
    </div>
  );

  const renderActionProposalCard = (msg: Message, proposal: ActionProposal, index: number) => {
    const cardKey = `${msg.id}#${index}`;
    const status = actionProposalStatus[cardKey] || 'pending';
    if (status === 'cancelled') return null;

    const applied = status === 'applied';
    const applying = status === 'applying';
    const failed = status === 'error';
    const failureMessage = actionProposalError[cardKey] ?? t('chat.actionProposalFailed');

    return (
      <div key={cardKey} className="mb-3 mt-2">
        <InlineAlert
          variant={applied ? 'success' : failed ? 'danger' : 'info'}
          title={applied ? t('chat.actionProposalApplied') : t('chat.actionProposalTitle')}
          message={(
            <span className="flex flex-col gap-3">
              <span className="font-medium">{proposal.summary}</span>
              {failed && (
                <span className="text-xs">{failureMessage}</span>
              )}
              {!applied && (
                <span className="flex gap-2">
                  {/* 确认按钮在失败态复用为「重试」：它不可点会让用户彻底卡死——失败态下
                      再没有别的入口能改状态（取消也会让卡片消失），而 409 /「持仓账本正忙」
                      这类必须先改数据才能成功。所以失败态保留它，并把原因显示在上方。 */}
                  <Button
                    type="button"
                    size="sm"
                    variant="primary"
                    isLoading={applying}
                    disabled={applying}
                    loadingText={t('chat.actionProposalApplying')}
                    onClick={() => void handleApplyActionProposal(cardKey, proposal)}
                  >
                    {t('chat.actionProposalConfirm')}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger-subtle"
                    disabled={applying}
                    onClick={() => handleCancelActionProposal(cardKey)}
                  >
                    {t('chat.actionProposalCancel')}
                  </Button>
                </span>
              )}
            </span>
          )}
        />
      </div>
    );
  };

  const sidebarContent = (
    <>
      <div className="border-b border-subtle px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-foreground tracking-tight">
            <svg className="w-4 h-4 flex-shrink-0 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span className="truncate">{t('chat.historyTitle')}</span>
          </h2>
          <Button variant="ghost" size="sm" onClick={handleStartNewChat} aria-label={t('chat.newChatAria')}>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            {t('chat.newChat')}
          </Button>
        </div>
      </div>
      <ScrollArea testId="chat-session-list-scroll" viewportClassName="p-3">
        {sessionsLoading ? (
          <DashboardStateBlock
            loading
            compact
            title="加载对话中..."
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : sessions.length === 0 ? (
          <DashboardStateBlock
            compact
            title="暂无历史对话"
            description="开始提问后，这里会保留会话记录。"
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => {
              const isActive = s.session_id === sessionId;
              return (
                <ListItemRow
                  key={s.session_id}
                  wrapperClassName="home-history-item w-full min-w-0 flex-1"
                  buttonClassName={`w-full min-w-0 flex-1 text-left p-2.5 ${
                    isActive ? 'home-history-item-selected' : ''
                  }`}
                  ariaLabel={`切换到对话 ${s.title}`}
                  onClick={() => handleSwitchSession(s.session_id)}
                  pressed={isActive}
                  leading={(
                    <div
                      className={`w-1 h-8 rounded-full flex-shrink-0 ${
                        isActive ? 'bg-primary' : 'bg-subtle'
                      }`}
                      style={isActive ? { boxShadow: '0 0 10px hsl(var(--primary) / 0.4)' } : undefined}
                    />
                  )}
                  title={(
                    <span className="block w-full truncate text-sm font-semibold text-foreground tracking-tight">
                      {s.title}
                    </span>
                  )}
                  meta={(
                    <>
                      <span className="text-[11px] text-muted-text">
                        {s.message_count} 条对话
                      </span>
                      {s.last_active && (
                        <>
                          <span className="w-1 h-1 rounded-full bg-subtle-hover" />
                          <span className="text-[11px] text-muted-text">
                            {new Date(s.last_active).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
                          </span>
                        </>
                      )}
                    </>
                  )}
                  onDelete={() => setPendingDeleteSession({ id: s.session_id, title: s.title })}
                  deleteAriaLabel={`删除对话 ${s.title}`}
                />
              );
            })}
          </div>
        )}
      </ScrollArea>
    </>
  );

  // 图片查看器转到 90° / 270° 时，视觉上的宽高和布局盒是对调的，布局要跟着换一套。
  const rotated = viewRotation % 180 !== 0;

  const selectedSkillSummary = selectedSkillIds.length > 0
    ? getSkillNames(selectedSkillIds).join('、')
    : t('chat.generalAnalysis');

  // 页面级提示统一走全局右上角通道：状态在就弹出，状态清了（或页面卸载）自动撤下。
  // 这些都是持续型状态，不自动消失——带按钮的提示若几秒后自己没了，用户就再也点不到
  // 「去设置」，所以下面只有引导提示和发送结果带 `autoDismissMs`。
  useToastSync('chat-codex-limited', agentStatus?.backend === 'codex_app_server', () => ({
    variant: 'warning',
    title: t('chat.codexLimitedTitle'),
    message: t('chat.codexLimitedMessage'),
    action: (
      <Button variant="action-primary" size="sm" onClick={() => navigate('/settings?category=agent')}>
        {t('chat.codexChangeBackend')}
      </Button>
    ),
  }));
  // 引导提示几秒后自动消失，鼠标悬停或键盘焦点停留在其上时暂停计时。
  useToastSync('chat-intro', introToastVisible, () => ({
    variant: 'info',
    title: t(agentStatus?.backend === 'codex_app_server' ? 'chat.introCodex' : 'chat.introDefault'),
    message: t('chat.introMessage'),
    autoDismissMs: INTRO_TOAST_DURATION_MS,
    onDismiss: dismissIntroToast,
  }));
  useToastSync('chat-error', chatError, (err) => ({ error: err }));
  // 停止要说清楚「为什么还没停」。litellm 后端是协作式取消：正在跑的工具调用不会被中断，
  // 本轮只在它返回后才收尾（实测一次数据源扇出要 90 秒），这段时间里界面既不该报错，
  // 也不该让用户以为卡死了。Codex 后端是真的把进程杀掉，收尾很快，不需要这条说明。
  // 所以这里要求「已经读到后端，且不是 Codex」：后端还没读到时按 Codex 处理——这条说明
  // 只是解释为什么慢，晚几百毫秒再出现没有损失，而先弹一条随即被撤回反而会闪一下。
  useToastSync('chat-stop-waiting', stopping && !!agentStatus && agentStatus.backend !== 'codex_app_server', () => ({
    role: 'status',
    variant: 'info',
    message: t('chat.stopWaitingNotice'),
  }));
  useToastSync('chat-terminal-cancelled', terminalStatus === 'cancelled', () => ({
    role: 'status',
    variant: 'info',
    message: t('chat.analysisStopped'),
  }));
  useToastSync('chat-terminal-timeout', terminalStatus === 'timeout', () => ({
    role: 'status',
    variant: 'warning',
    message: t('chat.analysisTimedOut'),
  }));
  useToastSync('chat-stop-error', stopError, () => ({
    variant: 'warning',
    message: t('chat.stopRequestFailed'),
  }));
  // `agentUnavailableError` 是每次渲染新拼的对象，不能当状态信号——否则提示会被当成
  // 「换了一条新的」反复重弹、用户关不掉。用稳定的 `agentStatus` 表达「确认不可用」。
  useToastSync('chat-agent-unavailable', agentConfirmedUnavailable ? agentStatus : null, () => ({
    error: agentUnavailableError ?? undefined,
    action: (
      <>
        <Button variant="secondary" size="sm" onClick={() => navigate('/settings?category=agent')}>
          {t('chat.openAgentSettings')}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => void loadAgentStatus()}>
          {t('chat.recheckAgentStatus')}
        </Button>
      </>
    ),
  }));
  useToastSync('chat-agent-status-error', agentStatusError, () => ({
    variant: 'warning',
    title: t('chat.statusUnavailableTitle'),
    message: t('chat.statusUnavailableMessage'),
    action: (
      <Button variant="secondary" size="sm" onClick={() => void loadAgentStatus()}>
        {t('chat.recheckAgentStatus')}
      </Button>
    ),
  }));
  useToastSync('chat-follow-up-context-loading', isFollowUpContextLoading, () => ({
    variant: 'info',
    title: t('chat.followUpContextLoadingTitle'),
    message: t('chat.followUpContextLoadingMessage'),
  }));
  useToastSync('chat-compression-save-error', contextCompressionError, (message) => ({
    variant: 'danger',
    title: t('chat.compressionSaveFailedTitle'),
    message,
  }));
  // 发送结果几秒后自动消失；鼠标悬停或键盘焦点停留在其上时暂停计时。
  useToastSync('chat-send-toast', sendToast, (result) => ({
    variant: result.type === 'success' ? 'success' : 'danger',
    title: result.title ?? (result.type === 'success' ? t('chat.sendSuccess') : t('chat.sendFailed')),
    message: result.message,
    autoDismissMs: result.durationMs,
    onDismiss: dismissSendToast,
  }));
  // 删除失败必须说出来：确认框已经关掉、列表里的那条也还在，用户唯一能看到的差别
  // 就是它没消失。只写 console.error 等于把这次失败藏起来。
  useToastSync('chat-delete-error', deleteSessionError, (message) => ({
    variant: 'danger',
    title: t('common.deleteFailed'),
    message,
  }));

  return (
    <>
    <div
      data-testid="chat-workspace"
      className="flex h-[calc(100vh-4rem)] w-full min-w-0 flex-col overflow-hidden px-3 pb-4 sm:h-[calc(100vh-4.5rem)] md:px-4"
    >
        {/* Top action bar: mobile menu left, external export/send buttons right */}
        <div className="mb-4 flex flex-shrink-0 flex-wrap items-center justify-between gap-2">
          <button
            onClick={() => setSidebarOpen(true)}
            className="md:hidden p-1.5 -ml-1 rounded-lg hover:bg-hover transition-colors text-secondary-text hover:text-foreground"
            aria-label={t('chat.historyTitle')}
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h16"
              />
            </svg>
          </button>
          <div className="ml-auto flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
            <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
              <Tooltip content={t('chat.exportSessionAria')}>
                <span className="inline-flex">
                  <Button
                    variant="action-primary"
                    size="sm"
                    disabled={messages.length === 0}
                    onClick={() => downloadSession(messages)}
                    aria-label={t('chat.exportSessionAria')}
                  >
                    <svg
                      className="w-4 h-4"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                      />
                    </svg>
                    {t('chat.exportSession')}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip content={t('chat.sendToChannelAria')}>
                <span className="inline-flex">
                  <Button
                    variant="action-primary"
                    size="sm"
                    disabled={sending || messages.length === 0}
                    onClick={async () => {
                      if (sending) return;
                      setSending(true);
                      setSendToast(null);
                      try {
                        const content = formatSessionAsMarkdown(messages);
                        await agentApi.sendChat(content);
                        showSendFeedback({ type: 'success', message: t('chat.sentToChannel') }, 3000);
                      } catch (err) {
                        const parsed = getParsedApiError(err);
                        showSendFeedback({
                          type: 'error',
                          message: parsed.message || t('chat.sendFailed'),
                        }, 5000);
                      } finally {
                        setSending(false);
                      }
                    }}
                    aria-label={t('chat.sendToChannelAria')}
                  >
                    {sending ? (
                      <svg
                        className="w-4 h-4 animate-spin"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                        />
                      </svg>
                    ) : (
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                        />
                      </svg>
                    )}
                    {t('chat.send')}
                  </Button>
                </span>
              </Tooltip>
            </div>
          </div>
        </div>

        {/* Content row: sidebar + main chat */}
        <div className="flex min-h-0 flex-1 gap-4">
          {/* Desktop sidebar */}
          <div className="hidden w-64 flex-shrink-0 flex-col overflow-hidden glass-card md:flex">
            {sidebarContent}
          </div>

          {/* Mobile sidebar overlay */}
          {sidebarOpen && (
            <div
              className="fixed inset-0 z-40 md:hidden"
              onClick={() => setSidebarOpen(false)}
            >
              <div className="page-drawer-overlay absolute inset-0" />
              <div
                className="absolute left-0 top-0 bottom-0 w-72 flex flex-col glass-card overflow-hidden border-r border-white/10 bg-card/90 shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              >
                {sidebarContent}
              </div>
            </div>
          )}

          {/* Main chat area */}
          <div className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden">

          <div className="relative z-10 flex min-h-0 flex-1 flex-col overflow-hidden border border-white/6 bg-card/78 glass-card">
          {/* Messages */}
          <ScrollArea
            className="relative z-10 flex-1"
            viewportRef={messagesViewportRef}
            onScroll={handleMessagesScroll}
            viewportClassName="space-y-6 p-4 md:p-6"
            testId="chat-message-scroll"
          >
            {messages.length === 0 && !loading ? (
              sessionMessagesLoading ? (
                <div className="flex h-full items-center justify-center" role="status" aria-live="polite">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                </div>
              ) : (
              <div className="flex h-full items-center justify-center">
                <EmptyState
                  title={t('chat.startTitle')}
                  description={t(
                    agentStatus?.backend === 'codex_app_server'
                      ? 'chat.emptyDescriptionCodex'
                      : 'chat.emptyDescriptionDefault',
                  )}
                  className="max-w-2xl border-dashed bg-card/55"
                  icon={(
                    <svg
                      className="h-8 w-8"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                      />
                    </svg>
                  )}
                  action={(
                    <div className="flex max-w-lg flex-wrap justify-center gap-2">
                      {quickQuestions.map((q, i) => (
                        <button
                          key={i}
                          onClick={() => handleQuickQuestion(q)}
                          disabled={!agentAvailable}
                          className="quick-question-btn disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {q.label}
                        </button>
                      ))}
                    </div>
                  )}
                />
              </div>
              )
            ) : (
              messages.map((msg) => {
                const skillLabel = getMessageSkillLabel(msg);
                return (
                <div
                  key={msg.id}
                  className={`flex ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
                >
                  {/* 不再渲染头像：一行的两边直接由内容自己交代 —— 用户气泡贴右、
                      AI 回复占满整行。左右各让出的 48px（32px 头像 + 16px gap）回到正文宽度。
                      AI 回复不是气泡（flex-1，不收窄到内容宽度、也不压 48rem 上限），没有内边距，
                      文字与面板内边距对齐；用户气泡保持 w-fit + 48rem 上限 + 自己的 chip 内边距。 */}
                  <div
                    className={cn(
                      'group/message min-w-0 overflow-hidden py-3.5 transition-colors',
                      msg.role === 'user'
                        ? 'w-fit max-w-[min(100%,48rem)] px-5 chat-bubble-user'
                        : 'flex-1 chat-message-ai'
                    )}
                  >
                    {msg.role === 'assistant' && (skillLabel || msg.backend) && (
                      <div className="mb-2 flex flex-wrap gap-2">
                        {skillLabel ? <Badge variant="info" className="chat-skill-badge shadow-none" aria-label={`技能 ${skillLabel}`}>
                          <svg
                            className="w-3 h-3"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M13 10V3L4 14h7v7l9-11h-7z"
                            />
                          </svg>
                          {skillLabel}
                        </Badge> : null}
                        {msg.backend ? (
                          <Badge variant={msg.backend === 'codex_app_server' ? 'warning' : 'history'} size="sm">
                            {t(msg.backend === 'codex_app_server' ? 'chat.codexBackendBadge' : 'chat.defaultBackendBadge')}
                          </Badge>
                        ) : null}
                      </div>
                    )}
                    {msg.role === 'assistant' && renderThinkingBlock(msg)}
                    {msg.role === 'assistant' &&
                      expandedThinking.has(msg.id) &&
                      msg.thinkingSteps &&
                      renderThinkingDetails(msg.thinkingSteps)}
                    {msg.role === 'assistant' &&
                      msg.actionProposals?.map((proposal, index) =>
                        renderActionProposalCard(msg, proposal, index),
                      )}
                    {msg.role === 'assistant' ? (
                      <div className="relative">
                        <div className="chat-message-actions">
                          <button
                            type="button"
                            onClick={() => copyMessageToClipboard(msg.id, msg.content)}
                            className="chat-copy-btn"
                            aria-label={copiedMessages.has(msg.id) ? text.copied : text.copy}
                          >
                            {copiedMessages.has(msg.id) ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                          </button>
                          <button
                            type="button"
                            onClick={() => downloadMessageAsMarkdown(msg)}
                            className="chat-copy-btn"
                            aria-label="导出此条消息为 Markdown"
                          >
                            <Download className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        <div className="chat-prose pr-3 sm:pr-4">
                          <MarkdownBody content={msg.content} />
                        </div>
                      </div>
                    ) : (
                      <>
                        {msg.imageDataUrl && (
                          // 图片只在会话内存在（dataUrl 挂在消息上），所以「查看」就是把它放大渲染一遍，
                          // 不涉及任何后端取图。
                          <button
                            type="button"
                            aria-label={t('chat.viewImage')}
                            onClick={() => openImageViewer(msg.imageDataUrl ?? '')}
                            className="mb-2 block cursor-zoom-in rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                          >
                            <img
                              src={msg.imageDataUrl}
                              alt="已发送的图片"
                              className="max-h-40 rounded-lg border border-subtle"
                            />
                          </button>
                        )}
                        {renderUserMessageContent(msg)}
                      </>
                    )}
                  </div>
                </div>
                );
              })
            )}

            {loading && (
              /* 等待中的占位与 AI 回复同形：没有头像、没有气泡外壳，文字与 AI 回复同一条左边线。 */
              <div className="py-4">
                <div className="flex items-center gap-2.5 text-sm text-secondary-text">
                  <div className="relative w-4 h-4 flex-shrink-0">
                    <div className="absolute inset-0 rounded-full border-2 border-cyan/20" />
                    <div className="absolute inset-0 rounded-full border-2 border-cyan border-t-transparent animate-spin" />
                  </div>
                  <span className="text-secondary-text">
                    {getCurrentStage(progressSteps)}
                  </span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </ScrollArea>

          {showJumpToBottom && (
            <div className="pointer-events-none absolute bottom-[5.75rem] right-4 z-20 md:bottom-24 md:right-6">
              <button
                type="button"
                className="pointer-events-auto chat-copy-btn shadow-soft-card"
                onClick={() => {
                  requestScrollToBottom('smooth');
                  scrollToBottom('smooth');
                }}
                aria-label="查看最新消息"
              >
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 14l-7 7m0 0l-7-7m7 7V3"
                  />
                </svg>
                有新消息
              </button>
            </div>
          )}

          {/* Input area */}
          <div className="border-t border-white/6 bg-card/88 p-4 md:p-6 relative z-20">
            <div
              className="space-y-3"
              onDrop={handleImageDrop}
              onDragOver={(e) => e.preventDefault()}
            >
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/6 bg-surface/25 px-3 py-2">
                <label
                  className={cn(
                    'inline-flex items-center gap-2 text-sm',
                    contextCompressionLoaded && !contextCompressionSaving
                      ? 'cursor-pointer text-foreground'
                      : 'cursor-not-allowed text-muted-text',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={contextCompressionEnabled}
                    disabled={!contextCompressionLoaded || contextCompressionSaving}
                    onChange={(event) => void updateContextCompressionEnabled(event.target.checked)}
                    className="chat-skill-checkbox"
                  />
                  <span className="font-medium">{t('chat.contextCompression')}</span>
                  <span className="text-xs text-muted-text">{t('chat.contextCompressionHint')}</span>
                </label>
                <span className="text-xs text-muted-text">
                  {contextCompressionSaving
                    ? t('chat.compressionSaving')
                    : contextCompressionEnabled
                      ? t('chat.compressionEnabled')
                      : t('chat.compressionDisabled')}
                </span>
              </div>
              {skills.length > 0 && (
                <div className="space-y-2">
                  <button
                    type="button"
                    className="home-surface-button flex h-10 w-full items-center justify-between gap-3 rounded-xl px-3 text-left text-sm text-foreground md:hidden"
                    aria-label={mobileSkillPickerOpen ? '收起策略选择' : '展开策略选择'}
                    aria-expanded={mobileSkillPickerOpen}
                    aria-controls="chat-skill-picker-panel"
                    onClick={() => setMobileSkillPickerOpen((open) => !open)}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <SlidersHorizontal className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                      <span className="flex-shrink-0 font-medium">{t('chat.strategy')}</span>
                      <span className="truncate text-xs text-muted-text">{selectedSkillSummary}</span>

                    </span>
                    <ChevronDown
                      className={cn(
                        'h-4 w-4 flex-shrink-0 text-muted-text transition-transform',
                        mobileSkillPickerOpen ? 'rotate-180' : '',
                      )}
                      aria-hidden="true"
                    />
                  </button>
                  <div
                    id="chat-skill-picker-panel"
                    data-testid="chat-skill-picker-panel"
                    className={cn(
                      mobileSkillPickerOpen ? 'flex' : 'hidden',
                      'max-h-40 flex-wrap items-start gap-x-5 gap-y-2 overflow-y-auto rounded-xl border border-white/6 bg-surface/25 px-3 py-2 md:flex md:max-h-none md:overflow-visible md:border-0 md:bg-transparent md:p-0',
                    )}
                  >
                    <span className="text-xs text-muted-text font-medium uppercase tracking-wider flex-shrink-0 mt-1">
                      策略
                    </span>
                    <label className="flex items-center gap-1.5 text-sm cursor-pointer group mt-0.5">
                      <input
                        type="checkbox"
                        name="general-analysis"
                        value=""
                        checked={selectedSkillIds.length === 0}
                        onChange={() => setSelectedSkillIds([])}
                        className="chat-skill-checkbox"
                      />
                      <span
                        className={`transition-colors text-sm ${selectedSkillIds.length === 0 ? 'text-foreground font-medium' : 'text-secondary-text group-hover:text-foreground'}`}
                      >
                        {t('chat.generalAnalysis')}
                      </span>
                    </label>
                    {skills.map((s) => {
                      const checked = selectedSkillIdSet.has(s.id);
                      const disabled = !checked && skillLimitReached;
                      return (
                        <label
                          key={s.id}
                          className={`flex items-center gap-1.5 cursor-pointer group relative mt-0.5 ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
                          onMouseEnter={() => setShowSkillDesc(s.id)}
                          onMouseLeave={() => setShowSkillDesc(null)}
                        >
                          <input
                            type="checkbox"
                            name="skills"
                            value={s.id}
                            checked={checked}
                            disabled={disabled}
                            onChange={() => toggleSkillSelection(s.id)}
                            className="chat-skill-checkbox"
                          />
                          <span
                            className={`transition-colors text-sm ${checked ? 'text-foreground font-medium' : 'text-secondary-text group-hover:text-foreground'}`}
                          >
                            {s.name}
                          </span>
                          {showSkillDesc === s.id && s.description && (
                            <div className="skill-desc-tooltip">
                              <p className="skill-title">{s.name}</p>
                              <p>{s.description}</p>
                            </div>
                          )}
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}

            {activeStockCode && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-text font-mono">{activeStockCode}</span>
                {watchlistLoadFailed ? (
                  // 读不到自选列表时既不能断言「不在自选里」，也不该让用户在这个未知状态上增删。
                  <>
                    <Button variant="secondary" size="xsm" disabled className="text-[11px]">
                      自选状态未知
                    </Button>
                    <Button
                      variant="ghost"
                      size="xsm"
                      onClick={() => void loadWatchlist()}
                      className="text-[11px]"
                    >
                      重新加载自选
                    </Button>
                    <span className="text-[11px] text-secondary-text">
                      自选列表加载失败，无法判断该股票是否已加入。
                    </span>
                  </>
                ) : (
                  <Button
                    variant="secondary"
                    size="xsm"
                    isLoading={isWatchlistActioning}
                    onClick={() => void handleToggleWatchlist(activeStockCode)}
                    className="text-[11px]"
                  >
                    {stockInWatchlist(activeStockCode) ? '从自选删除' : '加入自选'}
                  </Button>
                )}
                {watchlistMessage && (
                  <span className="text-[11px] text-secondary-text animate-in fade-in">{watchlistMessage}</span>
                )}
              </div>
            )}

              {pendingImage && (
                <div className="relative w-fit">
                  <img
                    src={pendingImage.dataUrl}
                    alt={t('chat.pendingImageAlt')}
                    className="h-16 w-16 rounded object-cover border border-white/10"
                  />
                  <button
                    type="button"
                    aria-label={t('chat.removeImage')}
                    onClick={() => setPendingImage(null)}
                    className="chat-composer-icon-btn absolute -right-2 -top-2 rounded-full"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </div>
              )}

              {readingImage && (
                <div role="status" className="text-xs text-secondary-text">
                  {t('chat.readingImage')}
                </div>
              )}

              <div className="flex items-end gap-3">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={(e) => {
                    const files = e.clipboardData?.files;
                    const f = files?.[0];
                    if (f) {
                      // preventDefault 必须留在这个分支里：粘贴纯文字时不能拦，否则文字进不去。
                      e.preventDefault();
                      handleImageFile(f, (files?.length ?? 1) - 1);
                    }
                  }}
                  placeholder={t('chat.inputPlaceholder')}
                  disabled={loading || !agentAvailable}
                  rows={1}
                  className="input-surface input-focus-glow flex-1 min-h-[44px] max-h-[200px] rounded-xl border bg-transparent px-4 py-2.5 text-sm transition-[border-color,background-color,box-shadow] focus:outline-none resize-none disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ height: 'auto' }}
                  onInput={(e) => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = 'auto';
                    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
                  }}
                />
                {loading ? (
                  <Button
                    variant="danger-subtle"
                    onClick={stopStream}
                    disabled={stopping}
                    className="flex-shrink-0"
                  >
                    {stopping ? t('chat.stoppingAnalysis') : t('chat.stopAnalysis')}
                  </Button>
                ) : (
                  <Button
                    variant="primary"
                    onClick={() => handleSend()}
                    disabled={(!input.trim() && !pendingImage) || loading || !agentAvailable}
                    isLoading={loading}
                    className="btn-primary flex-shrink-0"
                  >
                    {t('chat.send')}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
        </div>
      </div>
    </div>
    {/* 点开已发送的图：Esc / 点背景 / 关闭按钮三种关法都复用 Dialog 现成的实现。 */}
    <Dialog
      isOpen={viewImage !== null}
      onClose={() => setViewImage(null)}
      title={t('chat.imagePreviewTitle')}
      ariaLabel={t('chat.imagePreviewTitle')}
      widthClassName="sm:max-w-3xl"
    >
      {viewImage && (
        <div className="flex flex-col items-center gap-4">
          {/* zoom 是布局缩放而不是 transform: scale，所以放大后这个 overflow-auto 容器
              真的能滚，滚动条就充当平移；旋转只做视觉变换。 */}
          <div
            className={cn(
              'w-full overflow-auto',
              // 转过 90° 后图片是绕着自身中心转的，视觉上会超出未旋转的布局盒（transform 又
              // 不算进滚动区，超出去的部分滚不到），所以转起来时改用定高容器 + safe center：
              // 装得下就居中，装不下退回顶部对齐，保证被裁的永远是能被滚到的那一侧。
              rotated
                ? 'flex h-[65vh] items-center-safe justify-center-safe'
                : 'max-h-[65vh]',
            )}
          >
            <img
              src={viewImage}
              alt="已发送的图片"
              style={{ zoom: viewZoom, transform: `rotate(${viewRotation}deg)` }}
              className={cn(
                'w-auto rounded-lg',
                rotated ? 'max-h-[65vh] max-w-[65vh]' : 'mx-auto block max-h-[65vh]',
              )}
            />
          </div>
          <div className="flex items-center justify-center gap-2">
            <button
              type="button"
              aria-label={t('chat.zoomOut')}
              disabled={viewZoom <= IMAGE_VIEWER_ZOOM_MIN}
              onClick={() =>
                setViewZoom((prev) => Math.max(IMAGE_VIEWER_ZOOM_MIN, Number((prev - IMAGE_VIEWER_ZOOM_STEP).toFixed(2))))
              }
              className={IMAGE_VIEWER_BUTTON_CLASS}
            >
              <ZoomOut aria-hidden="true" className="h-4 w-4" />
            </button>
            <span className="w-12 text-center text-xs tabular-nums text-secondary-text">
              {Math.round(viewZoom * 100)}%
            </span>
            <button
              type="button"
              aria-label={t('chat.zoomIn')}
              disabled={viewZoom >= IMAGE_VIEWER_ZOOM_MAX}
              onClick={() =>
                setViewZoom((prev) => Math.min(IMAGE_VIEWER_ZOOM_MAX, Number((prev + IMAGE_VIEWER_ZOOM_STEP).toFixed(2))))
              }
              className={IMAGE_VIEWER_BUTTON_CLASS}
            >
              <ZoomIn aria-hidden="true" className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label={t('chat.rotateLeft')}
              onClick={() => setViewRotation((prev) => (prev + 270) % 360)}
              className={IMAGE_VIEWER_BUTTON_CLASS}
            >
              <RotateCcw aria-hidden="true" className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label={t('chat.rotateRight')}
              onClick={() => setViewRotation((prev) => (prev + 90) % 360)}
              className={IMAGE_VIEWER_BUTTON_CLASS}
            >
              <RotateCw aria-hidden="true" className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => {
                setViewZoom(1);
                setViewRotation(0);
              }}
              className={IMAGE_VIEWER_BUTTON_CLASS}
            >
              {t('chat.resetViewer')}
            </button>
          </div>
        </div>
      )}
    </Dialog>
    {/* 删除历史对话不可逆，所以点删除按钮只发起确认；确认框里点名是哪条会话，
        避免列表里两条标题相近时删错。 */}
    <ConfirmDialog
      isOpen={pendingDeleteSession !== null}
      title={t('chat.deleteSessionTitle')}
      message={
        pendingDeleteSession
          ? formatUiText(t('chat.deleteSessionMessage'), { title: pendingDeleteSession.title })
          : ''
      }
      confirmText={t('common.delete')}
      cancelText={t('common.cancel')}
      isDanger
      onConfirm={confirmDeleteSession}
      onCancel={() => setPendingDeleteSession(null)}
    />
    </>
  );
};

export default ChatPage;
