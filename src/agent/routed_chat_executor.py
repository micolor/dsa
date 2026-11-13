# -*- coding: utf-8 -*-
"""Per-turn dispatcher for Agent Chat.

``AGENT_ARCH=multi`` used to mean "the multi-agent stock pipeline handles every
message".  That is the right behavior for "分析 600519", but it also forced
greetings and site queries through Technical -> Intel -> Risk, and the stage
agents cannot reach the site-operation tools.

``RoutedAgentChatExecutor`` keeps both executors alive and chooses one before a
turn is prepared:

* stock analysis (explicit stock scope + analysis intent) -> multi pipeline
* everything else -> the general single Agent

The choice is made in ``prepare_turn`` so the same delegate persists the user
turn, runs it, and persists the assistant reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from src.agent.chat_intent_router import route_chat_intent


@dataclass(frozen=True)
class RoutedChatTurn:
    """A prepared turn plus the executor that owns it."""

    delegate: Any
    inner: Any


class RoutedAgentChatExecutor:
    """Route each Chat turn to the single Agent or the multi-agent pipeline."""

    def __init__(
        self,
        *,
        single_executor: Any,
        multi_executor: Any,
        config: Any = None,
    ) -> None:
        self.single_executor = single_executor
        self.multi_executor = multi_executor
        self.config = config
        self.timeout_seconds = self._resolve_timeout_seconds(config)

    @staticmethod
    def _resolve_timeout_seconds(config: Any) -> int:
        raw = getattr(config, "agent_orchestrator_timeout_s", 0) if config is not None else 0
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    def _routing_enabled(self) -> bool:
        if self.config is None:
            return True
        return bool(getattr(self.config, "agent_chat_intent_routing_enabled", True))

    def _select_delegate(self, message: str, context: Optional[Dict[str, Any]]) -> Any:
        if not self._routing_enabled():
            return self.multi_executor
        arch = getattr(self.config, "agent_arch", "multi") if self.config is not None else "multi"
        if route_chat_intent(message, context, agent_arch=arch) == "multi":
            return self.multi_executor
        return self.single_executor

    def prepare_turn(
        self,
        *,
        message: str,
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
        selected_skill_ids: Optional[list] = None,
    ) -> RoutedChatTurn:
        delegate = self._select_delegate(message, context)
        inner = delegate.prepare_turn(
            message=message,
            session_id=session_id,
            context=context,
            selected_skill_ids=selected_skill_ids,
        )
        return RoutedChatTurn(delegate=delegate, inner=inner)

    def execute_turn(
        self,
        turn: RoutedChatTurn,
        *,
        progress_callback: Optional[Callable] = None,
        cancel_event: Any = None,
    ) -> Any:
        if not isinstance(turn, RoutedChatTurn):
            raise TypeError("execute_turn expects a RoutedChatTurn")
        return turn.delegate.execute_turn(
            turn.inner,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    def chat(
        self,
        message: str,
        session_id: str,
        progress_callback: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
        cancel_event: Any = None,
        selected_skill_ids: Optional[list] = None,
    ) -> Any:
        turn = self.prepare_turn(
            message=message,
            session_id=session_id,
            context=context,
            selected_skill_ids=selected_skill_ids,
        )
        return self.execute_turn(
            turn,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
