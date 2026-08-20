import unittest
from io import BytesIO
from unittest.mock import patch

from server import SearchPlan, build_search_plans, search, searxng_search


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload.read()


def fake_json_response(payload):
    return FakeResponse(__import__("json").dumps(payload).encode("utf-8"))


class SearchPlanTests(unittest.TestCase):
    def test_low_divergence_keeps_adjacent_queries(self):
        plans = build_search_plans("代码优化", 20)
        self.assertEqual(plans[0].query, "代码优化")
        self.assertLessEqual(max(plan.distance for plan in plans), 32)

    def test_zero_divergence_only_searches_original_query(self):
        plans = build_search_plans("代码优化", 0)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].query, "代码优化")
        self.assertEqual(plans[0].distance, 0)

    def test_high_divergence_uses_cross_domain_queries(self):
        plans = build_search_plans("代码优化", 90)
        self.assertGreaterEqual(min(plan.distance for plan in plans[1:]), 72)
        self.assertTrue(any("园林" in plan.query or "城市" in plan.query or "钟表" in plan.query for plan in plans))

    def test_search_has_stable_fallback(self):
        with patch("server.searxng_search", side_effect=OSError("offline")):
            payload = search("代码优化", 60, 6)
        self.assertEqual(payload["query"], "代码优化")
        self.assertEqual(payload["divergence"], 60)
        self.assertGreaterEqual(len(payload["results"]), 3)
        self.assertTrue(all(result["reason"] for result in payload["results"]))

    def test_fallback_results_change_with_divergence(self):
        with patch("server.searxng_search", side_effect=OSError("offline")):
            low = search("代码优化", 10, 8)
            high = search("代码优化", 90, 8)
        low_urls = {result["url"] for result in low["results"]}
        high_urls = {result["url"] for result in high["results"]}
        self.assertNotEqual(low_urls, high_urls)
        self.assertGreaterEqual(len(low_urls.symmetric_difference(high_urls)), 6)
        self.assertTrue(any(result["bridge"] in {"删减与留白", "空间与边界", "复杂网络", "修复思维"} for result in high["results"]))

    def test_searxng_json_is_mapped_to_search_results(self):
        response = {
            "results": [
                {
                    "title": "<b>测试标题</b>",
                    "url": "https://example.com/guide?q=1",
                    "content": "一段 <em>搜索摘要</em>",
                    "engine": "brave",
                }
            ]
        }
        plan = SearchPlan("跨域方向", "测试", "修复思维", "从另一个领域观察问题。", 72)
        with patch("server.urllib.request.urlopen", return_value=FakeResponse(__import__("json").dumps(response).encode("utf-8"))) as mocked:
            results = searxng_search(plan, 3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "测试标题")
        self.assertEqual(results[0].snippet, "一段 搜索摘要")
        self.assertEqual(results[0].bridge, "修复思维")
        request = mocked.call_args.args[0]
        self.assertIn("format=json", request.full_url)
        self.assertIn("language=zh-CN", request.full_url)
        self.assertIn("engines=baidu%2Cgoogle", request.full_url)

    def test_searxng_retries_with_bing_when_primary_engines_are_empty(self):
        bing_payload = {
            "results": [
                {"title": "嘉豪（网络流行词）", "url": "https://example.com/jiahao", "content": "嘉豪梗的含义与来源"}
            ]
        }
        plan = SearchPlan("原主题", "嘉豪", "原始问题", "直接相关。", 0)
        with patch("server.urllib.request.urlopen", side_effect=[fake_json_response({"results": []}), fake_json_response(bing_payload)]) as mocked:
            results = searxng_search(plan, 3)
        self.assertEqual(results[0].title, "嘉豪（网络流行词）")
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("engines=bing", mocked.call_args.args[0].full_url)

    def test_unknown_query_does_not_use_unrelated_original_fallback(self):
        with patch("server.searxng_search", return_value=[]):
            payload = search("不存在的陌生词", 0, 10)
        self.assertEqual(payload["mode"], "fallback")
        self.assertEqual(payload["results"], [])

    def test_searxng_prioritizes_exact_title_and_limits_same_source(self):
        response = {
            "results": [
                {"title": "速度优化 - 芯片文档", "url": "https://docs.example.com/a", "content": "提高代码速度"},
                {"title": "速度优化 - 另一芯片", "url": "https://docs.example.com/b", "content": "提高代码速度"},
                {"title": "速度优化 - 第三芯片", "url": "https://docs.example.com/c", "content": "提高代码速度"},
                {"title": "代码优化完整指南", "url": "https://guide.example.org/", "content": "编程性能最佳实践"},
            ]
        }
        plan = SearchPlan("原主题", "代码优化", "原始问题", "直接相关。", 0)
        with patch("server.urllib.request.urlopen", return_value=FakeResponse(__import__("json").dumps(response).encode("utf-8"))):
            results = searxng_search(plan, 4)
        self.assertEqual(results[0].title, "代码优化完整指南")
        self.assertLessEqual(sum(result.source == "docs.example.com" for result in results), 2)

    def test_search_reports_searxng_mode(self):
        def live_result(plan, _limit):
            slug = __import__("urllib.parse").parse.quote(plan.query)
            return [__import__("server").SearchResult(
                "实时结果", f"https://example.com/{slug}", "摘要", "example.com", "example.com", plan.bridge, plan.reason, plan.distance
            )]

        with patch("server.searxng_search", side_effect=live_result):
            payload = search("代码优化", 20, 3)
        self.assertEqual(payload["mode"], "searxng")
        self.assertTrue(payload["search_backend"]["available"])


if __name__ == "__main__":
    unittest.main()
