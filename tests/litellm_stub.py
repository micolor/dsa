# -*- coding: utf-8 -*-
"""Shared test helper: install a litellm stub only when litellm is unavailable.

litellm 装不上的环境（裸解释器 / 精简 CI）里，靠它兜住 ``import litellm`` 的模块级
调用点。**litellm 装得上时这里什么都不做**——调用方拿到的是真模块。

不要为了"提速"再把真模块屏蔽掉：``src/services/image_stock_extractor`` 在 import 期把
``sys.modules.get("litellm")`` 绑成模块全局，任何收集期注入（``sys.modules[...] =`` /
``setdefault`` / "不在 sys.modules 就注入"）都会让
``tests/test_chat_image_vision_routing.py::test_vision_call_actually_sees_the_image``
拿到 MagicMock——那是"图真的被转发了吗"的唯一守卫，它一红，图片没被读取这类静默失效
就没人看着了。
"""

import sys
import types
from importlib import import_module


def ensure_litellm_stub() -> None:
    """Install a minimal litellm stub unless the real module is importable.

    判断依据是"litellm 到底能不能 import"，不是"它有没有已经在 sys.modules 里"。收集期的
    stub 会长期留在 sys.modules：`src/services/image_stock_extractor` 在 import 期就把
    ``sys.modules["litellm"]`` 绑成模块全局，于是 stub（它的 ``completion`` 返回 None）会让
    "图真的被转发了吗"那条网络守卫（tests/test_chat_image_vision_routing.py）在全量跑里必然
    失败——那条守卫正是"图没被转发"这类静默失效的唯一看守。
    """
    existing = sys.modules.get("litellm")
    if getattr(existing, "__dsa_test_stub__", False):
        return
    if existing is not None:
        try:
            import_module("litellm.types.utils")
            return
        except ModuleNotFoundError:
            for module_name in ("litellm.types.utils", "litellm.types", "litellm"):
                sys.modules.pop(module_name, None)

    try:
        import_module("litellm")
        return
    except ImportError:
        pass

    litellm_stub = types.ModuleType("litellm")
    litellm_stub.__dsa_test_stub__ = True

    class _DummyRouter:  # pragma: no cover
        pass

    class _DummyRateLimitError(Exception):
        pass

    class _DummyContextWindowExceededError(Exception):
        pass

    class _DummyUsage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        def dict(self):
            return dict(self.__dict__)

    litellm_types_stub = types.ModuleType("litellm.types")
    litellm_types_utils_stub = types.ModuleType("litellm.types.utils")
    litellm_types_utils_stub.Usage = _DummyUsage
    litellm_types_stub.utils = litellm_types_utils_stub

    litellm_stub.Router = _DummyRouter
    litellm_stub.RateLimitError = _DummyRateLimitError
    litellm_stub.ContextWindowExceededError = _DummyContextWindowExceededError
    litellm_stub.completion = lambda **kwargs: None
    litellm_stub.types = litellm_types_stub
    sys.modules["litellm"] = litellm_stub
    sys.modules["litellm.types"] = litellm_types_stub
    sys.modules["litellm.types.utils"] = litellm_types_utils_stub


def remove_litellm_stub() -> None:
    """Remove this stub so tests that need real LiteLLM types can import them."""
    if not getattr(sys.modules.get("litellm"), "__dsa_test_stub__", False):
        return

    for module_name in ("litellm.types.utils", "litellm.types", "litellm"):
        sys.modules.pop(module_name, None)
