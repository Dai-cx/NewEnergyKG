# -*- coding: utf-8 -*-
"""
Week2 D6：检索失败 → 改写重试 链路测试

场景：
    引擎先按用户问题检索（图谱 + 文档），若两者都没有任何证据，
    且问题不是闲聊/未知、且 LLM 可用 → 触发"检索失败改写"
    （换一种说法再检索一次，最多 RETRY_ROUNDS 轮）。

覆盖：
1. 首次零命中 → 改写重试 → 第二次命中：retry_count=1、引用注入、实体更新；
2. 首次就命中 → 不触发重试（0 次额外 LLM 调用）；
3. LLM 返回与原问题相同的改写 → 视为无进展，停止重试不空转；
4. 闲聊问题零命中 → 不重试（闲聊不需要检索证据）。
"""

import pytest

from qa import config
from qa.answer_engine import AnswerEngine
from qa.retrieval.base import RetrievalResult, Retriever


class RouteLLM:
    """按 System Prompt 内容分发响应的脚本 LLM（意图 / 重试改写 / 主问答）"""

    provider = "fake"
    model = "fake-model"

    def __init__(self, intent='{"intent": "property"}',
                 retry_text="磷酸铁锂电池的优点有哪些方面？",
                 main_text="这是基于改写后问句检索到的资料生成的回答。"):
        self.intent = intent
        self.retry_text = retry_text
        self.main_text = main_text
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if "意图分类器" in system_prompt:
            content = self.intent
        elif "检索失败改写器" in system_prompt:
            content = self.retry_text
        else:  # 主问答（含查询改写器，本测试无历史不会触发）
            content = self.main_text
        return {
            "content": content,
            "model": self.model,
            "provider": self.provider,
            "elapsed_ms": 1,
        }


class EmptyThenHitRetriever(Retriever):
    """
    记录每次检索的 query；只对 miss_on 问句返回空，其余返回一条参考资料。
    用来模拟"原问法零召回、改写后命中"的检索场景。
    """

    name = "hybrid-stub"

    def __init__(self, miss_on: str, hit_text="全钒液流电池适合长时储能。"):
        self.miss_on = miss_on
        self.hit_text = hit_text
        self.queries = []

    def retrieve(self, query, top_k=5):
        self.queries.append(query)
        if query == self.miss_on:
            return []
        return [RetrievalResult(
            text=self.hit_text,
            score=0.9,
            source="vector+bm25",
            doc_id="doc-hit",
            section="长时储能",
            extra={"sources": ["vector", "bm25"]},
        )]


@pytest.fixture
def engine_env(sample_data_path, monkeypatch):
    """样例数据 + 注入脚本 LLM 的引擎环境；返回可复用的构造器"""
    monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
    monkeypatch.setattr(config, "RETRY_ROUNDS", 1)

    def build(llm, retriever, enable_documents=True):
        import qa.answer_engine as ae_module
        monkeypatch.setattr(ae_module, "create_llm_client", lambda: llm)
        return AnswerEngine(
            enable_kg=False,
            enable_documents=enable_documents,
            document_retriever=retriever,
        )

    return build


RAW = "磷酸铁锂电池的优点有哪些？"
ALT = "磷酸铁锂电池的优点有哪些方面？"


class TestRetryOnZeroHit:
    def test_retry_then_hit(self, engine_env):
        llm = RouteLLM()
        retriever = EmptyThenHitRetriever(miss_on=RAW)
        engine = engine_env(llm, retriever)

        result = engine.answer(RAW, session_id="s-retry")

        # 检索发生两次：原问法零命中 → 改写问法命中
        assert retriever.queries == [RAW, ALT]
        assert result["retry_count"] == 1
        assert result["retry_questions"] == [ALT]
        assert result["resolved_question"] == ALT
        assert result["question"] == RAW  # 原始输入保留
        assert result["references"], "重试后应检索到参考资料"
        assert result["references"][0]["text"] == "全钒液流电池适合长时储能。"
        assert result["entities"][0]["name"] == "磷酸铁锂电池"

        # LLM 调用序列：意图路由 → 检索失败改写 → 主问答（3 次）
        systems = [c["system"] for c in llm.calls]
        assert sum("检索失败改写器" in s for s in systems) == 1
        # 主问答的 Prompt 使用改写后的问句与命中的引用
        main_call = llm.calls[-1]
        assert ALT in main_call["user"]
        assert "全钒液流电池适合长时储能" in main_call["user"]

    def test_no_retry_when_first_hit(self, engine_env):
        llm = RouteLLM()
        retriever = EmptyThenHitRetriever(miss_on="绝不命中")
        engine = engine_env(llm, retriever)

        result = engine.answer(RAW, session_id="s-hit")

        assert retriever.queries == [RAW]
        assert result["retry_count"] == 0
        assert result["retry_questions"] == []
        # 没有触发重试改写调用（只有意图路由 + 主问答）
        assert len(llm.calls) == 2

    def test_no_progress_stops_retry(self, engine_env):
        # LLM 把问题原样返回 → 视为无进展，不应再检索
        llm = RouteLLM(retry_text=RAW)
        retriever = EmptyThenHitRetriever(miss_on=RAW)
        engine = engine_env(llm, retriever)

        result = engine.answer(RAW, session_id="s-same")

        assert retriever.queries == [RAW]  # 只检索了一次
        assert result["retry_count"] == 0
        assert result["references"] == []

    def test_chat_zero_hit_no_retry(self, engine_env):
        llm = RouteLLM(intent='{"intent": "chat"}')
        retriever = EmptyThenHitRetriever(miss_on="你好")
        engine = engine_env(llm, retriever)

        result = engine.answer("你好", session_id="s-chat")

        assert result["intent"] == "chat"
        assert result["retry_count"] == 0
        assert retriever.queries == ["你好"]
