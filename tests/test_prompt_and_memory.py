# -*- coding: utf-8 -*-
"""
PromptBuilder 与 ConversationMemory 的单元测试
"""

import pytest

from qa.memory import ConversationMemory
from qa.prompt_builder import PromptBuilder


# ==================== PromptBuilder ====================


class TestPromptBuilder:
    def test_system_prompt_contains_role(self):
        sp = PromptBuilder.build_system_prompt("property")
        assert "新能源" in sp
        assert "知识图谱数据" in sp

    def test_intent_extra_prompt_compare(self):
        sp = PromptBuilder.build_system_prompt("compare")
        assert "对比" in sp

    def test_user_prompt_basic(self):
        up = PromptBuilder.build_user_prompt(question="磷酸铁锂电池有什么优点？")
        assert "磷酸铁锂电池有什么优点？" in up

    def test_user_prompt_with_entities(self):
        entities = [{"name": "磷酸铁锂电池", "type": "exact_contained", "score": 98}]
        up = PromptBuilder.build_user_prompt(question="Q", entities=entities)
        assert "- 磷酸铁锂电池" in up

    def test_user_prompt_with_kg_context(self):
        ctx = {"technology": {"name": "磷酸铁锂电池", "desc": "安全性高"}}
        up = PromptBuilder.build_user_prompt(question="Q", kg_context=ctx)
        assert "知识图谱数据" in up
        assert '"name": "磷酸铁锂电池"' in up

    def test_user_prompt_with_history(self):
        history = [
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ]
        up = PromptBuilder.build_user_prompt(question="Q", history=history)
        assert "上一问" in up
        assert "上一答" in up

    def test_user_prompt_with_references(self):
        references = [
            {"text": "宁德时代是全球动力电池龙头。", "source": "企业.md", "section": "电池企业"},
            {"text": "液流电池适合长时储能。", "source": "储能.md", "section": "液流电池"},
        ]
        up = PromptBuilder.build_user_prompt(question="宁德时代是谁？", references=references)
        assert "参考资料" in up
        assert "[1] 来源：企业.md（章节：电池企业）" in up
        assert "宁德时代是全球动力电池龙头。" in up
        assert "[2]" in up
        assert "储能.md" in up

    def test_user_prompt_references_after_entities(self):
        entities = [{"name": "宁德时代", "type": "exact", "score": 98}]
        references = [{"text": "t1", "source": "s.md", "section": "sec"}]
        up = PromptBuilder.build_user_prompt(
            question="Q", entities=entities, references=references
        )
        # 参考资料段落应出现在实体信息之后
        assert up.index("参考资料") > up.index("识别到的实体")


# ==================== ConversationMemory ====================


class TestConversationMemory:
    def test_empty_without_session(self):
        mem = ConversationMemory()
        assert mem.get_history(None) == []
        assert mem.get_history("s1") == []

    def test_add_and_get_exchange(self):
        mem = ConversationMemory()
        mem.add_exchange("s1", "你好", "你好！我是新能源助手")
        mem.add_exchange("s1", "磷酸铁锂电池的优点？", "安全性高、循环寿命长")

        history = mem.get_history("s1")
        assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
        assert history[0]["content"] == "你好"
        assert history[-1]["content"] == "安全性高、循环寿命长"

    def test_max_rounds_truncation(self):
        # max_rounds=1 → 每会话最多保留 1 轮 = 2 条消息（user + assistant）
        mem = ConversationMemory(max_rounds=1)
        mem.add_exchange("s1", "q1", "a1")
        mem.add_exchange("s1", "q2", "a2")

        history = mem.get_history("s1")
        assert len(history) == 2
        assert history[0]["content"] == "q2"

    def test_clear_session(self):
        mem = ConversationMemory()
        mem.add_exchange("s1", "q", "a")
        mem.clear("s1")
        assert mem.get_history("s1") == []

    def test_count(self):
        mem = ConversationMemory()
        mem.add_exchange("s1", "q", "a")
        mem.add_exchange("s2", "q", "a")
        assert mem.count() == 2
