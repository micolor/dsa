# -*- coding: utf-8 -*-
"""`/stocks/parse-import` must not run its synchronous parsing on the event loop.

`parse_import` is `async def`, so every statement between its `await`s runs on the
event loop thread.  `parse_import_from_text` / `parse_import_from_bytes` are fully
synchronous, and per-row name resolution inside them performs a synchronous AkShare
request plus a `difflib` scan over the full 5000+ name table, so a single request
starves every other request on the process' only loop (measured: ~6.4s of loop
starvation for a 200-row name-only paste, and the 100KB text / 2MB file ceilings
allow far more).
"""

import asyncio
import json
import threading

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.v1.endpoints import stocks


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/stocks/parse-import",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        receive,
    )


def _multipart_request(filename: str, data: bytes) -> Request:
    boundary = "----dsaParseImportBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: text/csv\r\n\r\n",
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/stocks/parse-import",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        receive,
    )


class _ThreadRecorder:
    """Record which thread id the parsing call ran on."""

    def __init__(self, result: list) -> None:
        self.result = result
        self.thread_ids: list[int] = []

    def __call__(self, *args, **kwargs) -> list:
        self.thread_ids.append(threading.get_ident())
        return self.result


def test_parse_import_text_runs_off_the_event_loop(monkeypatch) -> None:
    recorder = _ThreadRecorder([("600519", "贵州茅台", "high")])
    monkeypatch.setattr(stocks, "parse_import_from_text", recorder)
    loop_thread_id = threading.get_ident()

    response = asyncio.run(stocks.parse_import(_json_request({"text": "600519\n"})))

    assert recorder.thread_ids, "parse_import_from_text was never called"
    assert recorder.thread_ids[0] != loop_thread_id
    assert response.codes == ["600519"]
    assert [item.name for item in response.items] == ["贵州茅台"]


def test_parse_import_file_runs_off_the_event_loop(monkeypatch) -> None:
    recorder = _ThreadRecorder([("600519", "贵州茅台", "high")])
    monkeypatch.setattr(stocks, "parse_import_from_bytes", recorder)
    loop_thread_id = threading.get_ident()

    response = asyncio.run(stocks.parse_import(_multipart_request("a.csv", b"code,name\n600519,x\n")))

    assert recorder.thread_ids, "parse_import_from_bytes was never called"
    assert recorder.thread_ids[0] != loop_thread_id
    assert response.codes == ["600519"]
    assert [item.name for item in response.items] == ["贵州茅台"]


def test_parse_import_keeps_rejecting_oversized_text() -> None:
    # The size guards still run on the request path they guarded before.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.parse_import(_json_request({"text": "x" * (101 * 1024)})))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "too_large"
