# -*- coding: utf-8 -*-
"""回归测试：流式对话的 usage 统计（累计输入/输出）不再丢失。

背景：openai SDK 2.x 的 Stream 不再聚合 usage，且带 usage 的末尾 chunk
（choices 为空数组）此前被 _consume_stream 跳过，导致 on_usage 永不触发，
UI 的「累计输入/累计输出」恒为 0。本测试验证修复后的捕获路径。
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc


def make_usage(prompt=100, completion=50, cache_hit=80, cache_miss=20):
    """模拟 DeepSeek 流式 usage（含缓存计费字段，SDK extra=allow 保留）。"""
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
    )


def make_chunk(content=None, reasoning=None, usage=None):
    """构造 ChatCompletionChunk 等价对象。usage chunk 的 choices 为空数组。"""
    if usage is not None:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=None
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=None)


class TestStreamUsageCapture(unittest.TestCase):
    """_consume_stream 必须捕获末尾 usage chunk 并返回。"""

    def setUp(self):
        self.client = dc.DeepSeekClient(api_key="test-key", model="deepseek-v4-flash")

    def test_usage_chunk_captured(self):
        usage = make_usage()
        chunks = [
            make_chunk(content="你"),
            make_chunk(content="好"),
            make_chunk(usage=usage),  # 末尾 usage chunk：choices 为空
        ]
        reasoning, content, tool_calls, got = self.client._consume_stream(
            chunks, None, None, None
        )
        self.assertEqual(content, "你好")
        self.assertIsNotNone(got)
        self.assertEqual(got.prompt_tokens, 100)
        self.assertEqual(got.completion_tokens, 50)
        self.assertEqual(got.prompt_cache_hit_tokens, 80)
        self.assertEqual(got.prompt_cache_miss_tokens, 20)

    def test_usage_none_when_absent(self):
        chunks = [make_chunk(content="x")]
        _, _, _, got = self.client._consume_stream(chunks, None, None, None)
        self.assertIsNone(got)

    def test_reasoning_and_tool_calls_still_work(self):
        """回归：捕获 usage 不影响既有 reasoning / tool_calls 解析。"""
        tc_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="思考中",
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(
                                    name="get_date", arguments="{}"
                                ),
                            )
                        ],
                    )
                )
            ],
            usage=None,
        )
        chunks = [tc_chunk, make_chunk(usage=make_usage())]
        reasoning, content, tool_calls, got = self.client._consume_stream(
            chunks, None, None, None
        )
        self.assertEqual(reasoning, "思考中")
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["name"], "get_date")
        self.assertIsNotNone(got)


class TestChatReportsUsage(unittest.TestCase):
    """chat() 流式路径必须把 usage 通过 on_usage 上报（修复前恒 0）。"""

    def setUp(self):
        self.client = dc.DeepSeekClient(api_key="test-key", model="deepseek-v4-flash")

    def test_on_usage_receives_correct_dict(self):
        chunks = [
            make_chunk(content="回复内容"),
            make_chunk(usage=make_usage(prompt=123, completion=45)),
        ]
        collected = []

        def on_usage(payload):
            collected.append(payload)

        with mock.patch.object(
            self.client, "_create_with_retry", return_value=iter(chunks)
        ):
            ok = self.client.chat(
                [{"role": "user", "content": "hi"}],
                scenario="通用",
                thinking="none",
                max_tokens=16,
                tools_enabled=False,
                max_tool_rounds=1,
                on_usage=on_usage,
            )
        self.assertTrue(ok)
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["prompt"], 123)
        self.assertEqual(collected[0]["completion"], 45)
        self.assertEqual(collected[0]["cache_hit"], 80)
        self.assertEqual(collected[0]["cache_miss"], 20)

    def test_usage_dict_fields(self):
        d = dc.DeepSeekClient._usage_dict(make_usage())
        self.assertEqual(d, {"prompt": 100, "completion": 50,
                             "cache_hit": 80, "cache_miss": 20})

    def test_usage_dict_missing_fields_default_zero(self):
        d = dc.DeepSeekClient._usage_dict(SimpleNamespace(prompt_tokens=7))
        self.assertEqual(d["prompt"], 7)
        self.assertEqual(d["completion"], 0)
        self.assertEqual(d["cache_hit"], 0)


if __name__ == "__main__":
    unittest.main()
