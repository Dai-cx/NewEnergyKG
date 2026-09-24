# -*- coding: utf-8 -*-
"""
AnswerEngine 端到端测试（无 LLM、无真实 Neo4j 的降级路径 + 文档检索注入）

构造要点：
1. monkeypatch 把 config.DATA_PATH 指向样例 JSON（不依赖真实数据文件）
2. monkeypatch 清空 LLM Key → create_llm_client() 返回 None → 走本地兜底
3. enable_kg=False → 使用 _NullKGClient，不触碰真实 Neo4j
4. enable_documents=False / 注入 StubRetriever → 不连接真实 Qdrant
"""

import pytest

from qa import config
from qa.answer_engine import AnswerEngine
from qa.retrieval.base import RetrievalResult, Retriever


@pytest.fixture
def no_llm_engine(sample_data_path, monkeypatch):
    """无 LLM、无 KG、无文档检索的纯本地兜底引擎"""
    monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_BASE", "")
    return AnswerEngine(enable_kg=False, enable_documents=False)


class StubDocumentRetriever(Retriever):
    """固定返回两条参考资料的桩检索器（模拟向量+BM25 混合结果）"""

    name = "hybrid"

    def __init__(self, texts=None):
        self._results = []
        for i, t in enumerate(texts or ["宁德时代是全球动力电池龙头。"]):
            self._results.append(RetrievalResult(
                text=t,
                score=0.9,
                source="vector",
                doc_id=f"doc{i}",
                section=f"章节{i}",
                extra={"sources": ["vector", "bm25"], "chunk_index": 0},
            ))

    def retrieve(self, query, top_k=10):
        return self._results[:top_k]


class FakeLLM:
    """捕获 Prompt 的假 LLM，验证上下文注入内容。

    Week2 D1 起 AnswerEngine 会先经过 LLM 意图路由（router 也是同一个
    llm_client），因此这里对"意图分类器"System Prompt 返回合法 JSON，
    其余（主问答）返回固定生成文本。
    """

    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if "意图分类器" in system_prompt:
            content = '{"intent": "property"}'
        else:
            content = "这是基于检索知识的生成答案。"
        return {
            "content": content,
            "model": self.model,
            "provider": self.provider,
            "elapsed_ms": 1,
        }


class TestAnswerEngineFallback:
    def test_property_question_uses_local_fallback(self, no_llm_engine):
        result = no_llm_engine.answer(
            "磷酸铁锂电池有哪些优点？", session_id="test-session"
        )
        assert result["source"] == "local_fallback"
        assert result["llm_used"] is False
        assert result["intent"] == "property"
        assert "磷酸铁锂电池" in result["answer"]
        assert result["kg_used"] is False
        assert result["references"] == []

    def test_chat_question(self, no_llm_engine):
        result = no_llm_engine.answer("你好", session_id="s")
        assert result["intent"] == "chat"
        assert "新能源" in result["answer"]

    def test_history_rounds_counted(self, no_llm_engine):
        no_llm_engine.answer("你好", session_id="s")
        result = no_llm_engine.answer("磷酸铁锂电池有哪些优点？", session_id="s")
        # 已有一轮历史（你好/回复），本轮 history_rounds 应为 1
        assert result["history_rounds"] == 1

    def test_response_shape(self, no_llm_engine):
        result = no_llm_engine.answer("你好", session_id="s")
        for key in ("question", "answer", "intent", "entities", "source",
                    "references", "kg_context", "kg_used",
                    "session_id", "history_rounds", "response_time_ms"):
            assert key in result
        assert result["response_time_ms"] >= 0


class TestDocumentRetrievalInjection:
    @pytest.fixture
    def fake_llm_env(self, sample_data_path, monkeypatch):
        """构造带假 LLM + 桩文档检索器的引擎环境"""
        import qa.answer_engine as ae_module

        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        fake_llm = FakeLLM()
        monkeypatch.setattr(ae_module, "create_llm_client", lambda: fake_llm)
        return fake_llm

    def test_references_injected_into_prompt(
        self, sample_data_path, fake_llm_env, monkeypatch
    ):
        texts = ["宁德时代是全球动力电池龙头，2024 年装机量领先。",
                 "全钒液流电池适合四小时以上长时储能。"]
        stub = StubDocumentRetriever(texts=texts)
        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        engine = AnswerEngine(
            enable_kg=False,
            enable_documents=True,
            document_retriever=stub,
        )

        result = engine.answer("宁德时代是谁？", session_id="s")

        # 1) 响应携带参考资料（含出处与来源）
        assert result["references"], "应返回检索到的参考资料"
        assert result["references"][0]["source"] == "vector+bm25"
        assert result["references"][0]["section"] == "章节0"

        # 2) 参考资料确实被注入 LLM 的 user prompt
        #    （多一次调用来自 LLM 意图路由，主问答是最后一次调用）
        assert len(fake_llm_env.calls) == 2
        assert fake_llm_env.calls[0]["system"].startswith("你是对话系统的意图分类器")
        user_prompt = fake_llm_env.calls[-1]["user"]
        assert "参考资料" in user_prompt
        assert "[1]" in user_prompt
        assert "宁德时代是全球动力电池龙头" in user_prompt

        # 3) 走 LLM 路径
        assert result["source"] == "llm"
        assert result["llm_used"] is True

    def test_status_reports_document_retrieval(self, sample_data_path, monkeypatch):
        stub = StubDocumentRetriever()
        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        engine = AnswerEngine(enable_kg=False, document_retriever=stub)
        status = engine.get_status()
        assert status["document_retrieval"] is True
        assert "hybrid" in status["document_sources"]
