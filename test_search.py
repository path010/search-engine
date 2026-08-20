import unittest
import time
from io import BytesIO
from unittest.mock import patch

from server import SEARCH_CACHE, SearchPlan, build_search_plans, search, searxng_search


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
    def setUp(self):
        SEARCH_CACHE.clear()

    def test_low_divergence_keeps_adjacent_queries(self):
        plans = build_search_plans("代码优化", 20)
        self.assertEqual(plans[0].query, "代码优化")
        self.assertLessEqual(max(plan.distance for plan in plans), 32)

    def test_zero_divergence_only_searches_original_query(self):
        plans = build_search_plans("代码优化", 0)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].query, "代码优化")
        self.assertEqual(plans[0].distance, 0)

    def test_any_low_divergence_query_uses_generic_facets(self):
        plans = build_search_plans("鸟", 2)
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(plan.distance <= 2 for plan in plans))
        self.assertEqual(
            {plan.bridge for plan in plans},
            {"概念解释", "分类体系"},
        )
        self.assertTrue(all(plan.anchor == "鸟" for plan in plans))

    def test_arbitrary_query_can_fill_three_stable_pages_without_curated_data(self):
        def generic_live_results(plan, requested_limit, _page=1):
            return [__import__("server").SearchResult(
                f"{plan.anchor} {plan.bridge} {index}",
                f"https://generic-{plan.bridge}-{index}.example/item",
                f"关于{plan.anchor}的通用检索结果", f"source-{index}.example", "example",
                plan.bridge, plan.reason, plan.distance,
            ) for index in range(requested_limit)]

        with patch("server.searxng_search", side_effect=generic_live_results):
            pages = [search("未配置词", 2, 10, page=number) for number in (1, 2, 3)]
        self.assertEqual(pages[0]["pagination"]["total_results"], 30)
        self.assertEqual(pages[0]["pagination"]["total_pages"], 3)
        self.assertEqual([len(payload["results"]) for payload in pages], [10, 10, 10])
        url_sets = [{result["url"] for result in payload["results"]} for payload in pages]
        self.assertFalse(url_sets[0] & url_sets[1])
        self.assertFalse(url_sets[1] & url_sets[2])

    def test_high_divergence_uses_cross_domain_queries(self):
        plans = build_search_plans("代码优化", 90)
        self.assertGreaterEqual(min(plan.distance for plan in plans[1:]), 72)
        self.assertTrue(any("园林" in plan.query or "城市" in plan.query or "钟表" in plan.query for plan in plans))

    def test_electricity_divergence_has_explainable_bridges(self):
        plans = build_search_plans("电", 100)
        self.assertEqual(plans[0].query, "电")
        bridges = {plan.bridge for plan in plans[1:]}
        self.assertEqual(bridges, {"生物电", "大气电", "生物仿生", "通信史"})
        self.assertTrue(all(any(term in plan.query for term in ("电", "闪电", "神经元")) for plan in plans[1:]))

    def test_single_character_electricity_profile_does_not_capture_movies(self):
        plans = build_search_plans("电影", 100)
        self.assertTrue(all("电影" in plan.query for plan in plans[1:]))
        self.assertNotIn("生物电", {plan.bridge for plan in plans})

    def test_generic_cross_queries_keep_original_topic(self):
        plans = build_search_plans("嘉豪", 100)
        self.assertTrue(all("嘉豪" in plan.query for plan in plans[1:]))

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
        request_urls = [call.args[0].full_url for call in mocked.call_args_list]
        self.assertTrue(any("format=json" in url for url in request_urls))
        self.assertTrue(any("language=zh-CN" in url for url in request_urls))
        self.assertEqual(
            {engine for engine in ("yandex", "zapmeta") if any(f"engines={engine}" in url for url in request_urls)},
            {"yandex", "zapmeta"},
        )

    def test_chinese_query_uses_concept_engines(self):
        response = {"results": [{"title": "鸟的分类", "url": "https://example.com/birds", "content": "鸟类知识"}]}
        plan = SearchPlan("主题分面", "鸟 分类", "分类体系", "分类。", 2, "鸟")
        with patch("server.urllib.request.urlopen", return_value=fake_json_response(response)) as mocked:
            results = searxng_search(plan, 3)
        self.assertTrue(results)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            {engine for engine in ("yandex", "zapmeta") if any(f"engines={engine}" in call.args[0].full_url for call in mocked.call_args_list)},
            {"yandex", "zapmeta"},
        )

    def test_single_character_anchor_keeps_valid_result_and_filters_noise(self):
        response = {"results": [
            {"title": "鸟类演化", "url": "https://example.com/birds", "content": "鸟的起源"},
            {"title": "Repurpose projects", "url": "https://noise.example.com/", "content": "unrelated English page"},
        ]}
        plan = SearchPlan("主题分面", "鸟 历史 演化", "历史演化", "历史。", 2, "鸟")
        with patch("server.urllib.request.urlopen", return_value=fake_json_response(response)):
            results = searxng_search(plan, 4)
        self.assertEqual([result.title for result in results], ["鸟类演化"])

    def test_one_chinese_engine_can_succeed_when_another_is_empty(self):
        bing_payload = {
            "results": [
                {"title": "嘉豪（网络流行词）", "url": "https://example.com/jiahao", "content": "嘉豪梗的含义与来源"}
            ]
        }
        plan = SearchPlan("原主题", "嘉豪", "原始问题", "直接相关。", 0, "嘉豪")

        def response_by_engine(request, **_kwargs):
            return fake_json_response(bing_payload if "engines=zapmeta" in request.full_url else {"results": []})

        with patch("server.urllib.request.urlopen", side_effect=response_by_engine) as mocked:
            results = searxng_search(plan, 3)
        self.assertEqual(results[0].title, "嘉豪（网络流行词）")
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(any("engines=yandex" in call.args[0].full_url for call in mocked.call_args_list))
        self.assertTrue(any("engines=zapmeta" in call.args[0].full_url for call in mocked.call_args_list))

    def test_unknown_query_does_not_use_unrelated_original_fallback(self):
        with patch("server.searxng_search", return_value=[]):
            payload = search("不存在的陌生词", 0, 10)
        self.assertEqual(payload["mode"], "fallback")
        self.assertEqual(payload["results"], [])

    def test_electricity_does_not_use_code_curated_fallback(self):
        with patch("server.searxng_search", return_value=[]):
            payload = search("电", 100, 10)
        titles = [result["title"] for result in payload["results"]]
        self.assertFalse(any("MDN" in title or "web.dev" in title for title in titles))

    def test_electricity_curated_pool_can_fill_three_pages(self):
        with patch("server.searxng_search", return_value=[]):
            pages = [search("电", 100, 10, page=number) for number in (1, 2, 3)]
        self.assertEqual(pages[0]["pagination"]["total_pages"], 3)
        self.assertTrue(all(payload["results"] for payload in pages))
        url_sets = [{result["url"] for result in payload["results"]} for payload in pages]
        self.assertFalse(url_sets[0] & url_sets[1])
        self.assertFalse(url_sets[1] & url_sets[2])

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

    def test_searxng_filters_results_without_query_connection(self):
        response = {
            "results": [
                {"title": "AFL Season Ladder", "url": "https://sport.example/a", "content": "Australian football standings"},
                {"title": "全球海底电缆地图", "url": "https://map.example/cable", "content": "海底通信基础设施维修"},
            ]
        }
        plan = SearchPlan("跨域方向", "海底电缆 维修", "隐形基础设施", "连接电与通信。", 72)
        with patch("server.urllib.request.urlopen", return_value=fake_json_response(response)):
            results = searxng_search(plan, 3)
        self.assertEqual([result.title for result in results], ["全球海底电缆地图"])

    def test_cross_domain_result_must_match_two_planned_concepts(self):
        response = {
            "results": [
                {"title": "电报新手注册与进群", "url": "https://chat.example/", "content": "即时通信软件教程"},
                {"title": "有线电报与莫尔斯电码", "url": "https://history.example/", "content": "电信史上的早期电气通信"},
            ]
        }
        plan = SearchPlan("跨域方向", "有线电报 莫尔斯 电信史", "通信史", "电改变通信距离。", 82)
        with patch("server.urllib.request.urlopen", return_value=fake_json_response(response)):
            results = searxng_search(plan, 3)
        self.assertEqual([result.title for result in results], ["有线电报与莫尔斯电码"])

    def test_search_reports_searxng_mode(self):
        def live_result(plan, _limit, page=1):
            slug = __import__("urllib.parse").parse.quote(plan.query)
            return [__import__("server").SearchResult(
                f"实时结果 {page}", f"https://example.com/{slug}/{page}", "摘要", "example.com", "example.com", plan.bridge, plan.reason, plan.distance
            )]

        with patch("server.searxng_search", side_effect=live_result):
            payload = search("代码优化", 20, 3)
        self.assertEqual(payload["mode"], "searxng")
        self.assertTrue(payload["search_backend"]["available"])

    def test_search_runs_multiple_plans_concurrently_and_caches_result(self):
        def slow_result(plan, _limit, _page=1):
            time.sleep(0.08)
            slug = __import__("urllib.parse").parse.quote(plan.query)
            return [__import__("server").SearchResult(
                plan.query, f"https://example.com/{slug}", plan.query, "example.com", "example.com", plan.bridge, plan.reason, plan.distance
            )]

        started = time.perf_counter()
        with patch("server.searxng_search", side_effect=slow_result) as mocked:
            first = search("代码优化", 60, 10)
            elapsed = time.perf_counter() - started
            second = search("代码优化", 60, 10)
        self.assertLess(elapsed, 0.25)
        self.assertEqual(mocked.call_count, 5)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])

    def test_page_is_sent_to_searxng_and_changes_cache_key(self):
        response = {"results": [{"title": "代码优化分页结果", "url": "https://example.com/page", "content": "代码优化"}]}
        plan = SearchPlan("原主题", "代码优化", "原始问题", "直接相关。", 0)
        with patch("server.urllib.request.urlopen", return_value=fake_json_response(response)) as mocked:
            searxng_search(plan, 3, page=2)
        requested_pages = {call.args[0].full_url.split("pageno=")[1].split("&")[0] for call in mocked.call_args_list}
        self.assertEqual(requested_pages, {"2"})

        def paged_result(plan, requested_limit, page=1):
            return [__import__("server").SearchResult(
                f"{plan.query} 结果 {index}", f"https://example.com/{__import__('urllib.parse').parse.quote(plan.query)}/{index}", "摘要", f"source-{index}.example.com", "example.com", plan.bridge, plan.reason, plan.distance
            ) for index in range(requested_limit)]

        with patch("server.searxng_search", side_effect=paged_result):
            first = search("代码优化", 0, 3, page=1)
            second = search("代码优化", 0, 3, page=2)
        self.assertNotEqual(first["results"][0]["url"], second["results"][0]["url"])
        self.assertEqual(second["page"], 2)
        self.assertGreaterEqual(first["pagination"]["total_pages"], 2)
        self.assertTrue(second["cached"])

    def test_candidate_pool_produces_three_non_overlapping_pages(self):
        def many_results(plan, requested_limit, _page=1):
            slug = __import__("urllib.parse").parse.quote(plan.query)
            return [__import__("server").SearchResult(
                f"{plan.query} {index}", f"https://source-{slug}-{index}.example/page", plan.query,
                f"source-{slug}-{index}.example", "example.com", plan.bridge, plan.reason, plan.distance
            ) for index in range(requested_limit)]

        with patch("server.searxng_search", side_effect=many_results):
            pages = [search("代码优化", 60, 10, page=number) for number in (1, 2, 3)]
        url_sets = [{result["url"] for result in payload["results"]} for payload in pages]
        self.assertTrue(all(len(urls) == 10 for urls in url_sets))
        self.assertFalse(url_sets[0] & url_sets[1])
        self.assertFalse(url_sets[1] & url_sets[2])
        self.assertEqual(pages[0]["pagination"]["total_pages"], 3)


if __name__ == "__main__":
    unittest.main()
