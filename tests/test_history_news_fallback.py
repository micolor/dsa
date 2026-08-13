# -*- coding: utf-8 -*-
"""Tests for history fallback published_date hard filtering (Issue #697)."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.history_service import HistoryService


class HistoryNewsFallbackTestCase(unittest.TestCase):
    def test_get_news_intel_passes_through_published_date(self) -> None:
        """get_news_intel 必须透传 published_date（回归：曾只返回 title/snippet/url）"""
        now = datetime(2026, 8, 12, 10, 30, 0)
        record = SimpleNamespace(
            title="茅台发布新产品",
            snippet="公司披露季度经营情况。",
            url="https://example.com/news",
            published_date=now,
        )

        mock_db = MagicMock()
        mock_db.get_news_intel_by_query_id.return_value = [record]

        svc = HistoryService(db_manager=mock_db)
        items = svc.get_news_intel("q-1", limit=20)

        self.assertEqual(items, [
            {
                "title": "茅台发布新产品",
                "snippet": "公司披露季度经营情况。",
                "url": "https://example.com/news",
                "published_date": now.date(),
            },
        ])

    def test_fallback_filters_by_published_date_window(self) -> None:
        now = datetime.now()
        analysis = SimpleNamespace(code="600519", created_at=now)

        # All entries are within fetched_at window; only one should pass published_date window.
        candidates = [
            SimpleNamespace(
                fetched_at=now,
                published_date=now - timedelta(days=20),  # too old
                title="old",
            ),
            SimpleNamespace(
                fetched_at=now,
                published_date=None,  # unknown -> drop
                title="unknown",
            ),
            SimpleNamespace(
                fetched_at=now,
                published_date=now - timedelta(days=1),  # valid
                title="fresh",
            ),
        ]

        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [analysis]
        mock_db.get_recent_news.return_value = candidates

        svc = HistoryService(db_manager=mock_db)
        fake_cfg = SimpleNamespace(news_max_age_days=30, news_strategy_profile="short")
        with patch("src.services.history_service.get_config", return_value=fake_cfg):
            result = svc._fallback_news_by_analysis_context("q-1", limit=20)

        self.assertEqual([item.title for item in result], ["fresh"])

    def test_fallback_uses_analysis_date_as_window_anchor(self) -> None:
        analysis_time = datetime.now() - timedelta(days=40)
        analysis = SimpleNamespace(code="600519", created_at=analysis_time)

        candidates = [
            SimpleNamespace(
                fetched_at=analysis_time,
                published_date=analysis_time - timedelta(days=10),  # too old for short profile
                title="too_old_for_analysis_window",
            ),
            SimpleNamespace(
                fetched_at=analysis_time,
                published_date=analysis_time - timedelta(days=1),  # valid around analysis date
                title="valid_near_analysis_date",
            ),
        ]

        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [analysis]
        mock_db.get_recent_news.return_value = candidates

        svc = HistoryService(db_manager=mock_db)
        fake_cfg = SimpleNamespace(news_max_age_days=30, news_strategy_profile="short")
        with patch("src.services.history_service.get_config", return_value=fake_cfg):
            result = svc._fallback_news_by_analysis_context("q-1", limit=20)

        self.assertEqual([item.title for item in result], ["valid_near_analysis_date"])


if __name__ == "__main__":
    unittest.main()
