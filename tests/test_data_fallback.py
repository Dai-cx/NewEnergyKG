# -*- coding: utf-8 -*-
"""
LocalDataStore 本地兜底模块的单元测试
"""

import pytest

from qa.data_fallback import LocalDataStore


@pytest.fixture
def store(sample_data_path):
    """用样例 JSON 构造数据存储"""
    return LocalDataStore(data_path=sample_data_path)


class TestDataLoading:
    def test_load_count(self, store):
        assert store.count() == 3

    def test_get_tech(self, store):
        tech = store.get_tech("磷酸铁锂电池")
        assert tech is not None
        assert tech["category"] == "锂离子电池"

    def test_get_missing_tech_returns_none(self, store):
        assert store.get_tech("不存在") is None

    def test_get_all_names(self, store, sample_technologies):
        expected = {t["name"] for t in sample_technologies}
        assert set(store.get_all_names()) == expected

    def test_get_categories(self, store):
        categories = store.get_categories()
        assert "锂离子电池" in categories
        assert "液流电池" in categories

    def test_missing_data_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LocalDataStore(data_path=tmp_path / "nope.json")


class TestFallbackAnswers:
    def test_chat_fallback(self, store):
        ans = store.generate_fallback_answer("你好", "chat")
        assert "新能源" in ans

    def test_unknown_fallback(self, store):
        ans = store.generate_fallback_answer("随便说点什么", "unknown")
        assert "抱歉" in ans

    def test_aggregate_fallback(self, store):
        ans = store.generate_fallback_answer("一共有多少种技术？", "aggregate")
        assert "3" in ans

    def test_list_fallback(self, store):
        ans = store.generate_fallback_answer("有哪些储能技术？", "list")
        # 样例数据中没有名称含"储能"的技术，会回退到全量列表（含全部 3 个名称）
        assert "全钒液流电池" in ans

    def test_compare_fallback_from_question_text(self, store):
        ans = store.generate_fallback_answer(
            "三元锂电池和磷酸铁锂电池哪个好？", "compare", entities=None
        )
        assert "三元锂电池" in ans
        assert "磷酸铁锂电池" in ans
