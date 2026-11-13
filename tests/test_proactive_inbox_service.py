# -*- coding: utf-8 -*-
"""Tests for the server-side proactive inbox."""

from __future__ import annotations

from src.services.proactive_inbox_service import ProactiveInboxService


class _FakeDb:
    def __init__(self, rows):
        self.rows = list(rows)
        self.read_ids = set()
        self.all_read = 0

    def list_proactive_messages(
        self,
        message_types,
        limit=100,
        offset=0,
        message_id=None,
        unread_only=False,
    ):
        rows = [row for row in self.rows if row["message_type"] in message_types]
        if message_id is not None:
            rows = [row for row in rows if row["id"] == message_id]
        if unread_only:
            rows = [row for row in rows if row.get("read_at") is None]
        return rows[offset:offset + limit]

    def count_unread_proactive_inbox(self, message_types):
        return sum(
            1 for row in self.rows
            if row["message_type"] in message_types and row.get("read_at") is None
        )

    def mark_proactive_inbox_read(self, message_ids):
        self.read_ids.update(message_ids)
        return len(message_ids)

    def unmark_proactive_inbox_read(self, message_ids):
        self.read_ids.difference_update(message_ids)
        return len(message_ids)

    def mark_all_proactive_inbox_read(self, session_ids):
        self.all_read += 1
        return 3


def _rows():
    return [
        {
            "id": 1,
            "message_type": "event",
            "title": "长青科技（001324）跌破 MA20",
            "content": "## 长青科技（001324）跌破 MA20\n\n现价 18.51 < MA20 18.76。",
            "created_at": "2026-11-05T14:32:00+08:00",
            "read_at": None,
        },
        {
            "id": 2,
            "message_type": "daily_brief",
            "title": "助手日报 · 2026-11-05 15:30",
            "content": "# 助手日报 · 2026-11-05 15:30\n\n**一句话：** 持仓 0 只。",
            "created_at": "2026-11-05T15:30:00+08:00",
            "read_at": "2026-11-05T15:31:00+08:00",
        },
    ]


def test_list_serializes_and_filters_by_type():
    service = ProactiveInboxService(db=_FakeDb(_rows()))

    result = service.list(type="event")

    assert result["unread_count"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["type"] == "event"
    assert item["type_label"] == "事件"
    assert item["title"].startswith("长青科技")
    assert item["read"] is False
    assert "MA20" in item["preview"]


def test_unread_only_filters_read_items():
    service = ProactiveInboxService(db=_FakeDb(_rows()))

    result = service.list(unread_only=True)

    assert [item["id"] for item in result["items"]] == [1]


def test_mark_read_and_mark_all_delegate_to_storage():
    db = _FakeDb(_rows())
    service = ProactiveInboxService(db=db)

    assert service.mark_read([1, 2]) == 2
    assert db.read_ids == {1, 2}
    assert service.mark_all_read() == 3
    assert db.all_read == 1


def test_pagination_returns_has_more():
    rows = _rows()
    rows.append({
        "id": 3,
        "message_type": "opportunity",
        "title": "机会扫描",
        "content": "# 机会扫描\n\n候选 1。",
        "created_at": "2026-11-05T16:00:00+08:00",
        "read_at": None,
    })
    service = ProactiveInboxService(db=_FakeDb(rows))

    first = service.list(page=1, page_size=2)
    second = service.list(page=2, page_size=2)

    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert len(second["items"]) == 1
    assert second["has_more"] is False


def test_unread_only_filters_before_pagination():
    rows = [
        {
            "id": 3,
            "message_type": "event",
            "title": "已读 3",
            "content": "已读 3",
            "created_at": "2026-11-07T10:00:00+08:00",
            "read_at": "2026-11-07T10:01:00+08:00",
        },
        {
            "id": 2,
            "message_type": "event",
            "title": "已读 2",
            "content": "已读 2",
            "created_at": "2026-11-07T09:00:00+08:00",
            "read_at": "2026-11-07T09:01:00+08:00",
        },
        {
            "id": 1,
            "message_type": "event",
            "title": "更早的未读",
            "content": "更早的未读",
            "created_at": "2026-11-06T09:00:00+08:00",
            "read_at": None,
        },
    ]
    service = ProactiveInboxService(db=_FakeDb(rows))

    result = service.list(unread_only=True, page_size=1)

    assert [item["id"] for item in result["items"]] == [1]
    assert result["items"][0]["read"] is False


def test_list_filters_by_message_id_for_deep_links():
    service = ProactiveInboxService(db=_FakeDb(_rows()))

    result = service.list(message_id=2)

    assert [item["id"] for item in result["items"]] == [2]
    assert result["items"][0]["type"] == "daily_brief"


def test_unknown_type_falls_back_to_all_items():
    service = ProactiveInboxService(db=_FakeDb(_rows()))

    result = service.list(type="unknown")

    assert len(result["items"]) == 2
