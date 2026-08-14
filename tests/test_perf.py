import json
import os
import sys
import tempfile
import time
import unittest
import tkinter as tk
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m


class PerfBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="dsa_perf_")
        m.CONFIG_PATH = os.path.join(cls.tmpdir, "config.json")
        m.HISTORY_DIR = cls.tmpdir
        m.SNAPSHOT_PATH = os.path.join(cls.tmpdir, "snap.json")
        m.SESSIONS_DIR = os.path.join(cls.tmpdir, "sessions")
        m.STATS_PATH = os.path.join(cls.tmpdir, "stats.json")
        m.PROMPTS_PATH = os.path.join(cls.tmpdir, "prompts.json")
        m.USER_TOOLS_PATH = os.path.join(cls.tmpdir, "ut.json")
        m.ARCHIVES_DIR = os.path.join(cls.tmpdir, "archives")
        m.CLEAN_EXIT_FLAG = os.path.join(cls.tmpdir, ".clean_exit")
        os.makedirs(m.SESSIONS_DIR, exist_ok=True)
        os.makedirs(m.ARCHIVES_DIR, exist_ok=True)
        with open(m.CLEAN_EXIT_FLAG, "w", encoding="utf-8") as f:
            f.write("ok")
        with open(m.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"welcomed": True, "restore_session": False}, f)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = m.AssistantApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.on_close()
        except Exception:
            pass
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.app.busy = False
        self.app.stop_event = None
        self.app.messages = [{"role": "system", "content": self.app.cfg["system_prompt"]}]
        self.app.blocks = []
        text = self.app.chat_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")
        self.app._fold_ranges[text] = []
        self.app._link_ranges[text] = []
        self.app._stream_thinking_fold = None
        self.app._stream_start = None
        self.app._stream_block_start = None

    def _simulate_round(self, turn_no):
        """模拟一轮完整生成：思考 → 工具 → 内容。"""
        self.app._begin_assistant()
        for chunk in (f"思考{turn_no}a", f"思考{turn_no}b", f"思考{turn_no}c"):
            self.app._push_reasoning(chunk)
            self.app._drain_ui_queue()
            self.app._flush_pending()
        self.app._push_tool("get_date", {}, f"2026-08-{turn_no % 28 + 1:02d}")
        self.app._drain_ui_queue()
        self.app._flush_pending()
        for chunk in (f"回答{turn_no}第一段", f"回答{turn_no}第二段"):
            self.app._push_content(chunk)
            self.app._drain_ui_queue()
            self.app._flush_pending()
        self.app._finish()


class TestFinishPerformance(PerfBase):
    def test_finish_does_not_full_render(self):
        """核心性能断言：_finish 不再触发全量重渲染。"""
        self._simulate_round(1)
        with mock.patch.object(self.app, "_render_all") as render:
            self._simulate_round(2)
        render.assert_not_called()

    def test_finish_no_full_render_long_session(self):
        for i in range(40):
            self._simulate_round(i)
        with mock.patch.object(self.app, "_render_all") as render:
            self._simulate_round(40)
        render.assert_not_called()

    def test_stream_thinking_single_card(self):
        """多个思考片段追加进同一张折叠卡片。"""
        self._simulate_round(1)
        text = self.app.chat_text
        thinking_folds = [f for f in self.app._fold_ranges[text] if f["style"] == "thinking"]
        self.assertEqual(len(thinking_folds), 1)
        content = text.get("1.0", "end")
        # 片段可能带换行连接，但顺序与完整性保持
        self.assertLess(content.index("思考1a"), content.index("思考1b"))
        self.assertLess(content.index("思考1b"), content.index("思考1c"))

    def test_tool_rendered_as_fold_card(self):
        self._simulate_round(1)
        text = self.app.chat_text
        tool_folds = [f for f in self.app._fold_ranges[text] if f["style"] == "tool"]
        self.assertEqual(len(tool_folds), 1)
        self.assertIn("[工具] get_date", tool_folds[0]["head"])

    def test_long_session_finish_stays_fast(self):
        for i in range(40):
            self._simulate_round(i)
        t0 = time.monotonic()
        self._simulate_round(40)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5)

    def test_render_all_still_works(self):
        self._simulate_round(1)
        self.app._render_all()
        content = self.app.chat_text.get("1.0", "end")
        self.assertIn("思考1a", content)
        self.assertIn("回答1第一段", content)

    def test_copy_all_includes_folds(self):
        self._simulate_round(1)
        self.app._copy_all()
        clip = self.app.root.clipboard_get()
        self.assertIn("思考1a", clip)
        self.assertIn("回答1第一段", clip)

    def test_thinking_fold_toggle_streamed(self):
        """流式创建的思考卡片可折叠（elide 隐藏显示，search 默认跳过折叠文本）。"""
        self._simulate_round(1)
        text = self.app.chat_text
        fold = next(f for f in self.app._fold_ranges[text] if f["style"] == "thinking")
        self.assertTrue(fold["visible"])
        self.app._toggle_fold(text, fold)
        self.assertFalse(fold["visible"])
        # 思考 body 被 elide：search 找不到（工具卡片自身保持折叠，fold_hidden 非空是正常的）
        self.assertFalse(text.search("思考1a", "1.0", stopindex="end"))
        self.app._toggle_fold(text, fold)
        self.assertTrue(fold["visible"])
        self.assertTrue(text.search("思考1a", "1.0", stopindex="end"))


class TestStreamMarkdownRender(PerfBase):
    def _stream(self, chunks, finish=True):
        self.app._begin_assistant()
        for c in chunks:
            self.app._push_content(c)
            self.app._drain_ui_queue()
            self.app._flush_pending()
        if finish:
            self.app._finish()
        return self.app.chat_text.get("1.0", "end")

    def test_bold_across_chunks(self):
        """粗体标记跨 chunk 时延迟到闭合后渲染。"""
        full = self._stream(["这是**粗体", "文本**内容"])
        self.assertNotIn("**", full)
        self.assertIn("粗体文本", full)

    def test_inline_code_and_link(self):
        full = self._stream(["有 `code` 和 [链接](http://a.com)"])
        self.assertNotIn("`", full)
        self.assertGreaterEqual(len(self.app._link_ranges[self.app.chat_text]), 1)

    def test_code_fence_across_chunks(self):
        """代码围栏跨 chunk：未闭合时暂缓，闭合后渲染；_finish 强制兜底。"""
        full = self._stream(["```python\nprint(1)\n```", "后续"])
        self.assertNotIn("```", full)
        self.assertIn("print(1)", full)

    def test_unclosed_fence_forced_at_finish(self):
        """生成结束仍有未闭合围栏：_finish(force) 强制渲染（不丢内容）。"""
        full = self._stream(["```python\nprint(2)"])
        self.assertIn("print(2)", full)

    def test_blocks_keep_raw_markdown(self):
        """blocks 始终保存原始 Markdown（rebuild 重新渲染一致）。"""
        self._stream(["**粗体** 与 `代码`"])
        content_blocks = [b for b in self.app.blocks if b[0] == "content"]
        self.assertTrue(any("**粗体**" in b[1] for b in content_blocks))
        self.assertTrue(any("`代码`" in b[1] for b in content_blocks))

    def test_stream_render_matches_rebuild(self):
        """流式渲染文本与全量重渲染文本一致（右键定位/搜索不脱节）。"""
        self._stream(["**加粗** 和 `行内` 与 [点我](http://x.com)"])
        text = self.app.chat_text
        streamed = text.get("1.0", "end")
        self.app._render_all()
        rebuilt = text.get("1.0", "end")
        self.assertEqual(streamed, rebuilt)

    def test_md_render_off_streams_plain(self):
        self.app._md_render = False
        try:
            full = self._stream(["**粗体**"])
            self.assertIn("**粗体**", full)
        finally:
            self.app._md_render = True


class TestRebuildSkip(PerfBase):
    def test_rebuild_noop_skips_render(self):
        """消息未变化时重复 rebuild 跳过全量重绘（编辑重发未改动时零成本）。"""
        self._simulate_round(1)
        self.app.rebuild_view_from_messages()  # 首次：结构一致后
        before_blocks = self.app.blocks
        before_text = self.app.chat_text.get("1.0", "end")
        with mock.patch.object(self.app, "_render_all") as render:
            self.app.rebuild_view_from_messages()  # 再次：无变化 → 跳过
        render.assert_not_called()
        self.assertIs(self.app.blocks, before_blocks)
        self.assertEqual(self.app.chat_text.get("1.0", "end"), before_text)

    def test_rebuild_changed_content_renders(self):
        """消息变化（编辑重发）时正常全量重建。"""
        self._simulate_round(1)
        self.app.rebuild_view_from_messages()
        self.app.messages.append({"role": "user", "content": "新问题"})
        with mock.patch.object(self.app, "_render_all") as render:
            self.app.rebuild_view_from_messages()
        render.assert_called_once()
        self.assertTrue(any(b[0] == "user" and "新问题" in b[1] for b in self.app.blocks))

    def test_rebuild_system_prompt_change_triggers(self):
        """仅系统提示词变化也视为内容变化（重绘）。"""
        self._simulate_round(1)
        self.app.rebuild_view_from_messages()
        self.app.messages[0]["content"] = "修改后的系统提示词"
        with mock.patch.object(self.app, "_render_all") as render:
            self.app.rebuild_view_from_messages()
        render.assert_called_once()


class TestRenderAllScaling(PerfBase):
    def test_many_blocks_render_all_reasonable(self):
        """全量渲染 60 轮（约 240 块）在宽松阈值内（手动刷新场景）。

        大内容渲染现在走分帧（事件循环驱动），需等待分帧完成后校验。
        """
        for i in range(60):
            self._simulate_round(i)
        t0 = time.monotonic()
        self.app._render_all()
        # 带超时的等待循环：分帧渲染异常置空不及时时测试不能无限自旋
        deadline = time.monotonic() + 30
        while self.app._paged_render is not None:
            self.app.root.update()
            if time.monotonic() > deadline:
                break
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 10.0)
        content = self.app.chat_text.get("1.0", "end")
        self.assertIn("回答59第二段", content)


if __name__ == "__main__":
    unittest.main()
