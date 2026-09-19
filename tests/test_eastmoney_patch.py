# -*- coding: utf-8 -*-
"""``src/patches/eastmoney_patch.py`` 的回归测试。

覆盖两个曾经静默失效的行为：
- 授权接口失败后的退避（曾经每个请求都重打一次授权接口）；
- 响应形状不符合预期时按「取不到 NID」降级（曾经抛 TypeError 打破数据抓取）。
"""

from typing import Any, List

import pytest
import requests

from src.patches import eastmoney_patch as ep


class _StubResponse:
    """最小响应替身：只提供 _get_nid 用到的 raise_for_status / json。"""

    def __init__(self, body: Any) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例拿到干净的缓存，避免用例之间互相看到对方的退避状态。"""
    monkeypatch.setattr(ep, "_cache", ep.AuthCache())


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> List[str]:
    """把授权接口的传输换成 handler，返回记录到的 URL 列表。"""
    calls: List[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> _StubResponse:
        calls.append(url)
        return handler(method, url, **kwargs)

    monkeypatch.setattr(ep.requests, "request", fake_request)
    return calls


def test_get_nid_backs_off_after_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: Any) -> _StubResponse:
        raise requests.exceptions.ConnectTimeout("stubbed transport failure")

    calls = _patch_transport(monkeypatch, handler)

    for _ in range(5):
        assert ep._get_nid("stub-ua") is None

    # 失败分支本意是「设置较长过期时间，可避免频繁请求」，数据为 None 时
    # 若用数据本身判断缓存有效性，退避会被短路，每次都重打一次授权接口。
    assert len(calls) == 1


def test_get_nid_retries_after_the_backoff_window(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}

    def handler(method: str, url: str, **kwargs: Any) -> _StubResponse:
        if len(calls) == 1:
            raise requests.exceptions.ConnectTimeout("stubbed transport failure")
        return _StubResponse({"data": {"nid": "TOKEN-2"}})

    calls = _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(ep.time, "time", lambda: clock["now"])

    assert ep._get_nid("stub-ua") is None

    clock["now"] += 301
    assert ep._get_nid("stub-ua") == "TOKEN-2"
    assert len(calls) == 2


def test_get_nid_caches_a_successful_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: Any) -> _StubResponse:
        return _StubResponse({"data": {"nid": "TOKEN-1"}})

    calls = _patch_transport(monkeypatch, handler)

    assert ep._get_nid("stub-ua") == "TOKEN-1"
    assert ep._get_nid("stub-ua") == "TOKEN-1"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        [],
        "error",
        {"data": None},
        {"data": {}},
        {},
        {"data": {"nid": ""}},
        {"data": {"nid": 12345}},
    ],
)
def test_get_nid_treats_unexpected_response_shapes_as_failure(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    def handler(method: str, url: str, **kwargs: Any) -> _StubResponse:
        return _StubResponse(body)

    calls = _patch_transport(monkeypatch, handler)

    # 契约是「获取失败则返回 None」：形状不对要降级成「没有 NID」，
    # 不能把 TypeError / KeyError 抛进数据抓取路径。
    assert ep._get_nid("stub-ua") is None
    assert len(calls) == 1
