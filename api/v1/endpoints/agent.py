# -*- coding: utf-8 -*-
"""
Agent API endpoints.
"""

import asyncio
import base64
import json
import logging
import math
import threading
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from api.deps import get_agent_chat_session_service
from api.v1.schemas.system_config import AgentBackendStatusResponse
from src.config import get_config
from src.services.agent_chat_session_service import AgentChatSessionService
from src.services.agent_model_service import list_agent_model_deployments
from src.services.chat_image_context import ImageContextError, build_image_context
from src.services.image_stock_extractor import ALLOWED_MIME, _verify_image_magic_bytes

# Tool name -> Chinese display name mapping
TOOL_DISPLAY_NAMES: Dict[str, str] = {
    "get_realtime_quote":         "获取实时行情",
    "get_daily_history":          "获取历史K线",
    "get_chip_distribution":      "分析筹码分布",
    "get_analysis_context":       "获取分析上下文",
    "get_stock_info":             "获取股票基本面",
    "get_capital_flow":           "分析主力资金流",
    "get_portfolio_snapshot":     "获取持仓快照",
    "search_stock_news":          "搜索股票新闻",
    "search_comprehensive_intel": "搜索综合情报",
    "analyze_trend":              "分析技术趋势",
    "calculate_ma":               "计算均线系统",
    "get_volume_analysis":        "分析量能变化",
    "analyze_pattern":            "识别K线形态",
    "get_market_indices":         "获取市场指数",
    "get_sector_rankings":        "分析行业板块",
    "get_skill_backtest_summary": "获取技能回测概览",
    "get_strategy_backtest_summary": "获取策略回测概览",
    "get_stock_backtest_summary": "获取个股回测数据",
    "propose_alert": "生成告警提案",
    "propose_portfolio_trade": "生成持仓录入提案",
    "propose_watchlist_change": "生成自选变更提案",
}

logger = logging.getLogger(__name__)

router = APIRouter()

_ACTIVE_CODEX_STREAMS: Dict[str, threading.Event] = {}
_ACTIVE_CODEX_STREAMS_LOCK = threading.Lock()

class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    skills: Optional[List[str]] = Field(
        default=None,
        validation_alias=AliasChoices("skills", "strategies"),
    )
    context: Optional[Dict[str, Any]] = None  # Previous analysis context for data reuse

    # 贴图：base64 + mime 成对出现。二进制不落盘，只在当轮转成文本注入（见设计文档 §2/§5）。
    # 成对校验刻意放在 _resolve_image_message 里而不是 model_validator：那样所有图片问题
    # 都是同一套 400 + 明确 message，而不是"成对错误回 422、其余回 400"。
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None

    @property
    def effective_skills(self) -> Optional[List[str]]:
        """Return skill ids from the unified request shape."""
        return self.skills


# 聊天请求体是 JSON 且还带会话上下文，base64 会放大 ~1.33 倍，因此上限比提取接口的 5MB 窄。
CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024


def _image_error(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "invalid_image", "message": message})


def _resolve_image_message(request: ChatRequest) -> str:
    """Return the message to hand to the agent, with image context injected when present.

    校验失败 → 400（参数问题）；视觉失败 → 502（外部依赖问题）。**不降级成"看不到图也照样
    回答"**：那种回答既无用又会伪装成成功（见设计文档 §8）。
    """
    if not request.image_base64 and not request.image_mime:
        return request.message
    if bool(request.image_base64) != bool(request.image_mime):
        raise _image_error("image_base64 与 image_mime 必须同时提供")

    mime = str(request.image_mime or "").strip().lower()
    if mime not in ALLOWED_MIME:
        raise _image_error(f"不支持的图片类型：{mime or '(空)'}；支持 {', '.join(sorted(ALLOWED_MIME))}")

    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except Exception as exc:
        raise _image_error(f"图片数据不是合法 base64：{exc}") from exc

    if len(raw) > CHAT_IMAGE_MAX_BYTES:
        raise _image_error(f"图片过大（{len(raw) // 1024}KB），上限 2MB")

    try:
        _verify_image_magic_bytes(raw, mime)
    except Exception as exc:
        raise _image_error(f"图片内容与声明的类型不符：{exc}") from exc

    try:
        block = build_image_context(request.image_base64, mime, request.message)
    except ImageContextError as exc:
        raise HTTPException(status_code=502, detail={"error": "image_unreadable", "message": str(exc)}) from exc

    # 注入块在前、用户原话在后：模型先读图，再对着问题回答。
    return f"{block}\n{request.message}"


def _build_agent_chat_context(request: ChatRequest, config, skills: Optional[List[str]]) -> Dict[str, Any]:
    """Build the shared context contract for regular and streaming Agent Chat."""
    context = dict(request.context or {})
    context.pop("skills", None)
    context.pop("strategies", None)
    if skills is not None:
        context["skills"] = skills
    report_language = context.get("report_language")
    if report_language is None or (isinstance(report_language, str) and not report_language.strip()):
        context["report_language"] = config.report_language
    return context


class ChatResponse(BaseModel):
    success: bool
    content: str
    session_id: str
    error: Optional[str] = None


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str

class SkillsResponse(BaseModel):
    skills: List[SkillInfo]
    default_skill_id: str = ""


class StrategiesResponse(BaseModel):
    strategies: List[SkillInfo]
    default_strategy_id: str = ""


class AgentModelDeployment(BaseModel):
    deployment_id: str
    model: str
    provider: str
    source: str
    api_base: Optional[str] = None
    deployment_name: Optional[str] = None
    is_primary: bool = False
    is_fallback: bool = False


class AgentModelsResponse(BaseModel):
    models: List[AgentModelDeployment]


@router.get("/models", response_model=AgentModelsResponse)
async def get_agent_models():
    """Get configured Agent model deployments for frontend selection."""
    config = get_config()
    from src.agent.agent_backend import AgentBackendConfigError, resolve_agent_backend_id

    try:
        selected_backend = resolve_agent_backend_id(config)
    except AgentBackendConfigError:
        return AgentModelsResponse(models=[])
    if selected_backend == "codex_app_server":
        return AgentModelsResponse(models=[])
    return AgentModelsResponse(
        models=[AgentModelDeployment(**item) for item in list_agent_model_deployments(config)]
    )


@router.get("/status", response_model=AgentBackendStatusResponse)
async def get_agent_status():
    """Return the current effective Chat backend status for the Chat page."""
    payload = await asyncio.to_thread(_get_agent_chat_status, get_config())
    return _agent_status_response(payload)


def _agent_status_response(payload: Dict[str, Any]) -> AgentBackendStatusResponse:
    return AgentBackendStatusResponse(
        backend=payload["backend"],
        available=payload["available"],
        experimental=payload["experimental"],
        version=payload.get("version"),
        error_code=payload.get("error_code"),
        message=payload.get("message"),
    )


def _build_skills_response(config) -> SkillsResponse:
    from src.agent.factory import get_skill_manager
    from src.agent.skills.defaults import get_primary_default_skill_id

    skill_manager = get_skill_manager(config)
    available_skills = sorted(
        [
            skill
            for skill in skill_manager.list_skills()
            if getattr(skill, "user_invocable", True)
        ],
        key=lambda skill: (
            int(getattr(skill, "default_priority", 100)),
            skill.display_name,
            skill.name,
        ),
    )
    skills = [
        SkillInfo(id=skill.name, name=skill.display_name, description=skill.description)
        for skill in available_skills
    ]
    return SkillsResponse(
        skills=skills,
        default_skill_id=get_primary_default_skill_id(available_skills),
    )


@router.get("/skills", response_model=SkillsResponse)
async def get_skills():
    """
    Get available agent strategy skills.
    """
    return _build_skills_response(get_config())


@router.get("/strategies", response_model=StrategiesResponse, include_in_schema=False)
async def get_strategies():
    """Compatibility alias for legacy clients."""
    payload = _build_skills_response(get_config())
    return StrategiesResponse(
        strategies=payload.skills,
        default_strategy_id=payload.default_skill_id,
    )

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    request: ChatRequest,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """
    Chat with the AI Agent without progress events.

    Codex Agent callers must use ``/chat/stream``, which provides progress
    events and request cancellation. The default LiteLLM Agent keeps this
    endpoint's existing behavior.
    """
    config = get_config()
    backend_id = _select_agent_chat_backend(config)
    if backend_id == "codex_app_server":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "capability_unsupported",
                "message": "Codex Agent requires the Chat interface with progress and stop support",
            },
        )
    
    session_id = request.session_id or str(uuid.uuid4())

    # 必须在下面那个兜底 except Exception 之前：HTTPException 也是 Exception，
    # 放进 try 里会被统一改写成 500，400/502 就永远传不出去。
    message_text = await asyncio.to_thread(_resolve_image_message, request)

    try:
        skill_selection = session_service.resolve_skill_selection(
            config,
            session_id,
            request.effective_skills,
        )
        skills = skill_selection.effective_skill_ids
        selected_skill_ids = skill_selection.selected_skill_ids_update
        executor = _build_executor(config, skills or None)

        ctx = _build_agent_chat_context(request, config, skills)

        # Offload the blocking call to a thread to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: executor.chat(message=message_text, session_id=session_id,
                                  context=ctx, selected_skill_ids=selected_skill_ids),
        )

        return ChatResponse(
            success=result.success,
            content=result.content,
            session_id=session_id,
            error=result.error,
        )
            
    except Exception as e:
        logger.exception("Agent chat API failed")
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "服务器内部错误"},
        )


class SessionItem(BaseModel):
    session_id: str
    title: str
    message_count: int
    created_at: Optional[str] = None
    last_active: Optional[str] = None

class SessionsResponse(BaseModel):
    sessions: List[SessionItem]

class SessionStateResponse(BaseModel):
    selected_skill_ids: Optional[List[str]]

class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]
    session_state: SessionStateResponse


@router.get("/chat/sessions", response_model=SessionsResponse)
async def list_chat_sessions(
    limit: int = 50,
    user_id: Optional[str] = None,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """获取聊天会话列表

    Args:
        limit: Maximum number of sessions to return.
        user_id: Optional platform-prefixed user identifier for session
            isolation.  When provided, only sessions whose session_id
            starts with this prefix are returned.  The value must
            include the platform prefix, e.g. ``telegram_12345``,
            ``feishu_ou_abc``.
    """
    sessions = session_service.list_sessions(limit, user_id)
    return SessionsResponse(sessions=sessions)


@router.get("/chat/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_chat_session_messages(
    session_id: str,
    limit: int = 100,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """获取单个会话的完整消息"""
    detail = session_service.get_session_detail(
        session_id,
        limit,
    )
    return SessionMessagesResponse(
        session_id=session_id,
        messages=detail.messages,
        session_state=SessionStateResponse(
            selected_skill_ids=detail.selected_skill_ids,
        ),
    )


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """删除指定会话"""
    count = session_service.delete_session(session_id)
    return {"deleted": count}


class SendChatRequest(BaseModel):
    """Request body for sending chat content to notification channels."""

    content: str = Field(..., min_length=1, max_length=50000)
    title: Optional[str] = None


@router.post("/chat/send")
async def send_chat_to_notification(request: SendChatRequest):
    """
    Send chat session content to configured notification channels.
    Uses run_in_executor to avoid blocking the event loop.
    """
    from src.notification import NotificationService

    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(
        None,
        lambda: NotificationService().send(request.content),
    )
    if not success:
        return {
            "success": False,
            "error": "no_channels",
            "message": "未配置通知渠道，请先在设置中配置",
        }
    return {"success": True}


def _build_executor(config, skills: Optional[List[str]] = None):
    """Build and return the backend-neutral Chat executor (sync helper)."""
    from src.agent.factory import build_agent_chat_executor

    return build_agent_chat_executor(config, skills=skills)


def _get_agent_chat_status(config) -> Dict[str, Any]:
    from src.services.agent_backend_status_service import AgentBackendStatusService

    return AgentBackendStatusService(config=config).get_status()


def _select_agent_chat_backend(config) -> str:
    """Select the runtime backend without repeating the compatibility probe."""
    from src.services.agent_backend_status_service import evaluate_agent_backend_config

    evaluation = evaluate_agent_backend_config(config)
    if not evaluation["available"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": evaluation["error_code"],
                "message": evaluation["message"],
            },
        )
    return evaluation["backend"]


# Margin added on top of the runtime budget before the API gives up.  It only
# covers delivering the runtime's own terminal event (plus the runtime's
# cooperative budget check granularity); it is not a second budget.
_STREAM_WAIT_GRACE_SECONDS = 30.0


def _resolve_stream_wait_timeout(executor) -> Optional[float]:
    """Derive the API-side per-event wait limit from the runtime's own budget.

    The pipeline enforces ``agent_orchestrator_timeout_s`` (``AgentChatExecutor``
    hands it to the runner as ``max_wall_clock_seconds``) and emits its own
    terminal event when that budget expires.  A second, hardcoded API-side limit
    races that deadline: under the 600s default a 300s limit fired first, the
    stream emitted "分析超时" and ended, while the worker thread — which the
    LiteLLM backend cannot cancel — kept running and later persisted an
    assistant message the user never saw arrive, so reloading the session showed
    a reply out of nowhere.

    The limit therefore follows the same budget plus a small margin, so the
    runtime's own outcome wins whenever it reaches one and the API deadline
    remains only as a backstop for a worker stuck past its own budget.  ``0``
    disables the runtime budget by design on the LiteLLM path; a disabled budget
    leaves nothing to derive a deadline from, so no second limit is imposed —
    the same contract as the Codex branch, where the backend owns the only
    deadline.
    """
    try:
        budget_s = float(getattr(executor, "timeout_seconds", None))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(budget_s) or budget_s <= 0:
        return None
    return budget_s + _STREAM_WAIT_GRACE_SECONDS


async def _run_research_in_background(
    agent,
    question: str,
    context: Optional[Dict[str, Any]],
    *,
    timeout: int,
):
    """Run deep research off the event loop with an internal overall timeout."""
    return await asyncio.to_thread(
        agent.research,
        question,
        context,
        timeout_seconds=timeout,
    )


# ============================================================
# Deep research endpoint
# ============================================================

class ResearchRequest(BaseModel):
    question: str
    stock_code: Optional[str] = None

class ResearchResponse(BaseModel):
    success: bool
    content: str
    sources: List[str] = Field(default_factory=list)
    token_usage: int = 0
    error: Optional[str] = None


@router.post("/research", response_model=ResearchResponse)
async def agent_research(request: ResearchRequest):
    """Run a deep-research query via the ResearchAgent.

    Similar to the ``/research`` bot command but exposed as a REST endpoint.
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    question = request.question
    context: Optional[Dict[str, Any]] = None
    if request.stock_code:
        question = f"[Stock: {request.stock_code}] {question}"
        context = {"stock_code": request.stock_code}

    try:
        from src.agent.research import ResearchAgent
        from src.agent.factory import get_tool_registry
        from src.agent.llm_adapter import LLMToolAdapter

        registry = get_tool_registry()
        llm_adapter = LLMToolAdapter(config)
        budget = getattr(config, "agent_deep_research_budget", 30000)

        agent = ResearchAgent(
            tool_registry=registry,
            llm_adapter=llm_adapter,
            token_budget=budget,
        )

        research_timeout = getattr(config, "agent_deep_research_timeout", 180)

        result = await _run_research_in_background(
            agent,
            question,
            context,
            timeout=research_timeout,
        )
        if getattr(result, "timed_out", False):
            logger.warning("Agent research API timed out after %ss", research_timeout)
            return ResearchResponse(
                success=False,
                content="",
                sources=[],
                token_usage=0,
                error=f"Deep research timed out after {research_timeout}s",
            )

        return ResearchResponse(
            success=result.success,
            content=result.report,
            sources=[f"Sub-question {i+1}: {q}" for i, q in enumerate(result.sub_questions)],
            token_usage=result.total_tokens,
            error=result.error if not result.success else None,
        )
    except Exception as e:
        logger.exception("Agent research API failed")
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "服务器内部错误"},
        )


@router.post("/chat/stream")
async def agent_chat_stream(
    request: ChatRequest,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """
    Chat with the AI Agent, streaming progress via SSE.
    Each SSE event is a JSON object with a 'type' field:
      - thinking: AI is deciding next action
      - stage_start: an agent or orchestrator stage has begun
      - stage_done: an agent or orchestrator stage finished
      - tool_start: a tool call has begun
      - tool_done: a tool call finished
      - generating: final answer being generated
      - pipeline_timeout: analysis stopped because the stage/pipeline budget expired
      - pipeline_budget_skipped: analysis stopped before an unstarted stage
        because the remaining budget was too low for useful work
      - done: analysis complete, contains 'content' and 'success'
      - error: error occurred, contains 'message'
    """
    config = get_config()
    backend_id = _select_agent_chat_backend(config)

    session_id = request.session_id or str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = threading.Event()
    request_id = request.request_id or str(uuid.uuid4())
    skill_selection = session_service.resolve_skill_selection(
        config,
        session_id,
        request.effective_skills,
    )
    skills = skill_selection.effective_skill_ids
    selected_skill_ids = skill_selection.selected_skill_ids_update
    stream_ctx = _build_agent_chat_context(request, config, skills)

    # 图片校验与视觉调用必须先于 StreamingResponse 完成：一旦开始流式，HTTPException
    # 就无法再变成 400/502 响应，只会退化成一条通用流错误（见设计说明）。也必须在下面
    # 的 codex 注册之前：注册是有副作用的，中途失败会留下一条永远不会被清理的
    # _ACTIVE_CODEX_STREAMS 记录，让同一个 request_id 之后一直被 409 挡掉。
    message_text = await asyncio.to_thread(_resolve_image_message, request)

    if backend_id == "codex_app_server":
        with _ACTIVE_CODEX_STREAMS_LOCK:
            if request_id in _ACTIVE_CODEX_STREAMS:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "request_conflict",
                        "message": "This Agent request is already running",
                    },
                )
            _ACTIVE_CODEX_STREAMS[request_id] = cancel_event

    def progress_callback(event: dict):
        if backend_id == "codex_app_server" and cancel_event.is_set():
            return
        # Enrich tool events with display names
        if event.get("type") in ("tool_start", "tool_done"):
            tool = event.get("tool", "")
            event["display_name"] = TOOL_DISPLAY_NAMES.get(tool, tool)
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def run_sync(executor, turn):
        try:
            execute_kwargs = {
                "progress_callback": progress_callback,
            }
            if backend_id == "codex_app_server":
                execute_kwargs["cancel_event"] = cancel_event
            result = executor.execute_turn(
                turn,
                **execute_kwargs,
            )
            event = {
                "type": "done",
                "success": result.success,
                "content": result.content,
                "error": result.error,
                "total_steps": result.total_steps,
                "session_id": session_id,
            }
            event.update({
                "backend": getattr(result, "backend", "") or backend_id,
                "error_code": getattr(result, "error_code", None),
                "request_id": request_id,
            })
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            logger.error("Agent stream error: %s", exc)
            event = {
                "type": "error",
                "message": "Agent Chat failed" if backend_id == "codex_app_server" else str(exc),
                "error_code": getattr(exc, "code", "unknown_backend_error"),
                "backend": backend_id,
                "request_id": request_id,
            }
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    async def event_generator():
        fut = None
        try:
            try:
                executor = await asyncio.to_thread(_build_executor, config, skills or None)
                stream_wait_timeout = _resolve_stream_wait_timeout(executor)
                turn = await asyncio.to_thread(
                    executor.prepare_turn,
                    message=message_text,
                    session_id=session_id,
                    context=stream_ctx,
                    selected_skill_ids=selected_skill_ids,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Agent request preparation failed: %s", exc, exc_info=True)
                event = {
                    "type": "error",
                    "message": "Agent request was not accepted",
                    "error_code": "request_not_accepted",
                    "backend": backend_id,
                    "request_id": request_id,
                }
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                return

            accepted_event = {
                "type": "accepted",
                "backend": backend_id,
                "request_id": request_id,
                "session_id": session_id,
            }
            yield "data: " + json.dumps(accepted_event, ensure_ascii=False) + "\n\n"

            # Backend execution starts only after the accepted event has been
            # yielded, so Web state and server persistence share one commit point.
            fut = loop.run_in_executor(None, run_sync, executor, turn)
            while True:
                try:
                    if backend_id == "codex_app_server":
                        # Codex owns one authoritative backend deadline.  A
                        # second API timeout would race it and could emit a
                        # terminal event before process cleanup finishes.
                        event = await queue.get()
                    else:
                        event = await asyncio.wait_for(queue.get(), timeout=stream_wait_timeout)
                except asyncio.TimeoutError:
                    # The runtime's own budget did not produce a terminal event in
                    # time.  This backend cannot cancel its worker, so the pipeline
                    # may still finish and persist its own message afterwards; record
                    # it, since the user's stream has already ended by then.
                    logger.warning(
                        "agent stream wait limit expired after %ss for session %s; "
                        "the %s worker is still running and may persist its result",
                        stream_wait_timeout,
                        session_id,
                        backend_id,
                    )
                    event = {"type": "error", "message": "分析超时", "error_code": "timeout"}
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event.get("type") in ("done", "error"):
                    break
        finally:
            if backend_id == "codex_app_server" and (fut is None or not fut.done()):
                cancel_event.set()
            try:
                if backend_id == "codex_app_server" and fut is not None:
                    while not fut.done():
                        try:
                            await asyncio.shield(fut)
                        except asyncio.CancelledError:
                            # Client disconnect cancellation must not abandon the
                            # owned Codex/tool worker before it actually exits.
                            cancel_event.set()
                    if not fut.cancelled():
                        fut.result()
                elif fut is not None:
                    await asyncio.wait_for(fut, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                # Cleanup taking longer than 5s is treated as an expected timeout; no warning.
                logger.debug("agent executor cleanup timed out after 5s for session %s", session_id)
            except Exception as exc:
                logger.warning("agent executor cleanup error (ignored): %s", exc, exc_info=True)
            finally:
                if backend_id == "codex_app_server":
                    with _ACTIVE_CODEX_STREAMS_LOCK:
                        if _ACTIVE_CODEX_STREAMS.get(request_id) is cancel_event:
                            _ACTIVE_CODEX_STREAMS.pop(request_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/chat/stream/{request_id}/cancel")
async def cancel_agent_chat_stream(request_id: str):
    """Signal cancellation while the original Codex SSE remains open."""
    with _ACTIVE_CODEX_STREAMS_LOCK:
        cancel_event = _ACTIVE_CODEX_STREAMS.get(request_id)
    if cancel_event is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "request_not_active",
                "message": "This Agent request is no longer running",
            },
        )
    cancel_event.set()
    return {"accepted": True, "request_id": request_id}
