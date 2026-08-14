import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc


BING_HTML = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/ai-news" h="id=1">AI 最新进展发布</a></h2>
    <div class="b_caption"><p>2026年8月，AI 领域迎来重大更新。</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/paper">新论文：<b>深度模型</b>研究</a></h2>
    <div class="b_caption"><p>论文摘要内容。</p></div>
  </li>
</ol>
"""

DDG_HTML = """
<div class="result results_links_deep highlight_d">
  <a class="result__a" href="/l/?kh=-1&amp;uddg=https%3A%2F%2Fexample.com%2Fddg1">DuckDuckGo 结果一</a>
  <a class="result__snippet" href="/l/?uddg=...">这是摘要内容一</a>
</div>
<div class="result results_links_deep">
  <a class="result__a" href="//example.com/ddg2">结果二</a>
  <a class="result__snippet" href="/l/?uddg=...">摘要内容二</a>
</div>
"""


class FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeClient:
    """复用客户端 _http_client() 的测试替身（get 返回 FakeResp）。"""

    def __init__(self, text):
        self._text = text

    def get(self, url, **kw):
        return FakeResp(self._text)


class FakeClient2:
    """按 URL 分流的客户端替身（side_effect 函数形式）。"""

    def __init__(self, fn):
        self._fn = fn

    def get(self, url, **kw):
        return self._fn(url, **kw)


def patch_http(text):
    return mock.patch("deepseek_client._http_client", return_value=FakeClient(text))


class TestSearchWeb(unittest.TestCase):
    def test_tool_registered(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("search_web", names)
        self.assertIn("search_web", dc.TOOL_CALL_MAP)

    def test_empty_query(self):
        self.assertIn("错误", dc.search_web(""))
        self.assertIn("错误", dc.search_web("   "))

    def test_long_query(self):
        self.assertIn("错误", dc.search_web("x" * 300))

    def test_bing_parse(self):
        with patch_http(BING_HTML):
            results = dc._search_bing("AI 新闻")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "AI 最新进展发布")
        self.assertEqual(results[0]["url"], "https://example.com/ai-news")
        self.assertIn("重大更新", results[0]["snippet"])

    def test_bing_strips_nested_tags(self):
        with patch_http(BING_HTML):
            results = dc._search_bing("x")
        self.assertEqual(results[1]["title"], "新论文：深度模型研究")

    def test_duckduckgo_parse(self):
        with patch_http(DDG_HTML):
            results = dc._search_duckduckgo("查询")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "DuckDuckGo 结果一")
        self.assertEqual(results[0]["url"], "https://example.com/ddg1")
        self.assertIn("摘要内容一", results[0]["snippet"])
        self.assertEqual(results[1]["url"], "https://example.com/ddg2")

    def test_search_falls_back_to_duckduckgo(self):
        def fake_get(url, **kw):
            if "bing.com" in url:
                raise ConnectionError("bing 不可用")
            return FakeResp(DDG_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            result = dc.search_web("测试关键词")
        self.assertIn("搜索结果", result)
        self.assertIn("DuckDuckGo 结果一", result)

    def test_search_both_sources_fail(self):
        def boom(url, **kw):
            raise ConnectionError("网络错误")

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(boom)):
            result = dc.search_web("测试")
        self.assertIn("错误", result)

    def test_search_empty_results_error(self):
        with patch_http("<html>无结果</html>"):
            result = dc.search_web("不存在的关键词xyz")
        self.assertIn("错误", result)

    def test_output_format(self):
        with patch_http(BING_HTML):
            result = dc.search_web("AI 新闻")
        self.assertIn("1. AI 最新进展发布", result)
        self.assertIn("https://example.com/ai-news", result)
        self.assertIn("2. 新论文", result)

    def test_max_results_capped(self):
        many = BING_HTML.replace("</ol>", BING_HTML + "</ol>")
        with patch_http(many):
            results = dc._search_bing("x")
        self.assertLessEqual(len(results), dc.SEARCH_MAX_RESULTS)

    # ===== 搜索增强（num/offset/时间/site/聚合去重）=====

    def test_aggregates_both_sources(self):
        """两引擎都可用时结果合并（去重后按 Bing 优先顺序输出）。"""
        def fake_get(url, **kw):
            return FakeResp(BING_HTML if "bing.com" in url else DDG_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            result = dc.search_web("AI 新闻", num=10)
        self.assertIn("AI 最新进展发布", result)   # Bing 结果
        self.assertIn("DuckDuckGo 结果一", result)  # DDG 结果补充
        self.assertIn("（4 条）", result)           # 2 + 2 无重复

    def test_dedup_same_url(self):
        """两引擎返回相同 URL 时只保留一条。"""
        def fake_get(url, **kw):
            return FakeResp(BING_HTML if "bing.com" in url else DDG_HTML.replace(
                "%2Fddg1", "%2Fai-news"))  # DDG HTML 内 URL 为编码形式

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            result = dc.search_web("AI 新闻", num=10)
        self.assertIn("（3 条）", result)  # 2 + 2 - 1 重复
        self.assertEqual(result.count("https://example.com/ai-news"), 1)

    def test_num_parameter_capped(self):
        """num 指定条数：请求 count=num，输出不超过 num 条。"""
        captured = []

        def fake_get(url, **kw):
            captured.append(url)
            return FakeResp(BING_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            result = dc.search_web("AI 新闻", num=1)
        bing_url = next(u for u in captured if "bing.com" in u)
        self.assertIn("count=1", bing_url)
        self.assertIn("（1 条）", result)

    def test_num_clamped(self):
        """num 越界钳制：0 → 1，超大 → 20。"""
        with patch_http(BING_HTML):
            r0 = dc.search_web("x", num=0)
        self.assertIn("（1 条）", r0)
        with patch_http(BING_HTML):
            rbig = dc.search_web("x", num=999)
        self.assertIn("（2 条）", rbig)  # mock 只有 2 条

    def test_offset_in_url(self):
        """offset 翻页：Bing URL 带 first=offset+1。"""
        captured = []

        def fake_get(url, **kw):
            captured.append(url)
            return FakeResp(BING_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            dc.search_web("x", offset=5)
        bing_url = next(u for u in captured if "bing.com" in u)
        self.assertIn("first=6", bing_url)

    def test_since_until_in_url(self):
        """时间过滤：Bing 带 filters 日期范围，DDG 带 df=since。"""
        captured = []

        def fake_get(url, **kw):
            captured.append(url)
            return FakeResp(BING_HTML if "bing.com" in url else DDG_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            dc.search_web("x", since="2026-08-01", until="2026-08-15")
        bing_url = next(u for u in captured if "bing.com" in u)
        ddg_url = next(u for u in captured if "duckduckgo.com" in u)
        self.assertIn("filters=ex1:%222026-08-01..2026-08-15%22", bing_url)
        self.assertIn("df=2026-08-01", ddg_url)

    def test_site_filter_appended(self):
        """site 限定：query 自动追加 site: 域名。"""
        captured = []

        def fake_get(url, **kw):
            captured.append(url)
            return FakeResp(BING_HTML if "bing.com" in url else DDG_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            dc.search_web("OpenAI", site="openai.com")
        for u in captured:
            self.assertIn("site%3Aopenai.com", u)  # quote() 编码 site:

    def test_invalid_site_rejected(self):
        with patch_http(BING_HTML):
            r = dc.search_web("x", site="bad site!")
        self.assertIn("错误", r)

    def test_invalid_date_rejected(self):
        with patch_http(BING_HTML):
            r1 = dc.search_web("x", since="2026/08/01")
        self.assertIn("错误", r1)
        with patch_http(BING_HTML):
            r2 = dc.search_web("x", until="昨天")
        self.assertIn("错误", r2)


if __name__ == "__main__":
    unittest.main()
