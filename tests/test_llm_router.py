# -*- coding: utf-8 -*-
"""
Week2 D1：LLM 查询理解路由（改写 + 意图路由）的单元测试

测试要点：
1. 无 LLM（离线）→ 整条链路自动降级为规则，行为与 Week1 一致；
2. LLM 可用 → 改写器把"它/这个"补成自包含问题，意图走 LLM 路由；
3. LLM 输出脏/不可解析/抛异常 → 各自降级，绝不中断问答主链路；
4. AnswerEngine 集成：多轮对话中"那它的能量密度呢？" 被改写后再检索/生成。
"""

import pytest

from qa import config
from qa.answer_engine import AnswerEngine
from qa.intent_classifier import IntentClassifier
from qa.llm_router import LLMRouter
from qa.llm_client import LLMError


# ==================== 脚本化 FakeLLM ====================


class ScriptedLLM:
    """
    按 System Prompt 内容分发响应的假 LLM：
    - 含"意图分类器" → 返回意图 JSON
    - 含"查询改写器" → 返回改写后的问句
    - 其他（主问答）  → 返回固定回答文本
    """

    provider = "fake"
    model = "fake-llm"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        content = self.handler(system_prompt, user_prompt)
        return {
            "content": content,
            "model": self.model,
            "provider": self.provider,
            "elapsed_ms": 1,
        }


class RaisingLLM:
    """总是抛 LLMError 的假 LLM：验证 LLM 挂了也能正常降级"""

    provider = "fake"
    model = "fake-llm"

    def __init__(self):
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append(system_prompt)
        raise LLMError("模拟 DashScope 超时")


def scripted_handler(system_prompt, user_prompt):
    """默认脚本：意图 → property；改写 → 补全到磷酸铁锂电池的循环寿命"""
    if "意图分类器" in system_prompt:
        return '{"intent": "property"}'
    if "查询改写器" in system_prompt:
        return "磷酸铁锂电池的循环寿命如何？"
    raise AssertionError(f"测试未覆盖的 System Prompt: {system_prompt[:50]}")


def build_router(llm=None, intent_mode="auto", rewrite_mode="auto"):
    classifier = IntentClassifier(
        entity_names=["磷酸铁锂电池", "三元锂电池", "全钒液流电池"],
        fuzzy_threshold=85,
    )
    return LLMRouter(
        llm_client=llm,
        classifier=classifier,
        intent_mode=intent_mode,
        rewrite_mode=rewrite_mode,
    )


HISTORY = [
    {"role": "user", "content": "磷酸铁锂电池的循环寿命长吗？"},
    {"role": "assistant", "content": "磷酸铁锂电池循环寿命长，一般超过 3000 次。"},
]


# ==================== LLMRouter：离线/规则兜底 ====================


class TestRulesFallback:
    def test_no_llm_uses_rules_intent_and_entities(self):
        router = build_router(llm=None)
        analysis = router.analyze("三元锂电池和磷酸铁锂电池哪个好？")
        assert analysis["intent"] == "compare"
        assert analysis["intent_source"] == "rules"
        assert analysis["rewrite_applied"] is False
        names = [e["name"] for e in analysis["entities"]]
        assert "磷酸铁锂电池" in names
        assert "三元锂电池" in names
        # 与 Week1 行为一致：question 即原问题
        assert analysis["question"] == "三元锂电池和磷酸铁锂电池哪个好？"

    def test_llm_failure_degrades_to_rules(self):
        router = build_router(llm=RaisingLLM())
        analysis = router.analyze("全钒液流电池的成本如何？", history=HISTORY)
        assert analysis["intent"] == "property"
        assert analysis["intent_source"] == "rules"
        # 改写失败 → 原问题兜底，链路不断
        assert analysis["question"] == "全钒液流电池的成本如何？"
        assert analysis["rewrite_applied"] is False


# ==================== LLMRouter：查询改写 ====================


class TestQueryRewrite:
    def test_coreference_rewritten_with_history(self):
        fake = ScriptedLLM(scripted_handler)
        router = build_router(llm=fake)
        analysis = router.analyze("它的循环寿命如何？", history=HISTORY)

        assert analysis["question"] == "磷酸铁锂电池的循环寿命如何？"
        assert analysis["rewrite_applied"] is True
        assert analysis["rewrite_used_llm"] is True
        assert analysis["intent"] == "property"
        assert analysis["intent_source"] == "llm"
        # 改写后问题可被规则实体抽取直接命中
        names = [e["name"] for e in analysis["entities"]]
        assert "磷酸铁锂电池" in names
        # 一共 2 次 LLM 调用：改写 1 次 + 意图路由 1 次
        assert len(fake.calls) == 2

    def test_no_history_skips_rewrite_but_still_routes_intent(self):
        fake = ScriptedLLM(scripted_handler)
        router = build_router(llm=fake)
        analysis = router.analyze("它的循环寿命如何？")
        # 没有历史就无法消解指代 → 不调用改写器（只发 1 次意图路由调用）
        assert analysis["question"] == "它的循环寿命如何？"
        assert analysis["rewrite_applied"] is False
        assert len(fake.calls) == 1

    def test_rewrite_mode_off_disables_llm_call(self):
        fake = ScriptedLLM(scripted_handler)
        router = build_router(llm=fake, rewrite_mode="off")
        analysis = router.analyze("它的循环寿命如何？", history=HISTORY)
        assert analysis["rewrite_applied"] is False
        # rewrite 关闭 → 只发意图路由 1 次
        assert len(fake.calls) == 1

    def test_chat_turn_skips_rewrite(self):
        fake = ScriptedLLM(scripted_handler)
        router = build_router(llm=fake)
        analysis = router.analyze("谢谢", history=HISTORY)
        # 闲聊不需要改写，也不该被误补成技术问题
        assert analysis["rewrite_applied"] is False
        assert len(fake.calls) == 1  # 仅意图路由

    def test_clean_rewritten_variants(self):
        cases = [
            ("磷酸铁锂电池的优点是什么？", "磷酸铁锂电池的优点是什么？"),
            ('"磷酸铁锂电池的优点是什么？"', "磷酸铁锂电池的优点是什么？"),
            ("改写后的问题：磷酸铁锂电池的优点是什么？",
             "磷酸铁锂电池的优点是什么？"),
            ("```json\n磷酸铁锂电池的优点是什么？\n```",
             "磷酸铁锂电池的优点是什么？"),
            ("先说明一下：这段讲的是上下文。\n磷酸铁锂电池的优点是什么？",
             "磷酸铁锂电池的优点是什么？"),
        ]
        for raw, expected in cases:
            assert LLMRouter.clean_rewritten(raw) == expected

    def test_empty_rewrite_output_falls_back_to_original(self):
        fake = ScriptedLLM(lambda s, u: "  ")
        router = build_router(llm=fake)
        analysis = router.analyze("它的循环寿命如何？", history=HISTORY)
        assert analysis["question"] == "它的循环寿命如何？"
        assert analysis["rewrite_applied"] is False


# ==================== LLMRouter：意图路由容错 ====================


class TestIntentRoutingRobustness:
    def test_intent_mode_rules_sends_no_llm_call(self):
        fake = ScriptedLLM(scripted_handler)
        router = build_router(llm=fake, intent_mode="rules")
        analysis = router.analyze("三元锂电池和磷酸铁锂电池哪个好？")
        assert analysis["intent"] == "compare"
        assert analysis["intent_source"] == "rules"
        assert fake.calls == []

    def test_malformed_intent_output_falls_back_to_rules(self):
        fake = ScriptedLLM(
            lambda s, u: "抱歉，我不太确定怎么归类……" if "意图分类器" in s
            else "磷酸铁锂电池的循环寿命如何？"
        )
        router = build_router(llm=fake)
        analysis = router.analyze("它的循环寿命如何？", history=HISTORY)
        # 改写成功（非意图输出），意图解析失败 → 规则兜底
        assert analysis["question"] == "磷酸铁锂电池的循环寿命如何？"
        assert analysis["intent"] == "property"
        assert analysis["intent_source"] == "rules"

    def test_invalid_intent_label_falls_back_to_rules(self):
        fake = ScriptedLLM(
            lambda s, u: '{"intent": "我要飞"}'
        )
        router = build_router(llm=fake)
        analysis = router.analyze("全钒液流电池的原理是什么？")
        assert analysis["intent"] == "property"
        assert analysis["intent_source"] == "rules"

    def test_chinese_alias_normalized_to_standard_label(self):
        # 模型可能输出中文意图（"属性"），应归一化为 property
        fake = ScriptedLLM(lambda s, u: '{"intent": "属性"}')
        router = build_router(llm=fake)
        analysis = router.analyze("全钒液流电池的原理是什么？")
        assert analysis["intent"] == "property"
        assert analysis["intent_source"] == "llm"

    def test_parse_intent_json_with_markdown_fence(self):
        content = '```json\n{"intent": "compare"}\n```'
        obj = LLMRouter.parse_intent_json(content)
        assert obj == {"intent": "compare"}
        # 前后有解释文字也能解析
        obj2 = LLMRouter.parse_intent_json('好的，分类结果是 {"intent": "list"}。')
        assert obj2 == {"intent": "list"}
        # 完全不是 JSON → None
        assert LLMRouter.parse_intent_json("不知道") is None


# ==================== AnswerEngine 集成：多轮指代消解 ====================


def answer_handler(system_prompt, user_prompt):
    """模拟完整链路：改写器 / 意图分类器 / 主问答"""
    if "意图分类器" in system_prompt:
        return '{"intent": "property"}'
    if "查询改写器" in system_prompt:
        return "磷酸铁锂电池的能量密度如何？"
    return "这是基于改写后自包含问题生成的回答。"


class TestEngineIntegration:
    def test_multi_turn_coreference_flow(self, sample_data_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        # 该场景无 KG/文档检索证据：关闭检索失败重试（D6 特性单独在
        # test_retry_engine.py 覆盖），避免脚本 LLM 被重试分支误触发
        monkeypatch.setattr(config, "RETRY_ROUNDS", 0)
        fake = ScriptedLLM(answer_handler)

        import qa.answer_engine as ae_module
        monkeypatch.setattr(ae_module, "create_llm_client", lambda: fake)

        engine = AnswerEngine(enable_kg=False, enable_documents=False)

        # 第一轮：完整问题，无指代 → 只走意图路由，不触发改写
        r1 = engine.answer("磷酸铁锂电池的循环寿命长吗？", session_id="s1")
        assert r1["query_rewritten"] is False
        assert r1["intent_source"] == "llm"

        # 第二轮：指代问句 → 应改写后再检索/生成
        r2 = engine.answer("那它的能量密度呢？", session_id="s1")
        assert r2["query_rewritten"] is True
        assert r2["resolved_question"] == "磷酸铁锂电池的能量密度如何？"
        # question 保留用户原始输入，前端可如实回显
        assert r2["question"] == "那它的能量密度呢？"
        assert r2["intent"] == "property"
        assert r2["intent_source"] == "llm"
        assert r2["entities"], "改写后应能抽取出磷酸铁锂电池实体"
        assert r2["entities"][0]["name"] == "磷酸铁锂电池"

        # 主问答的 Prompt 应使用改写后的自包含问题（而不是"那它的"）
        answer_calls = [c for c in fake.calls if "当前问题" in c["user"]]
        assert answer_calls, "应有主问答调用"
        last_answer_user = answer_calls[-1]["user"]
        assert "磷酸铁锂电池的能量密度如何？" in last_answer_user
        # 历史仍以原文形态注入（上一轮的原始问句出现在历史里，忠实还原对话）
        assert "磷酸铁锂电池的循环寿命长吗？" in last_answer_user

    def test_response_fields_shape(self, sample_data_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        monkeypatch.setattr(config, "OPENAI_API_BASE", "")
        engine = AnswerEngine(enable_kg=False, enable_documents=False)
        result = engine.answer("磷酸铁锂电池有哪些优点？", session_id="s2")
        for key in ("question", "resolved_question", "query_rewritten",
                    "rewrite_used_llm", "intent_source", "intent", "entities",
                    "answer", "references", "kg_used", "session_id"):
            assert key in result
