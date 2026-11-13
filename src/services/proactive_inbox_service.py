# -*- coding: utf-8 -*-
"""Server-side proactive inbox built on the proactive chat sessions."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager

INBOX_MESSAGE_TYPES = {
    "daily_brief": "日报",
    "event": "事件",
    "weekly": "周复盘",
    "opportunity": "机会",
}
INBOX_TYPE_KEYS = tuple(INBOX_MESSAGE_TYPES)
INBOX_TYPE_ALIASES = {
    "daily_brief": "daily_brief",
    "brief": "daily_brief",
    "event": "event",
    "weekly": "weekly",
    "opportunity": "opportunity",
}
_MARKDOWN_PREFIX_RE = re.compile(r"^[#>*\-\s]+")
_MARKDOWN_NOISE_RE = re.compile(r"[*_`>|#]+")
_WHITESPACE_RE = re.compile(r"\s+")
_STOCK_CODE_RE = re.compile(r"[（(]([0-9]{5,6}|[A-Z]{2,5}(?:\.[A-Z]{1,2})?)[）)]")


def _clean_text(value: str) -> str:
    text = _MARKDOWN_NOISE_RE.sub(" ", value or "")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _first_line(content: str) -> str:
    for line in (content or "").splitlines():
        cleaned = _MARKDOWN_PREFIX_RE.sub("", line).strip()
        if cleaned:
            return cleaned
    return ""


class ProactiveInboxService:
    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or DatabaseManager.get_instance()

    def list(
        self,
        *,
        type: Optional[str] = None,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_type = self._normalize_type(type)
        message_types = [
            message_type
            for message_type in INBOX_MESSAGE_TYPES
            if normalized_type is None or message_type == normalized_type
        ]
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        offset = (safe_page - 1) * safe_size
        rows = self.db.list_proactive_messages(
            message_types,
            limit=safe_size + 1,
            offset=offset,
            message_id=message_id,
            unread_only=unread_only,
        )
        has_more = len(rows) > safe_size
        items = [self._serialize(row) for row in rows[:safe_size]]
        return {
            "items": items,
            "unread_count": self.db.count_unread_proactive_inbox(list(INBOX_TYPE_KEYS)),
            "total": len(items),
            "page": safe_page,
            "page_size": safe_size,
            "has_more": has_more,
        }

    def mark_read(self, message_ids: List[int]) -> int:
        return self.db.mark_proactive_inbox_read(message_ids)

    def mark_unread(self, message_ids: List[int]) -> int:
        return self.db.unmark_proactive_inbox_read(message_ids)

    def mark_all_read(self) -> int:
        return self.db.mark_all_proactive_inbox_read(list(INBOX_TYPE_KEYS))

    @staticmethod
    def _normalize_type(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return INBOX_TYPE_ALIASES.get(str(value).strip().lower())

    @staticmethod
    def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
        message_type = str(row.get("message_type") or "unknown")
        label = INBOX_MESSAGE_TYPES.get(message_type, "提醒")
        content = str(row.get("content") or "")
        title = str(row.get("title") or "").strip() or _clean_text(_first_line(content)) or label
        preview = _clean_text(content)
        if len(preview) > 140:
            preview = f"{preview[:137]}..."
        code_match = _STOCK_CODE_RE.search(title) or _STOCK_CODE_RE.search(content)
        return {
            "id": int(row.get("id") or 0),
            "session_id": "",
            "type": message_type,
            "type_label": label,
            "title": title[:120],
            "preview": preview,
            "content": content,
            "stock_code": code_match.group(1) if code_match else None,
            "created_at": row.get("created_at"),
            "read": bool(row.get("read_at")),
            "read_at": row.get("read_at"),
        }
