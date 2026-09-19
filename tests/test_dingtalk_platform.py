# -*- coding: utf-8 -*-
"""钉钉 Webhook 适配器签名校验。

校验失败必须一律拒绝（fail-closed），与 Discord 适配器保持一致：
「没配置密钥就不校验」等于把回调地址当成了凭据。
"""
import base64
import hashlib
import hmac
import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

from bot.models import ChatType
from bot.platforms.dingtalk import DingtalkPlatform

APP_SECRET = "dingtalk-test-app-secret"


def _make_platform(app_secret: str | None) -> DingtalkPlatform:
    with patch(
        "src.config.get_config",
        return_value=SimpleNamespace(
            dingtalk_app_key="dingtalk-test-app-key",
            dingtalk_app_secret=app_secret,
        ),
    ):
        return DingtalkPlatform()


def _current_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def _sign(timestamp: str, app_secret: str = APP_SECRET) -> str:
    digest = hmac.new(
        app_secret.encode("utf-8"),
        f"{timestamp}\n{app_secret}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _signed_headers(body: bytes, timestamp: str | None = None) -> dict:
    if timestamp is None:
        timestamp = _current_timestamp_ms()
    return {"timestamp": timestamp, "sign": _sign(timestamp)}


def _message_payload() -> dict:
    return {
        "msgtype": "text",
        "text": {"content": "@机器人 /analyze 600519"},
        "msgId": "msg-1",
        "createAt": "1758200000000",
        "conversationType": "2",
        "conversationId": "cid-1",
        "senderId": "uid-1",
        "senderNick": "tester",
        "atUsers": [{"dingtalkId": "bot-1"}],
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=x",
    }


def test_signed_request_is_accepted_and_parsed():
    platform = _make_platform(APP_SECRET)
    payload = _message_payload()
    body = b'{"msgtype": "text"}'

    message, response = platform.handle_webhook(_signed_headers(body), body, payload)

    assert response is None
    assert message is not None
    assert message.chat_type == ChatType.GROUP
    assert message.content == "/analyze 600519"


def test_missing_app_secret_rejects_the_request():
    platform = _make_platform(None)
    payload = _message_payload()
    body = b"{}"

    assert platform.verify_request(_signed_headers(body), body) is False
    message, response = platform.handle_webhook(_signed_headers(body), body, payload)
    assert message is None
    assert response is not None
    assert response.status_code == 403


def test_missing_signature_headers_reject_the_request():
    platform = _make_platform(APP_SECRET)
    body = b"{}"

    assert platform.verify_request({}, body) is False
    assert platform.verify_request({"timestamp": _current_timestamp_ms()}, body) is False
    assert platform.verify_request({"sign": "whatever"}, body) is False

    message, response = platform.handle_webhook({}, body, _message_payload())
    assert message is None
    assert response is not None
    assert response.status_code == 403


def test_invalid_signature_rejects_the_request():
    platform = _make_platform(APP_SECRET)
    headers = _signed_headers(b"{}")
    headers["sign"] = _sign(headers["timestamp"], "some-other-secret")

    assert platform.verify_request(headers, b"{}") is False


def test_expired_timestamp_rejects_the_request():
    platform = _make_platform(APP_SECRET)
    stale = str(int(time.time() * 1000) - 2 * 3600 * 1000)

    assert platform.verify_request(_signed_headers(b"{}", timestamp=stale), b"{}") is False


def test_non_numeric_timestamp_rejects_the_request():
    platform = _make_platform(APP_SECRET)

    assert platform.verify_request({"timestamp": "not-a-number", "sign": "x"}, b"{}") is False


def test_header_names_are_matched_case_insensitively():
    """HTTP 头名大小写不敏感：合法签名不应被误判成缺少签名。"""
    platform = _make_platform(APP_SECRET)
    headers = _signed_headers(b"{}")
    upper = {key.upper(): value for key, value in headers.items()}

    assert platform.verify_request(upper, b"{}") is True


def _error_records(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_missing_app_secret_is_reported_once_as_an_actionable_error(caplog):
    """fail-closed 的代价是回调静默 403，运维必须能在 ERROR 级别看到怎么修。"""
    platform = _make_platform(None)

    with caplog.at_level(logging.ERROR, logger="bot.platforms.dingtalk"):
        assert platform.verify_request(_signed_headers(b"{}"), b"{}") is False
        assert platform.verify_request(_signed_headers(b"{}"), b"{}") is False

    errors = _error_records(caplog)
    assert len(errors) == 1, "配置类拒绝只应记一次，避免未鉴权请求刷满日志"
    message = errors[0].getMessage()
    assert "DINGTALK_APP_SECRET" in message
    assert "加签" in message


def test_missing_signature_headers_are_reported_once_as_an_actionable_error(caplog):
    platform = _make_platform(APP_SECRET)

    with caplog.at_level(logging.ERROR, logger="bot.platforms.dingtalk"):
        assert platform.verify_request({}, b"{}") is False
        assert platform.verify_request({}, b"{}") is False

    errors = _error_records(caplog)
    assert len(errors) == 1
    assert "加签" in errors[0].getMessage()


def test_forged_signature_is_not_reported_as_a_configuration_error(caplog):
    """签名不符是请求问题，不是配置问题：保持逐条 warning，不占用 ERROR。"""
    platform = _make_platform(APP_SECRET)
    headers = _signed_headers(b"{}")
    headers["sign"] = _sign(headers["timestamp"], "some-other-secret")

    with caplog.at_level(logging.WARNING, logger="bot.platforms.dingtalk"):
        assert platform.verify_request(headers, b"{}") is False

    assert _error_records(caplog) == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)
