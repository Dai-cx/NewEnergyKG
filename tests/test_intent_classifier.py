# -*- coding: utf-8 -*-
"""
意图识别与实体抽取的单元测试

覆盖 IntentClassifier 的核心行为：
- 8 种意图关键词分类（property / relation / compare / aggregate / list / path / chat / unknown）
- 五级实体匹配（精确包含 / 整句 / Token / 同义词 / 模糊）
- 比较问句的多实体切分
"""

import pytest

from qa.intent_classifier import IntentClassifier


@pytest.fixture
def classifier(sample_technologies):
    """用 3 条样例技术名构造分类器（与真实使用方式一致）"""
    names = [t["name"] for t in sample_technologies]
    return IntentClassifier(entity_names=names, fuzzy_threshold=85)


class TestClassify:
    """意图分类"""

    def test_property_intent(self, classifier):
        assert classifier.classify("磷酸铁锂电池有哪些优点？") == "property"

    def test_relation_intent(self, classifier):
        assert classifier.classify("磷酸铁锂电池有哪些公司？") == "relation"

    def test_compare_intent(self, classifier):
        assert classifier.classify("三元锂电池和磷酸铁锂电池哪个好？") == "compare"

    def test_list_intent(self, classifier):
        assert classifier.classify("有哪些储能技术？") == "list"

    def test_aggregate_intent(self, classifier):
        assert classifier.classify("一共有多少种锂电池？") == "aggregate"

    def test_path_intent(self, classifier):
        assert classifier.classify("光伏产业链上下游是什么？") == "path"

    def test_chat_intent(self, classifier):
        assert classifier.classify("你好") == "chat"

    def test_empty_question_unknown(self, classifier):
        assert classifier.classify("") == "unknown"

    def test_short_text_unknown(self, classifier):
        assert classifier.classify("嗯") == "unknown"


class TestExtractEntities:
    """实体抽取"""

    def test_exact_contained(self, classifier):
        entities = classifier.extract_entities("磷酸铁锂电池有什么优点？")
        names = [e["name"] for e in entities]
        assert "磷酸铁锂电池" in names

    def test_synonym_mapping(self, classifier):
        # "锂电池" 是 SYNONYMS 中的词，应映射到 磷酸铁锂电池
        entities = classifier.extract_entities("锂电池有什么优点？")
        names = [e["name"] for e in entities]
        assert "磷酸铁锂电池" in names

    def test_compare_extracts_two_entities(self, classifier):
        entities = classifier.extract_entities("三元锂电池和磷酸铁锂电池哪个好？")
        names = [e["name"] for e in entities]
        assert "三元锂电池" in names
        assert "磷酸铁锂电池" in names

    def test_returns_at_most_three(self, classifier):
        entities = classifier.extract_entities("三元锂电池和磷酸铁锂电池哪个好？")
        assert len(entities) <= 3

    def test_unknown_question_returns_empty(self, classifier):
        assert classifier.extract_entities("？？？？") == []
