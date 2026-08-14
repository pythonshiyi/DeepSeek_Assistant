import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# tkinter 可用性探测：部分 CI 环境的 Tcl/Tk 运行库缺失（如 init.tcl 路径失效），
# 此时跳过需要真实 Tk 实例的测试而不是报错
try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m


class PerfBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TK_AVAILABLE:
            raise unittest.SkipTest("tkinter 不可用（缺少 Tcl/Tk 运行库）")
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


class TestFinishAborted(PerfBase):
    """修复回归：生成被截断/中断时 _finish 必须报告「任务中断」而非「任务完成」。"""

    def _round_with_fail_tool(self):
        self.app._begin_assistant()
        self.app._push_tool("write_file", {"path": "x"}, "错误：内容超限")
        self.app._drain_ui_queue()
        self.app._flush_pending()
        self.app._push_content("部分回复")
        self.app._drain_ui_queue()
        self.app._flush_pending()

    def test_aborted_marks_interrupted(self):
        self._round_with_fail_tool()
        self.app._round_aborted = True  # 模拟 on_truncated 已置位（max_tokens 截断）
        self.app._finish()
        text = self.app.chat_text.get("1.0", "end")
        self.assertIn("[任务中断]", text)
        self.assertNotIn("[任务完成]", text)
        self.assertIn("继续生成", text)  # 提供继续路径指引

    def test_normal_marks_completed(self):
        self._round_with_fail_tool()
        self.app._finish()
        text = self.app.chat_text.get("1.0", "end")
        self.assertIn("[任务完成]", text)
        self.assertNotIn("[任务中断]", text)

    def test_aborted_resets_flag(self):
        """_round_aborted 是单轮状态：本轮中断标记用完即复位，不污染下一轮。"""
        self._round_with_fail_tool()
        self.app._round_aborted = True
        self.app._finish()
        self.assertFalse(self.app._round_aborted)

    def test_pure_chat_aborted_notice(self):
        """纯对话（无工具）被截断 → 显示「回复中断」提示。"""
        self.app._begin_assistant()
        self.app._push_content("一半的内容")
        self.app._drain_ui_queue()
        self.app._flush_pending()
        self.app._round_aborted = True
        self.app._finish()
        text = self.app.chat_text.get("1.0", "end")
        self.assertIn("[回复中断]", text)


class TestCompressFacts(PerfBase):
    """压缩事实提炼：摘要器输出「关键事实」小节并注入摘要消息。"""

    def _long_history(self, n=10):
        msgs = [{"role": "system", "content": "s"}]
        for i in range(n):
            msgs.append({"role": "user", "content": f"问题{i}"})
            msgs.append({"role": "assistant", "content": f"回答{i}"})
        return msgs

    def test_compress_extracts_facts(self):
        from unittest import mock

        self.app.messages = self._long_history()
        self.app._current["pinned"] = []

        class _Msg:
            content = "早期对话摘要内容。\n\n关键事实：\n- 用户偏好简洁回答\n- 决定采用方案B"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Completions:
            def create(self, **kw):
                return _Resp()

        class _Client:
            model = "deepseek-v4-flash"

            class client:
                chat = type("Chat", (), {"completions": _Completions()})()

        with mock.patch.object(self.app, "ensure_client", return_value=_Client()):
            ok = self.app._compress_old_history()
        self.assertTrue(ok)
        sys_msg = self.app.messages[1]
        self.assertEqual(sys_msg["role"], "system")
        self.assertIn("[历史对话摘要]", sys_msg["content"])
        self.assertIn("关键事实", sys_msg["content"])     # 事实清单随摘要注入
        self.assertIn("方案B", sys_msg["content"])


if __name__ == "__main__":
    unittest.main()
