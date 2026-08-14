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
    def setUp(self):
        # 重置引擎健康度：测试间失败计数会累积导致引擎被暂停
        dc._SEARCH_HEALTH.clear()

    def tearDown(self):
        # 防残留暂停状态影响其他测试文件（测试顺序无关）
        dc._SEARCH_HEALTH.clear()

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

    # ===== 引擎池：so360 解析 + 健康度 =====

    SO360_HTML = """
<html><body>
<ul class="res-list">
  <li><h3><a href="javascript:;" data-mdurl="">推广入口</a></h3></li>
  <li><h3><a href="https://example.com/so1">360 结果一：AI 大模型发布</a></h3><p class="res-desc">摘要一</p></li>
  <li><h3><a href="/link?m=abc123">360 加密跳转结果</a></h3></li>
</ul>
</body></html>
"""

    def test_so360_parse(self):
        with patch_http(self.SO360_HTML):
            results = dc._search_so360("AI")
        self.assertEqual(len(results), 2)  # 跳过 javascript:
        self.assertEqual(results[0]["title"], "360 结果一：AI 大模型发布")
        self.assertEqual(results[0]["url"], "https://example.com/so1")
        self.assertEqual(results[1]["url"], "https://www.so.com/link?m=abc123")  # 补全域名

    def test_so360_registered(self):
        names = [e[0] for e in dc._SEARCH_ENGINES]
        self.assertIn("so360", names)
        self.assertIn("bing", names)

    def test_engine_health_pauses_after_failures(self):
        """连续失败 3 次后引擎被暂停（skip_until 设置）；成功后恢复。"""
        for _ in range(dc._SEARCH_HEALTH_FAIL_LIMIT):
            dc._search_report("bing", False)
        self.assertFalse(dc._search_healthy("bing"))
        # 冷却期内仍不可用
        self.assertFalse(dc._search_healthy("bing"))
        # 模拟冷却结束
        dc._SEARCH_HEALTH["bing"]["skip_until"] = 0
        self.assertTrue(dc._search_healthy("bing"))
        dc._search_report("bing", True)
        self.assertEqual(dc._SEARCH_HEALTH["bing"]["fails"], 0)

    def test_unhealthy_engine_skipped(self):
        """被暂停的引擎不再发起请求。"""
        dc._search_report("bing", False)
        dc._search_report("bing", False)
        dc._search_report("bing", False)
        requested = []

        def fake_get(url, **kw):
            requested.append(url)
            return FakeResp(BING_HTML)

        with mock.patch("deepseek_client._http_client", return_value=FakeClient2(fake_get)):
            dc.search_web("x", num=5)
        self.assertFalse(any("bing.com" in u for u in requested))
        self.assertTrue(any("so.com" in u for u in requested))  # 其余引擎正常

    # ===== search_github 垂直源 =====

    def test_github_search(self):
        payload = {
            "items": [
                {"full_name": "org/repo1", "html_url": "https://github.com/org/repo1",
                 "description": "一个好项目", "stargazers_count": 1234},
                {"full_name": "org/repo2", "html_url": "https://github.com/org/repo2",
                 "description": None, "stargazers_count": 0},
            ]
        }

        class FakeGithubResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        captured = {}

        class FakeGithubClient:
            def get(self, url, **kw):
                captured["params"] = kw.get("params")
                return FakeGithubResp()

        with mock.patch("deepseek_client._http_client", return_value=FakeGithubClient()):
            result = dc.search_github("deepseek", num=2)
        self.assertIn("org/repo1 ⭐1234", result)
        self.assertIn("https://github.com/org/repo1", result)
        self.assertIn("（2 个", result)
        self.assertEqual(captured["params"]["per_page"], 2)

    def test_github_language_filter(self):
        captured = {}

        class FakeGithubResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"items": []}

        class FakeGithubClient:
            def get(self, url, **kw):
                captured["params"] = kw.get("params")
                return FakeGithubResp()

        with mock.patch("deepseek_client._http_client", return_value=FakeGithubClient()):
            dc.search_github("deepseek", language="python")
        self.assertIn("language:python", captured["params"]["q"])

    def test_github_rate_limited(self):
        class FakeResp403:
            status_code = 403

            def raise_for_status(self):
                pass

        class FakeClient403:
            def get(self, url, **kw):
                return FakeResp403()

        with mock.patch("deepseek_client._http_client", return_value=FakeClient403()):
            result = dc.search_github("x")
        self.assertIn("限流", result)

    def test_github_invalid_args(self):
        self.assertIn("错误", dc.search_github(""))
        self.assertIn("错误", dc.search_github("x", language="bad lang!"))

    # ===== P0 修复回归：fetch_blocked 签名 / site 硬过滤 / offset 切片 =====

    def test_fetch_blocked_named_args(self):
        """分发器以 fn(**args) 调用，_run_fetch_blocked 必须接收具名参数。"""
        import inspect
        sig = inspect.signature(dc._run_fetch_blocked)
        self.assertIn("url", sig.parameters)
        # 具名调用不抛 TypeError
        with mock.patch.object(dc, "_fetch_blocked_impl", return_value="OK"):
            self.assertEqual(dc._run_fetch_blocked(url="https://linux.do"), "OK")
            self.assertEqual(dc._run_fetch_blocked("https://linux.do", proxy="p"), "OK")

    def test_fetch_blocked_missing_module(self):
        with mock.patch.object(dc, "_fetch_blocked_impl", None):
            r = dc._run_fetch_blocked(url="https://linux.do")
        self.assertIn("错误", r)

    def test_site_hard_filter(self):
        """site 过滤在聚合后按域名兜底（引擎忽略 site: 语法也生效）。"""
        results = [
            {"title": "a", "url": "https://openai.com/a", "snippet": ""},
            {"title": "b", "url": "https://www.openai.com/b", "snippet": ""},
            {"title": "c", "url": "https://sub.openai.com/c", "snippet": ""},
            {"title": "d", "url": "https://evil.com/d", "snippet": ""},
        ]
        with mock.patch("deepseek_client._search_bing", return_value=results), \
             mock.patch("deepseek_client._search_so360", return_value=[]), \
             mock.patch("deepseek_client._search_duckduckgo", return_value=[]):
            out = dc.search_web("x", site="openai.com")
        self.assertNotIn("evil.com", out)
        self.assertIn("openai.com/a", out)
        self.assertIn("sub.openai.com/c", out)  # 子域名匹配

    def test_site_filter_empty_hint(self):
        """site 过滤后为空时给出明确提示而非误导性错误。"""
        results = [{"title": "a", "url": "https://other.com/a", "snippet": ""}]
        with mock.patch("deepseek_client._search_bing", return_value=results), \
             mock.patch("deepseek_client._search_so360", return_value=[]), \
             mock.patch("deepseek_client._search_duckduckgo", return_value=[]):
            out = dc.search_web("x", site="openai.com")
        self.assertIn("未找到限定站点", out)

    def test_offset_slicing(self):
        """offset 手动翻页：请求多取，聚合后切片（引擎不支持 first= 也生效）。"""
        results = [{"title": f"r{i}", "url": f"https://example.com/{i}", "snippet": ""}
                   for i in range(8)]
        with mock.patch("deepseek_client._search_bing", return_value=results), \
             mock.patch("deepseek_client._search_so360", return_value=[]), \
             mock.patch("deepseek_client._search_duckduckgo", return_value=[]):
            out = dc.search_web("x", num=3, offset=5)
        self.assertIn("example.com/5", out)
        self.assertIn("example.com/6", out)
        self.assertIn("example.com/7", out)
        self.assertNotIn("example.com/0", out)
        self.assertNotIn("example.com/4", out)


if __name__ == "__main__":
    unittest.main()
