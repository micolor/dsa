# -*- coding: utf-8 -*-
"""docker/healthcheck.sh 的行为契约。

旧实现的检查命令末尾有无条件成功的兜底（`python -c "import sys; sys.exit(0)"`），
且探测端口写死 8000：服务容器在 API 挂死、或按文档用 API_PORT=8888 / 8080 时，
`docker ps` 依然报 healthy。这里用真实的本地 HTTP 服务验证「服务模式必须探测到
API、定时任务模式不看 HTTP」这两条契约。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = REPO_ROOT / "docker" / "healthcheck.sh"
PROC_CMDLINE_LITERAL = "open('/proc/1/cmdline', 'rb')"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _HealthDocs:
    """在临时目录里提供 /api/health 与 /health 两个 200 端点。"""

    def __init__(self, port: int, docroot: Path) -> None:
        self.port = port
        handler = partial(SimpleHTTPRequestHandler, directory=str(docroot))
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_HealthDocs":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _run_healthcheck(
    tmp_path: Path,
    cmdline_args: list[str],
    *,
    env_port: str | None = None,
    cmdline_file: Path | None = None,
) -> subprocess.CompletedProcess:
    script = HEALTHCHECK.read_text(encoding="utf-8")
    assert PROC_CMDLINE_LITERAL in script, (
        "健康检查必须从 /proc/1/cmdline 读取实际启动命令"
    )
    if cmdline_file is None:
        cmdline_file = tmp_path / "cmdline"
        cmdline_file.write_bytes(b"".join(arg.encode() + b"\0" for arg in cmdline_args))
    patched = script.replace(
        PROC_CMDLINE_LITERAL, "open(%r, 'rb')" % str(cmdline_file)
    )
    script_path = tmp_path / "healthcheck.sh"
    script_path.write_text(patched, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k not in ("API_PORT", "WEBUI_ENABLED")}
    if env_port is not None:
        env["API_PORT"] = env_port
    shell = shutil.which("sh")
    assert shell is not None
    return subprocess.run(
        [shell, str(script_path)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def health_docroot(tmp_path: Path) -> Path:
    docroot = tmp_path / "docroot"
    (docroot / "api").mkdir(parents=True)
    (docroot / "api" / "health").write_text("ok", encoding="utf-8")
    (docroot / "health").write_text("ok", encoding="utf-8")
    return docroot


def test_scheduled_container_is_healthy_without_any_http_endpoint(tmp_path: Path) -> None:
    """定时任务容器没有 HTTP 端点，健康状态只代表进程活着。"""
    port = _free_port()
    result = _run_healthcheck(
        tmp_path, ["python", "main.py", "--schedule"], env_port=str(port)
    )
    assert result.returncode == 0, result.stderr
    assert "非服务模式" in result.stdout


def test_service_container_reports_unhealthy_when_the_api_is_down(tmp_path: Path) -> None:
    """这是旧实现报 healthy 的场景：服务模式但端口上没有任何应答。"""
    port = _free_port()
    result = _run_healthcheck(
        tmp_path,
        ["python", "main.py", "--serve-only", "--host", "0.0.0.0", "--port", str(port)],
        env_port=str(port),
    )
    assert result.returncode != 0
    assert "健康检查失败" in result.stderr


def test_service_container_is_healthy_when_the_api_answers(
    tmp_path: Path, health_docroot: Path
) -> None:
    port = _free_port()
    with _HealthDocs(port, health_docroot):
        result = _run_healthcheck(
            tmp_path,
            ["python", "main.py", "--serve-only", "--host", "0.0.0.0", "--port", str(port)],
            env_port=str(port),
        )
    assert result.returncode == 0, result.stderr
    assert "/api/health" in result.stdout


def test_probe_port_follows_api_port_when_the_command_omits_it(
    tmp_path: Path, health_docroot: Path
) -> None:
    """文档里 API_PORT=8888/8080 的用法：命令没有 --port 时按 API_PORT 探测。

    旧实现写死 8000，这些部署两条 curl 都打空，然后落到「无条件健康」。
    """
    port = _free_port()
    with _HealthDocs(port, health_docroot):
        healthy = _run_healthcheck(
            tmp_path, ["python", "main.py", "--serve"], env_port=str(port)
        )
    assert healthy.returncode == 0, healthy.stderr

    dead_port = _free_port()
    down = _run_healthcheck(
        tmp_path, ["python", "main.py", "--serve"], env_port=str(dead_port)
    )
    assert down.returncode != 0


def test_probe_port_follows_the_port_flag_over_api_port(
    tmp_path: Path, health_docroot: Path
) -> None:
    """命令里的 --port 才是实际绑定值，优先级高于 API_PORT。"""
    real_port = _free_port()
    stale_port = _free_port()
    with _HealthDocs(real_port, health_docroot):
        result = _run_healthcheck(
            tmp_path,
            ["python", "main.py", "--serve-only", "--port=%d" % real_port],
            env_port=str(stale_port),
        )
    assert result.returncode == 0, result.stderr


def test_webui_enabled_env_also_counts_as_service_mode(tmp_path: Path) -> None:
    """WEBUI_ENABLED=true 会让 main.py 隐式起服务，此时必须真的探测。"""
    port = _free_port()
    script = HEALTHCHECK.read_text(encoding="utf-8")
    script_path = tmp_path / "healthcheck.sh"
    cmdline_file = tmp_path / "cmdline"
    cmdline_file.write_bytes(b"python\0main.py\0--schedule\0")
    script_path.write_text(
        script.replace(PROC_CMDLINE_LITERAL, "open(%r, 'rb')" % str(cmdline_file)),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in ("API_PORT", "WEBUI_ENABLED")}
    env["API_PORT"] = str(port)
    env["WEBUI_ENABLED"] = "true"
    result = subprocess.run(
        [shutil.which("sh"), str(script_path)], capture_output=True, text=True, env=env
    )
    assert result.returncode != 0
    assert "健康检查失败" in result.stderr


def test_unreadable_proc_cmdline_degrades_to_non_service(tmp_path: Path) -> None:
    """读不到 PID 1 命令行时不得崩，也不得虚构一个 unhealthy。"""
    missing = tmp_path / "does-not-exist"
    result = _run_healthcheck(tmp_path, [], cmdline_file=missing)
    assert result.returncode == 0, result.stderr
    assert "非服务模式" in result.stdout


def test_dockerfile_wires_the_healthcheck_script(tmp_path: Path) -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY docker/healthcheck.sh /usr/local/bin/docker-healthcheck.sh" in dockerfile
    assert "chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/docker-healthcheck.sh" in dockerfile
    assert 'CMD ["/usr/local/bin/docker-healthcheck.sh"]' in dockerfile
    # 无条件成功的兜底不得回来
    assert "sys.exit(0)" not in dockerfile
