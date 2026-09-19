# -*- coding: utf-8 -*-
"""Integration tests for auth API endpoints (login, logout, change-password, API protection)."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from dotenv import dotenv_values
from fastapi.responses import Response
from starlette.requests import Request

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.middlewares.auth import AuthMiddleware
from api.v1.endpoints import auth as auth_endpoint
from src.config import Config


def _extract_session_cookie(response) -> str:
    """Read the dsa_session value out of a response's Set-Cookie header."""
    header = response.headers.get("set-cookie", "")
    name, _, value = header.split(";", 1)[0].partition("=")
    assert name == auth_endpoint.COOKIE_NAME, f"unexpected cookie in response: {header!r}"
    return value.strip().strip('"')


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class AuthApiTestCase(unittest.TestCase):
    """Integration tests for /api/v1/auth/* and API protection."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.data_dir / "test.db")
        Config.reset_instance()

        self.auth_patcher = patch.object(auth, "_is_auth_enabled_from_env", return_value=True)
        self.data_dir_patcher = patch.object(auth, "_get_data_dir", return_value=self.data_dir)
        self.auth_patcher.start()
        self.data_dir_patcher.start()

    def tearDown(self) -> None:
        self.auth_patcher.stop()
        self.data_dir_patcher.stop()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _read_auth_enabled_from_env(self) -> bool:
        values = dotenv_values(self.env_path)
        return (values.get("ADMIN_AUTH_ENABLED") or "").strip().lower() in ("true", "1", "yes")

    @staticmethod
    def _build_request(cookies=None):
        return SimpleNamespace(
            headers={},
            url=SimpleNamespace(scheme="http"),
            cookies=cookies or {},
            client=SimpleNamespace(host="127.0.0.1"),
        )

    def test_auth_status_when_password_not_set(self) -> None:
        data = asyncio.run(auth_endpoint.auth_status(self._build_request()))
        self.assertTrue(data["authEnabled"])
        self.assertFalse(data["passwordSet"])
        self.assertFalse(data["loggedIn"])

    def test_login_first_time_set_initial_password(self) -> None:
        response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="newpass123", passwordConfirm="newpass123"),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("dsa_session=", response.headers["set-cookie"])
        self.assertIn(b'"ok":true', response.body)

    def test_login_first_time_mismatch_rejected(self) -> None:
        response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="pass1", passwordConfirm="pass2"),
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"password_mismatch"', response.body)

    def test_login_after_set_normal_login(self) -> None:
        first_response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="mypass456", passwordConfirm="mypass456"),
            )
        )
        self.assertEqual(first_response.status_code, 200)

        response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="mypass456"),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"ok":true', response.body)

    def test_login_wrong_password_returns_401(self) -> None:
        first_response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="correct", passwordConfirm="correct"),
            )
        )
        self.assertEqual(first_response.status_code, 200)

        response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="wrong"),
            )
        )
        self.assertEqual(response.status_code, 401)

    def test_logout_clears_cookie(self) -> None:
        response = asyncio.run(auth_endpoint.auth_logout(self._build_request()))
        self.assertEqual(response.status_code, 204)
        self.assertIn("dsa_session=", response.headers["set-cookie"])

    def test_logout_invalidates_existing_session(self) -> None:
        login_response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="passwd6", passwordConfirm="passwd6"),
            )
        )
        self.assertEqual(login_response.status_code, 200)
        cookie_header = login_response.headers["set-cookie"]
        session_cookie = cookie_header.split("dsa_session=", 1)[1].split(";", 1)[0]
        self.assertTrue(auth.verify_session(session_cookie))

        logout_response = asyncio.run(auth_endpoint.auth_logout(self._build_request()))

        self.assertEqual(logout_response.status_code, 204)
        self.assertFalse(auth.verify_session(session_cookie))

    def test_logout_returns_500_when_session_invalidation_fails(self) -> None:
        with patch.object(auth_endpoint, "rotate_session_secret", return_value=False):
            response = asyncio.run(auth_endpoint.auth_logout(self._build_request()))

        self.assertEqual(response.status_code, 500)
        self.assertIn(b'"error":"internal_error"', response.body)

    def test_change_password_requires_session(self) -> None:
        first_response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="oldpass6", passwordConfirm="oldpass6"),
            )
        )
        self.assertEqual(first_response.status_code, 200)

        response = asyncio.run(
            auth_endpoint.auth_change_password(
                self._build_request(),
                auth_endpoint.ChangePasswordRequest(
                    currentPassword="oldpass6",
                    newPassword="newpass6",
                    newPasswordConfirm="newpass6",
                )
            )
        )
        self.assertIn(response.status_code, (200, 204))

    def test_change_password_wrong_current_rejected(self) -> None:
        first_response = asyncio.run(
            auth_endpoint.auth_login(
                self._build_request(),
                auth_endpoint.LoginRequest(password="actual6", passwordConfirm="actual6"),
            )
        )
        self.assertEqual(first_response.status_code, 200)

        response = asyncio.run(
            auth_endpoint.auth_change_password(
                self._build_request(),
                auth_endpoint.ChangePasswordRequest(
                    currentPassword="wrong",
                    newPassword="new123",
                    newPasswordConfirm="new123",
                )
            )
        )
        self.assertEqual(response.status_code, 400)

    def _change_password(self, request, current: str, new: str):
        """Invoke the change-password handler with a matching confirm field."""
        return asyncio.run(
            auth_endpoint.auth_change_password(
                request,
                auth_endpoint.ChangePasswordRequest(
                    currentPassword=current,
                    newPassword=new,
                    newPasswordConfirm=new,
                ),
            )
        )

    def test_change_password_rotates_session_and_keeps_caller_logged_in(self) -> None:
        """改密成功后必须轮换会话秘钥，并给调用者重发一个可用会话。

        只改密码不轮换秘钥时，改密前签发的 Cookie 仍然有效——旧会话（例如被泄露的
        那个）在改密后照样能用，等于补救手段失效。轮换又会连带失效调用者自己的
        Cookie，所以同一个响应里必须补发新会话，操作者不能把自己踢下线。
        """
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            login_resp = asyncio.run(
                auth_endpoint.auth_login(
                    self._build_request(),
                    auth_endpoint.LoginRequest(password="oldpass6", passwordConfirm="oldpass6"),
                )
            )
            self.assertEqual(login_resp.status_code, 200)
            old_session = _extract_session_cookie(login_resp)
            self.assertTrue(auth.verify_session(old_session))

            response = self._change_password(
                self._build_request(cookies={auth_endpoint.COOKIE_NAME: old_session}),
                "oldpass6",
                "newpass6",
            )

            self.assertEqual(response.status_code, 204)
            new_session = _extract_session_cookie(response)
            self.assertNotEqual(new_session, old_session)
            # 轮换后旧会话立即失效，其它已登录会话被踢下线
            self.assertFalse(auth.verify_session(old_session))
            # 调用者拿到的新会话是有效的，不需要重新登录
            self.assertTrue(auth.verify_session(new_session))
            self.assertTrue(auth.verify_stored_password("newpass6"))
            self.assertFalse(auth.verify_stored_password("oldpass6"))

    def test_change_password_wrong_current_triggers_rate_limit_429(self) -> None:
        """反复输错当前密码必须累积到限流表并触发 429。

        与 /login、/settings 共用同一张限流表；这里的失败次数全部来自真实的
        认证失败（当前密码错误），不是预填状态。
        """
        from src.auth import RATE_LIMIT_MAX_FAILURES

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            auth.set_initial_password("oldpass6")

            responses = [
                self._change_password(self._build_request(), "wrongpass", "newpass6")
                for _ in range(RATE_LIMIT_MAX_FAILURES + 1)
            ]

            self.assertEqual(
                [r.status_code for r in responses[:-1]],
                [400] * RATE_LIMIT_MAX_FAILURES,
            )
            self.assertEqual(responses[-1].status_code, 429)
            self.assertIn(b'"error":"rate_limited"', responses[-1].body)
            # 限流拦截不能顺手把密码改掉
            self.assertTrue(auth.verify_stored_password("oldpass6"))

    def test_change_password_invalid_new_password_does_not_consume_rate_limit(self) -> None:
        """新密码不合规不是认证失败，不应把用户推进限流。

        同一语义的两种失败必须分开记账：否则攻击者/手抖用户只要连续提交短密码，
        就能凭新密码校验失败把自己（或别人）锁进 429。
        """
        from src.auth import RATE_LIMIT_MAX_FAILURES

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            auth.set_initial_password("oldpass6")

            responses = [
                self._change_password(self._build_request(), "oldpass6", "abc")
                for _ in range(RATE_LIMIT_MAX_FAILURES + 1)
            ]

            self.assertEqual([r.status_code for r in responses], [400] * (RATE_LIMIT_MAX_FAILURES + 1))
            self.assertTrue(all(b'"error":"invalid_password"' in r.body for r in responses))
            self.assertNotIn("429", {str(r.status_code) for r in responses})
            # 密码未被改动，且正确密码依然可以用
            self.assertTrue(auth.verify_stored_password("oldpass6"))

    def test_change_password_rotation_failure_reports_error_after_password_changed(self) -> None:
        """轮换失败返回 500，但不能谎报密码没改。

        密码已经落盘，此时没有可用的回滚路径（旧哈希不在手上）；如实返回错误并说明
        其它会话仍然有效，比假装成功（静默降级）更能让操作者判断下一步。
        """
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            auth.set_initial_password("oldpass6")
            with patch.object(auth_endpoint, "rotate_session_secret", return_value=False):
                response = self._change_password(self._build_request(), "oldpass6", "newpass6")

            self.assertEqual(response.status_code, 500)
            self.assertIn(b'"error":"internal_error"', response.body)
            self.assertTrue(auth.verify_stored_password("newpass6"))
            self.assertFalse(auth.verify_stored_password("oldpass6"))

    def test_protected_api_returns_401_without_session(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/system/config",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
        request = Request(scope)
        middleware = AuthMiddleware(app=MagicMock())

        with patch("api.middlewares.auth.is_auth_enabled", return_value=True):
            response = asyncio.run(middleware.dispatch(request, AsyncMock(return_value=Response(status_code=200))))

        self.assertEqual(response.status_code, 401)

    def test_logout_requires_session_when_auth_enabled(self) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/logout",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
        request = Request(scope)
        middleware = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=204))

        with patch("api.middlewares.auth.is_auth_enabled", return_value=True):
            response = asyncio.run(middleware.dispatch(request, call_next))

        self.assertEqual(response.status_code, 401)
        call_next.assert_not_awaited()

    def test_protected_api_accessible_with_session(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/system/config",
            "headers": [(b"cookie", b"dsa_session=test-session")],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
        request = Request(scope)
        middleware = AuthMiddleware(app=MagicMock())
        next_response = Response(status_code=200)
        call_next = AsyncMock(return_value=next_response)

        with patch("api.middlewares.auth.is_auth_enabled", return_value=True):
            with patch("api.middlewares.auth.verify_session", return_value=True):
                response = asyncio.run(middleware.dispatch(request, call_next))

        self.assertEqual(response.status_code, 200)
        call_next.assert_awaited_once()

    def test_auth_settings_requires_session_when_auth_enabled(self) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/settings",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
        request = Request(scope)
        middleware = AuthMiddleware(app=MagicMock())

        with patch("api.middlewares.auth.is_auth_enabled", return_value=True):
            response = asyncio.run(middleware.dispatch(request, AsyncMock(return_value=Response(status_code=200))))

        self.assertEqual(response.status_code, 401)

    def test_auth_settings_is_reachable_when_auth_disabled(self) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/settings",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
        request = Request(scope)
        middleware = AuthMiddleware(app=MagicMock())
        next_response = Response(status_code=200)
        call_next = AsyncMock(return_value=next_response)

        with patch("api.middlewares.auth.is_auth_enabled", return_value=False):
            response = asyncio.run(middleware.dispatch(request, call_next))

        self.assertEqual(response.status_code, 200)
        call_next.assert_awaited_once()

    def test_auth_settings_enable_sets_initial_password_and_logs_in(self) -> None:
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()

            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(
                        authEnabled=True,
                        password="initpass123",
                        passwordConfirm="initpass123",
                    ),
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"authEnabled":true', response.body)
        self.assertIn(b'"loggedIn":true', response.body)
        self.assertIn(b'"passwordSet":true', response.body)
        self.assertIn("dsa_session=", response.headers["set-cookie"])
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_enable_requires_password_when_missing(self) -> None:
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()

            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=True),
                )
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"password_required"', response.body)

    def test_auth_settings_rechecks_password_before_initial_write(self) -> None:
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()

            with patch.object(
                auth_endpoint,
                "has_stored_password",
                side_effect=[False, True],
            ) as has_password_mock:
                with patch.object(auth_endpoint, "set_initial_password") as set_password_mock:
                    response = asyncio.run(
                        auth_endpoint.auth_update_settings(
                            self._build_request(),
                            auth_endpoint.AuthSettingsRequest(
                                authEnabled=True,
                                password="initpass123",
                                passwordConfirm="initpass123",
                            ),
                        )
                    )

        self.assertEqual(has_password_mock.call_count, 2)
        set_password_mock.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"password_already_set"', response.body)

    def test_auth_settings_disable_clears_cookie_and_hides_password_state(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"authEnabled":false', response.body)
        self.assertIn(b'"loggedIn":false', response.body)
        self.assertIn(b'"passwordSet":false', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=false", self.env_path.read_text(encoding="utf-8"))
        self.assertIn("dsa_session=", response.headers["set-cookie"])

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            status_response = asyncio.run(auth_endpoint.auth_status(self._build_request()))
        self.assertFalse(status_response["authEnabled"])
        self.assertFalse(status_response["passwordSet"])

    def test_auth_settings_disable_requires_current_password_when_auth_enabled(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False),
                )
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"current_required"', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_toggle_fails_when_secret_rotation_fails(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            with patch.object(auth_endpoint, "rotate_session_secret", return_value=False):
                response = asyncio.run(
                    auth_endpoint.auth_update_settings(
                        self._build_request(),
                        auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                    )
                )

        self.assertEqual(response.status_code, 500)
        self.assertIn(b'"error":"internal_error"', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_enable_with_existing_password_reuses_stored_password(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            disable_response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                )
            )
        self.assertEqual(disable_response.status_code, 200)

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            enable_response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=True, currentPassword="passwd6"),
                )
            )

        self.assertEqual(enable_response.status_code, 200)
        self.assertIn(b'"authEnabled":true', enable_response.body)
        self.assertIn(b'"passwordSet":true', enable_response.body)
        self.assertIn(b'"loggedIn":true', enable_response.body)
        self.assertIn("dsa_session=", enable_response.headers["set-cookie"])

    def test_auth_settings_enable_with_existing_password_requires_current_password(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            disable_response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                )
            )
        self.assertEqual(disable_response.status_code, 200)

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=True),
                )
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"current_required"', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=false", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_enable_with_existing_password_rejects_wrong_current_password(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            disable_response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                )
            )
        self.assertEqual(disable_response.status_code, 200)

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=True, currentPassword="wrongpass"),
                )
            )

        self.assertEqual(response.status_code, 401)
        self.assertIn(b'"error":"invalid_password"', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=false", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_enable_rolls_back_when_session_creation_fails(self) -> None:
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            with patch.object(auth_endpoint, "create_session", return_value=""):
                response = asyncio.run(
                    auth_endpoint.auth_update_settings(
                        self._build_request(),
                        auth_endpoint.AuthSettingsRequest(
                            authEnabled=True,
                            password="initpass123",
                            passwordConfirm="initpass123",
                        ),
                    )
                )

        self.assertEqual(response.status_code, 500)
        self.assertIn(b'"error":"internal_error"', response.body)
        self.assertIn("ADMIN_AUTH_ENABLED=false", self.env_path.read_text(encoding="utf-8"))

    def test_auth_settings_rejects_overwriting_existing_password(self) -> None:
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.set_initial_password("passwd6")
            disable_response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(authEnabled=False, currentPassword="passwd6"),
                )
            )
            self.assertEqual(disable_response.status_code, 200)

        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(),
                    auth_endpoint.AuthSettingsRequest(
                        authEnabled=True,
                        password="newpass123",
                        passwordConfirm="newpass123",
                    ),
                )
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"password_already_set"', response.body)

    def test_auth_settings_enable_requires_valid_session_cookie_against_toctou(self) -> None:
        """Verify fix for P1 vulnerability: passing authEnabled=True without currentPassword
        must be rejected if the caller lacks a cryptographically valid session, even if
        is_auth_enabled() evaluates to True during handler execution (TOCTOU race condition).
        """
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            # 1. Setup an existing password, auth is currently disabled
            auth.set_initial_password("passwd6")
            
            # 2. Simulate the race condition:
            # The middleware let the request through because auth was supposedly False.
            # But just before the handler runs, another thread enables auth.
            self.env_path.write_text(
                "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
                encoding="utf-8",
            )
            auth.refresh_auth_state() # simulate the flip to True

            # 3. The attacker tries to re-enable auth without a password or valid cookie
            response = asyncio.run(
                auth_endpoint.auth_update_settings(
                    self._build_request(cookies={"dsa_session": "invalid"}),
                    auth_endpoint.AuthSettingsRequest(authEnabled=True),
                )
            )

        # 4. Must be rejected because they lack a valid session + NO current_password
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'"error":"current_required"', response.body)

    # --- Issue #1970 hardening: disable auth must enforce current-password re-auth
    # regardless of whether the request carries a cryptographically valid session cookie.

    def _auth_setup_with_stored_password(self):
        """Set up enabled auth + stored admin password shared by the disable tests."""
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            auth.refresh_auth_state()
            auth.set_initial_password("passwd6")

    def test_disable_auth_repeated_wrong_password_triggers_rate_limit_429(self):
        """Repeated invalid currentPassword attempts on the disable path must trigger 429.

        Drives the disable path through ``RATE_LIMIT_MAX_FAILURES`` consecutive
        wrong-password attempts that record failures, plus one more request
        that should now be rejected because the in-process ``auth._rate_limit``
        map has accumulated to the threshold. The final attempt should return
        429 with ``rate_limited`` error and ``ADMIN_AUTH_ENABLED=true``,
        proving the disable path actually accumulates failures through the
        shared rate-limit table rather than prefilled state.

        This is the handler-level rate-limit branch test; the higher-level
        "valid session cookie must not bypass currentPassword" contract is
        covered by ``AuthDisableViaRealASGITestCase``.
        """
        self._auth_setup_with_stored_password()
        with patch.object(auth, "_is_auth_enabled_from_env", side_effect=self._read_auth_enabled_from_env):
            with patch.object(auth_endpoint, "verify_stored_password", return_value=False):
                from src.auth import RATE_LIMIT_MAX_FAILURES

                responses: list = []
                # First RATE_LIMIT_MAX_FAILURES attempts: each records a
                # failure and returns 401 invalid_password.
                # The next attempt (RATE_LIMIT_MAX_FAILURES + 1) enters
                # check_rate_limit which now sees count >= MAX and returns
                # 429 rate_limited before reaching verify_stored_password.
                for _ in range(RATE_LIMIT_MAX_FAILURES + 1):
                    response = asyncio.run(
                        auth_endpoint.auth_update_settings(
                            self._build_request(),
                            auth_endpoint.AuthSettingsRequest(
                                authEnabled=False,
                                currentPassword="wrongpass",
                            ),
                        )
                    )
                    responses.append(response)

            # The first RATE_LIMIT_MAX_FAILURES attempts should be 401
            # invalid_password (each one records a failure); the final
            # attempt should be 429 rate_limited, proving the disable
            # path actually accumulates failures through the shared
            # rate-limit table rather than prefilled state.
            self.assertEqual(
                [r.status_code for r in responses[:-1]],
                [401] * RATE_LIMIT_MAX_FAILURES,
            )
            self.assertEqual(responses[-1].status_code, 429)
            self.assertIn(b'"error":"rate_limited"', responses[-1].body)
            self.assertIn(
                "ADMIN_AUTH_ENABLED=true",
                self.env_path.read_text(encoding="utf-8"),
            )


class AuthDisableViaRealASGITestCase(unittest.TestCase):
    """End-to-end regression tests through the real ASGI / AuthMiddleware stack.

    Issue #1970 / PR #2050: a leaked session cookie alone must NEVER be enough to
    disable auth — `currentPassword` must be enforced on the disable path even when
    the request carries a cryptographically valid session.

    These tests deliberately exercise the full ``create_app`` + ``AuthMiddleware`` +
    ``api.v1.endpoints.auth.router`` composition via httpx.ASGITransport (the same
    Starlette TestClient path used by ``tests/test_api_health.py``), instead of
    invoking the handler directly. They log in via the real ``POST /api/v1/auth/login``
    endpoint to obtain a genuine signed cookie, then issue ``POST /api/v1/auth/settings``
    with ``authEnabled=false`` to confirm the disable contract under the real
    middleware + endpoint combination path.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._temp_dir.name)
        cls.env_path = cls.data_dir / ".env"
        cls.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(cls.env_path)
        os.environ["DATABASE_PATH"] = str(cls.data_dir / "test.db")
        # ADMIN_AUTH_ENABLED 以进程环境优先，与 .env 里的值保持一致，避免开发机
        # 真实 .env 泄漏进来的值把这条前置条件顶掉。
        os.environ["ADMIN_AUTH_ENABLED"] = "true"
        Config.reset_instance()

        cls._data_dir_patcher = patch.object(
            auth, "_get_data_dir", return_value=cls.data_dir
        )
        cls._data_dir_patcher.start()

        _reset_auth_globals()
        auth.refresh_auth_state()
        auth.set_initial_password("passwd6")

        # Minimal create_app: static_dir pointed at the temp data dir so the
        # frontend-asset consistency check has nothing to scan.
        from api.app import create_app
        from fastapi.testclient import TestClient
        cls.client = TestClient(create_app(static_dir=cls.data_dir))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._data_dir_patcher.stop()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("ADMIN_AUTH_ENABLED", None)
        _reset_auth_globals()
        cls._temp_dir.cleanup()

    def setUp(self) -> None:
        # Each test starts from auth-enabled + a stored password; the disable
        # path mutates the .env (and syncs the process env) so we restore both
        # before every test.
        _reset_auth_globals()
        os.environ["ADMIN_AUTH_ENABLED"] = "true"
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        Config.reset_instance()
        auth.refresh_auth_state()
        if not auth.has_stored_password():
            auth.set_initial_password("passwd6")

    def _login_for_session(self) -> None:
        """Authenticate via the real /api/v1/auth/login endpoint.

        The TestClient persists cookies across requests; we do not need to
        return them — assertions about cookie state after disable read
        ``self.client.cookies`` directly.
        """
        login_resp = self.client.post(
            "/api/v1/auth/login",
            json={"password": "passwd6"},
        )
        self.assertEqual(login_resp.status_code, 200, login_resp.text)

    def test_disable_via_real_asgi_with_valid_session_but_no_current_password_returns_400(self):
        """Real middleware + endpoint: valid session cookie + no currentPassword -> 400.

        This is the regression the Issue #1970 fix introduces: a leaked session
        cookie alone MUST NOT be enough to flip the system into unauthenticated
        mode. The HTTP-level contract surfaces as a 400 ``current_required`` from
        the endpoint (after middleware has admitted the request because the
        session cookie is cryptographically valid).
        """
        self._login_for_session()
        resp = self.client.post(
            "/api/v1/auth/settings",
            json={"authEnabled": False},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json().get("error"), "current_required")
        # Auth must remain enabled because the request was rejected.
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))

    def test_disable_via_real_asgi_with_valid_session_and_correct_current_password_succeeds(self):
        """Real middleware + endpoint: valid session + correct currentPassword -> 200.

        Positive path: a logged-in admin who supplies the correct currentPassword
        can disable auth, the .env flips to ADMIN_AUTH_ENABLED=false, the server
        rotates the session secret, and the response instructs the client to
        drop the existing dsa_session cookie. A leaked pre-disable cookie must
        NOT remain usable after this response, so we assert:

        1. The Set-Cookie header carries ``dsa_session=`` with an empty value
           (or a deletion-form cookie), not a fresh authenticated session id.
        2. The header contains ``Max-Age=0`` or an ``Expires`` date in the past
           — the standard cookie-deletion semantics used by ``delete_cookie``.
        3. The TestClient cookie jar drops the ``dsa_session`` cookie after
           the response, so subsequent requests in this client context no
           longer carry it.
        """
        self._login_for_session()
        # Sanity: the login flow left a session cookie in the jar.
        self.assertIn("dsa_session", self.client.cookies)
        pre_disable_cookie = self.client.cookies.get("dsa_session")
        self.assertTrue(pre_disable_cookie)

        resp = self.client.post(
            "/api/v1/auth/settings",
            json={"authEnabled": False, "currentPassword": "passwd6"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body.get("authEnabled"))
        self.assertFalse(body.get("loggedIn"))
        self.assertIn("ADMIN_AUTH_ENABLED=false", self.env_path.read_text(encoding="utf-8"))

        # 1. Cookie value is empty / deletion-form — not a fresh session id.
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("dsa_session=", set_cookie)
        # ``delete_cookie`` emits ``dsa_session=; Max-Age=0; ...`` (empty value);
        # a newly minted session would carry a long signed token instead.
        # Split on the first ';' to isolate the ``name=value`` pair, then take
        # the value side. Starlette's delete_cookie emits an empty value but
        # may quote it; strip surrounding double quotes before comparing.
        cookie_pair = set_cookie.split(";", 1)[0]
        cookie_value = cookie_pair.split("=", 1)[1] if "=" in cookie_pair else ""
        cookie_value = cookie_value.strip().strip('"')
        self.assertEqual(
            cookie_value,
            "",
            f"expected empty dsa_session value (cookie deletion form), got: {cookie_value!r}",
        )

        # 2. Cookie carries Max-Age=0 OR an Expires date in the past —
        #    the standard cookie-deletion semantics used by ``delete_cookie``.
        set_cookie_lower = set_cookie.lower()
        has_max_age_zero = "max-age=0" in set_cookie_lower
        has_expires_past = "expires=" in set_cookie_lower and ("1970" in set_cookie_lower or "01 jan 1970" in set_cookie_lower)
        self.assertTrue(
            has_max_age_zero or has_expires_past,
            f"expected cookie deletion header (Max-Age=0 / Expires in the past), got: {set_cookie!r}",
        )

        # 3. The TestClient cookie jar should no longer carry dsa_session
        #    after processing the deletion response. This proves the
        #    authenticated jar state has actually been cleared, not just
        #    overwritten with a new value.
        self.assertNotIn("dsa_session", self.client.cookies)


class AuthChangePasswordViaRealASGITestCase(unittest.TestCase):
    """End-to-end change-password through the real ASGI / AuthMiddleware stack.

    The handler-level tests above call ``auth_change_password`` directly, so they
    cannot show what an actual client experiences. This class logs in over HTTP,
    changes the password over HTTP, and then replays the pre-change cookie to prove
    it is rejected by ``AuthMiddleware`` — the user-visible contract behind
    "changing the password logs out other sessions".
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._temp_dir.name)
        cls.env_path = cls.data_dir / ".env"
        cls.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(cls.env_path)
        os.environ["DATABASE_PATH"] = str(cls.data_dir / "test.db")
        # ADMIN_AUTH_ENABLED 以进程环境优先，与 .env 里的值保持一致，避免开发机
        # 真实 .env 泄漏进来的值把这条前置条件顶掉。
        os.environ["ADMIN_AUTH_ENABLED"] = "true"
        Config.reset_instance()

        cls._data_dir_patcher = patch.object(
            auth, "_get_data_dir", return_value=cls.data_dir
        )
        cls._data_dir_patcher.start()

        _reset_auth_globals()
        auth.refresh_auth_state()

        # Minimal create_app: static_dir pointed at the temp data dir so the
        # frontend-asset consistency check has nothing to scan.
        from api.app import create_app
        from fastapi.testclient import TestClient
        cls.client = TestClient(create_app(static_dir=cls.data_dir))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._data_dir_patcher.stop()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("ADMIN_AUTH_ENABLED", None)
        _reset_auth_globals()
        cls._temp_dir.cleanup()

    def setUp(self) -> None:
        _reset_auth_globals()
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        Config.reset_instance()
        auth.refresh_auth_state()
        # Unconditional: the change-password test below rewrites the credential,
        # so every test must start from a known password.
        auth.set_initial_password("passwd6")
        self.client.cookies.clear()

    def test_change_password_via_real_asgi_invalidates_old_session(self) -> None:
        login_resp = self.client.post("/api/v1/auth/login", json={"password": "passwd6"})
        self.assertEqual(login_resp.status_code, 200, login_resp.text)
        old_cookie = _extract_session_cookie(login_resp)

        # Sanity: the login cookie actually opens a protected endpoint.
        opened_before = self.client.get("/api/v1/system/config")
        self.assertEqual(opened_before.status_code, 200, opened_before.text)

        change_resp = self.client.post(
            "/api/v1/auth/change-password",
            json={
                "currentPassword": "passwd6",
                "newPassword": "passwd9",
                "newPasswordConfirm": "passwd9",
            },
        )
        self.assertEqual(change_resp.status_code, 204, change_resp.text)
        new_cookie = _extract_session_cookie(change_resp)
        self.assertNotEqual(new_cookie, old_cookie)

        # The caller keeps working without re-logging in...
        opened_after = self.client.get("/api/v1/system/config")
        self.assertEqual(opened_after.status_code, 200, opened_after.text)

        # ...while the pre-change cookie is now dead as far as the middleware cares.
        self.client.cookies.set("dsa_session", old_cookie)
        replayed = self.client.get("/api/v1/system/config")
        self.assertEqual(replayed.status_code, 401, replayed.text)

        # And the old password no longer logs in.
        self.client.cookies.clear()
        relogin = self.client.post("/api/v1/auth/login", json={"password": "passwd6"})
        self.assertEqual(relogin.status_code, 401, relogin.text)
        new_login = self.client.post("/api/v1/auth/login", json={"password": "passwd9"})
        self.assertEqual(new_login.status_code, 200, new_login.text)


if __name__ == "__main__":
    unittest.main()
