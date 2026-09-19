# -*- coding: utf-8 -*-
"""每个注册工具都必须有中文标签，否则界面会露出工具的内部英文 id。

两处标签表服务于不同位置：``runner._THINKING_TOOL_LABELS`` 用于「思考过程」里
「「X」已完成，继续深入分析…」这类叙述，``api.v1.endpoints.agent.TOOL_DISPLAY_NAMES``
用于返回给客户端的 ``display_name``。缺条目时两处都回退成工具名本身
（``get_capital_flow``），用户看到的就是一串不知道什么意思的英文。
"""

import unittest

from api.v1.endpoints.agent import TOOL_DISPLAY_NAMES
from src.agent.factory import get_tool_registry
from src.agent.runner import _THINKING_TOOL_LABELS


class TestToolLabelParity(unittest.TestCase):
    def _registered_names(self):
        return sorted(get_tool_registry().list_names())

    def test_every_registered_tool_has_a_thinking_label(self):
        missing = [n for n in self._registered_names() if not _THINKING_TOOL_LABELS.get(n)]
        self.assertEqual(missing, [], msg=f"缺少思考过程标签的工具：{missing}")

    def test_every_registered_tool_has_a_display_name(self):
        missing = [n for n in self._registered_names() if not TOOL_DISPLAY_NAMES.get(n)]
        self.assertEqual(missing, [], msg=f"缺少中文展示名的工具：{missing}")

    def test_labels_are_chinese(self):
        for name in self._registered_names():
            for label in (_THINKING_TOOL_LABELS.get(name), TOOL_DISPLAY_NAMES.get(name)):
                self.assertIsNotNone(label)
                self.assertTrue(
                    any("一" <= ch <= "鿿" for ch in label),
                    msg=f"{name} 的标签 {label!r} 不是中文",
                )

    def test_guard_covers_the_whole_registry(self):
        """先确认扫到的是完整工具面，否则上面的断言会空过。"""
        names = self._registered_names()
        self.assertIn("get_capital_flow", names)
        self.assertIn("get_portfolio_snapshot", names)
        self.assertGreaterEqual(len(names), 15)


if __name__ == "__main__":
    unittest.main()
