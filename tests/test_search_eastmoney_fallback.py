# -*- coding: utf-8 -*-
"""
===================================
东方财富免费搜索兜底 单元测试
===================================

职责：
1. 验证 _search_eastmoney_free 对东财搜索 API 返回的解析与 HTML 清洗
2. 验证空结果 / 请求异常时返回 None（不破坏主流程）
3. 验证 search_stock_news 所有搜索引擎无结果时自动走东财兜底
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SearchResponse, SearchService  # noqa: E402


def _eastmoney_jsonp(title="贵州茅台：最新消息", content="内容<em>摘要</em>。", media_name="第一财经"):
    """构造东财 jsonp 响应。

    发布日期取「1 天前」的相对日期：情报搜索的严格时效过滤按 news_max_age_days
    相对当前时间截断（默认 3 天 + 1 天缓冲），若写死某个具体日期，会随时间推移
    掉出时效窗口（如 2026-08-10 在 2026-08-15 之后即被判过期）导致用例回归。
    """
    pub_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    articles = [{
        "date": pub_date,
        "title": title,
        "content": content,
        "mediaName": media_name,
        "url": "http://finance.eastmoney.com/a/20260810.html",
    }]
    return "x(" + json.dumps({
        "code": 0,
        "hitsTotal": 10,
        "result": {"cmsArticleWebOld": articles},
    }, ensure_ascii=False) + ")"


def _mock_requests_get(payload_text):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = payload_text
    return resp


class TestEastmoneyFreeFallback(unittest.TestCase):
    """直接测试 _search_eastmoney_free 方法。"""

    def _service(self):
        return SearchService(searxng_public_instances_enabled=False)

    @patch("src.search_service.requests.get")
    def test_parses_results_and_cleans_html(self, mock_get):
        mock_get.return_value = _mock_requests_get(
            _eastmoney_jsonp(title="<em>贵州茅台</em>自营渠道再提价", content="据<em>每经</em>报道...")
        )
        svc = self._service()
        resp = svc._search_eastmoney_free("600519", "贵州茅台", max_results=5)

        self.assertIsNotNone(resp)
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "东方财富")
        self.assertEqual(len(resp.results), 1)
        result = resp.results[0]
        self.assertEqual(result.title, "贵州茅台自营渠道再提价")
        self.assertNotIn("<em>", result.title)
        self.assertIn("每经", result.snippet)
        self.assertEqual(result.source, "第一财经")
        self.assertEqual(result.url, "http://finance.eastmoney.com/a/20260810.html")

    @patch("src.search_service.requests.get")
    def test_empty_results_returns_none(self, mock_get):
        mock_get.return_value = _mock_requests_get(
            "x(" + json.dumps({"code": 0, "result": {"cmsArticleWebOld": []}}) + ")"
        )
        resp = self._service()._search_eastmoney_free("600519", "贵州茅台", max_results=5)
        self.assertIsNone(resp)

    @patch("src.search_service.requests.get")
    def test_request_error_returns_none(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        resp = self._service()._search_eastmoney_free("600519", "贵州茅台", max_results=5)
        self.assertIsNone(resp)

    @patch("src.search_service.requests.get")
    def test_non_200_returns_none(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp
        result = self._service()._search_eastmoney_free("600519", "贵州茅台", max_results=5)
        self.assertIsNone(result)


class TestSearchStockNewsFallbackIntegration(unittest.TestCase):
    """验证 search_stock_news 在所有搜索引擎无结果时自动走东财兜底。"""

    def test_falls_back_when_all_providers_empty(self):
        service = SearchService(
            bocha_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
        )
        empty = SearchResponse(
            query="q",
            results=[],
            provider="Bocha",
            success=True,
            error_message="过滤后无有效新闻",
        )
        service._providers[0].search = MagicMock(return_value=empty)

        with patch("src.search_service.requests.get") as mock_get:
            mock_get.return_value = _mock_requests_get(_eastmoney_jsonp())

            response = service.search_stock_news("600519", "贵州茅台", max_results=5)

        self.assertIsNotNone(response)
        self.assertTrue(response.success)
        self.assertTrue(response.results)
        self.assertEqual(response.provider, "东方财富")

    def test_no_fallback_when_provider_has_results(self):
        service = SearchService(
            bocha_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
        )
        from datetime import date
        from src.search_service import SearchResult

        has_results = SearchResponse(
            query="q",
            results=[SearchResult(
                title="博查命中新闻",
                snippet="snippet",
                url="https://example.com/1",
                source="Bocha",
                published_date=date.today().isoformat(),
            )],
            provider="Bocha",
            success=True,
        )
        service._providers[0].search = MagicMock(return_value=has_results)

        with patch("src.search_service.requests.get") as mock_get:
            response = service.search_stock_news("600519", "贵州茅台", max_results=5)

        mock_get.assert_not_called()
        self.assertEqual(response.provider, "Bocha")
        self.assertEqual(response.results[0].title, "博查命中新闻")

    def test_comprehensive_intel_falls_back_when_provider_empty(self):
        """search_comprehensive_intel 在 provider 无结果时用东财免费资讯填充。"""
        service = SearchService(
            bocha_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
        )
        empty = SearchResponse(
            query="q",
            results=[],
            provider="Bocha",
            success=True,
            error_message="无结果",
        )
        service._providers[0].search = MagicMock(return_value=empty)

        with patch("src.search_service.requests.get") as mock_get:
            mock_get.return_value = _mock_requests_get(_eastmoney_jsonp(title="兴业银行最新消息"))
            intel = service.search_comprehensive_intel("601166", "兴业银行", max_searches=2)

        self.assertTrue(intel)
        any_hits = any(
            response.success and response.results
            for response in intel.values()
            if response is not None
        )
        self.assertTrue(any_hits, "至少一个维度应包含东财免费资讯结果")
        eastmoney_provider = any(
            response.provider == "东方财富"
            for response in intel.values()
            if response is not None
        )
        self.assertTrue(eastmoney_provider)


if __name__ == "__main__":
    unittest.main()
