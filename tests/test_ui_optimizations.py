# -*- coding: utf-8 -*-
"""v2.24 optimization regression tests."""
import copy
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mdparse
import deepseek_client as dc


class TestMarkdownTaskList:
    def test_unchecked_task_renders_checkbox(self):
        text, spans, links, code_blocks = mdparse.render_markdown("- [ ] 待办事项")
        assert "☐ 待办事项" in text

    def test_checked_task_renders_checked_box(self):
        text, _, _, _ = mdparse.render_markdown("- [x] 已完成事项")
        assert "☑ 已完成事项" in text

    def test_task_list_preserves_indent(self):
        text, _, _, _ = mdparse.render_markdown("  - [x] 缩进任务")
        assert "  • ☑ 缩进任务" in text


class TestToolSchemaCache:
    def test_cache_returns_deepcopy_with_tools(self):
        tools = dc._cached_all_tools([])
        assert isinstance(tools, list)
        names = {t["function"]["name"] for t in tools}
        assert "get_date" in names

    def test_cache_preserves_custom_tools(self):
        custom = [{"type": "function", "function": {
            "name": "my_tool", "description": "x",
            "parameters": {"type": "object", "properties": {}},
            "endpoint": "https://example.com/api",
        }}]
        tools = dc._cached_all_tools(custom)
        names = {t["function"]["name"] for t in tools}
        assert "my_tool" in names
        assert "get_date" in names