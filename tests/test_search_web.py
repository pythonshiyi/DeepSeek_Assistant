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


if __name__ == "__main__":
    unittest.main()
