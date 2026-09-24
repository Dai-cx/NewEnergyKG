#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问答引擎

负责编排整个问答流程：
1. 接收用户问题
2. 查询理解：LLM 查询改写（多轮指代消解）+ LLM 意图路由，规则兜底
   （详见 qa/llm_router.py；无 LLM 时整条链路自动降级为纯规则）
3. 从知识图谱检索结构化上下文（KG+RAG）
4. 构建 Prompt 并调用 LLM
5. LLM 失败时启用本地 JSON 规则兜底
6. 组装统一响应格式
"""

import time
from typing import Dict, Any, Optional, List

from loguru import logger

from qa import config
from qa.intent_classifier import IntentClassifier
from qa.data_fallback import LocalDataStore
from qa.prompt_builder import PromptBuilder
from qa.llm_client import create_llm_client, LLMError
from qa.llm_router import LLMRouter
from qa.kg_client import KGClient, _NullKGClient
from qa.memory import ConversationMemory


class AnswerEngine:
    """新能源问答引擎"""

    # 问题关键词到 Neo4j 关系类型的映射
    RELATION_KEYWORDS = {
        "produced_by": ["公司", "企业", "生产", "厂商", "制造商"],
        "uses_material": ["材料"],
        "requires_equipment": ["设备"],
        "applies_to": ["应用", "场景"],
        "has_parameter": ["指标", "参数"],
        "supported_by": ["政策", "支持"],
        "competes_with": ["竞争", "替代"],
        "belongs_to": ["类别", "类型", "属于"],
    }

    def __init__(
        self,
        enable_kg: bool = True,
        enable_documents: bool = True,
        document_retriever: Optional[Any] = None,
    ):
        """
        Args:
            enable_kg: 是否启用知识图谱检索（对比实验时可关闭）。
            enable_documents: 是否尝试接入"文档混合检索"（向量 + BM25）。
            document_retriever: 可注入现成的文档检索器（便于测试）；
                                为 None 且 enable_documents 时按环境自动构建。
        """
        # 加载本地数据
        self.data_store = LocalDataStore()
        # 初始化意图分类器，注入所有技术名称用于实体匹配
        # 模糊匹配阈值可从环境变量调整，默认 85
        self.classifier = IntentClassifier(
            entity_names=self.data_store.get_all_names(),
            fuzzy_threshold=config.ENTITY_FUZZY_THRESHOLD,
        )
        # 初始化 LLM 客户端（未配置 API Key 时返回 None）
        self.llm_client = create_llm_client()
        # 查询理解路由器：LLM 改写 + LLM 意图路由，规则兜底（Week2 D1）
        self.router = LLMRouter(
            llm_client=self.llm_client,
            classifier=self.classifier,
            intent_mode=config.INTENT_ROUTING_MODE,
            rewrite_mode=config.QUERY_REWRITE_MODE,
        )
        # 初始化 Neo4j 知识图谱客户端（未配置密码时自动禁用）
        self.kg_client = KGClient() if enable_kg else _NullKGClient()
        self.prompt_builder = PromptBuilder()
        # 初始化会话记忆（纯内存，保留最近 N 轮）
        self.memory = ConversationMemory()
        # 文档混合检索器（向量 + BM25）：Qdrant 未就绪时自动降级为 None
        self.document_retriever = document_retriever
        if self.document_retriever is None and enable_documents:
            try:
                from qa.retrieval.factory import try_build_document_retriever
                self.document_retriever = try_build_document_retriever()
            except Exception as e:
                logger.warning(f"文档检索器初始化失败，本问答将不注入文档知识：{e}")
                self.document_retriever = None

    # ==================== 知识图谱检索 ====================
    def _detect_relation_type(self, question: str) -> Optional[str]:
        """根据问题关键词判断要查询的关系类型"""
        for rel_type, keywords in self.RELATION_KEYWORDS.items():
            for kw in keywords:
                if kw in question:
                    return rel_type
        return None

    def _detect_category(self, question: str) -> Optional[str]:
        """从问题中匹配已知技术类别，支持类别名被问题关键词包含或包含问题关键词"""
        categories = self.data_store.get_categories()
        # 优先匹配长类别名
        categories = sorted(categories, key=len, reverse=True)

        # 1. 精确匹配：类别名完整出现在问题中
        for category in categories:
            if category in question:
                return category

        # 2. 反向包含：问题中的关键词（长度>=2）被某个类别名包含
        #    例如问题"储能技术"可匹配类别"储能系统"
        keywords = [kw for kw in self.classifier.segment(question) if len(kw) >= 2]
        for category in categories:
            for kw in keywords:
                if kw in category:
                    return category

        return None

    def _retrieve_from_kg(
        self,
        entities: List[Dict[str, Any]],
        intent: str,
        question: str,
    ) -> Optional[Dict[str, Any]]:
        """
        从 Neo4j 知识图谱检索结构化上下文。

        根据意图与问题关键词选择查询策略：
        - property: 查询技术综合信息
        - relation: 查询特定关系（企业/材料/设备/应用等）
        - compare: 对比两个技术
        - aggregate/list: 查询类别下技术列表或统计
        """
        if not self.kg_client.is_connected():
            return None

        top_entity = entities[0]["name"] if entities else None

        # 比较意图：需要至少两个实体
        if intent == "compare":
            names = [e["name"] for e in entities[:2]]
            if len(names) < 2:
                # 兜底：从问题中再尝试找第二个实体
                names = self._extract_compare_entities(question)
            if len(names) >= 2:
                result = self.kg_client.compare_techs(names[0], names[1])
                if result:
                    return {"compare": result}
            return None

        # 聚合/列表意图：查询类别下技术
        if intent in ("aggregate", "list"):
            category = self._detect_category(question)
            if category:
                result = self.kg_client.get_techs_by_category(category)
                if result:
                    return {"category_list": result}
            # 查询全部技术统计
            return self._retrieve_all_techs_summary()

        # 没有识别到实体，尝试类别查询
        if not top_entity:
            category = self._detect_category(question)
            if category:
                result = self.kg_client.get_techs_by_category(category)
                if result:
                    return {"category_list": result}
            return None

        # 关系意图：判断具体关系类型
        if intent == "relation":
            rel_type = self._detect_relation_type(question)
            if rel_type:
                result = self.kg_client.get_related(top_entity, rel_type)
                if result:
                    return {"relation": result}
            # 关系类型不明确时返回综合信息
            overview = self.kg_client.get_tech_overview(top_entity)
            if overview:
                return {"overview": overview}
            return None

        # 属性/未知等意图：返回综合信息
        overview = self.kg_client.get_tech_overview(top_entity)
        if overview:
            return {"overview": overview}

        return None

    def _extract_compare_entities(self, question: str) -> List[str]:
        """从比较问题中抽取两个技术实体"""
        names = sorted(self.data_store.get_all_names(), key=len, reverse=True)
        found = []
        for name in names:
            if name in question and name not in found:
                found.append(name)
            if len(found) >= 2:
                break
        return found

    def _retrieve_all_techs_summary(self) -> Optional[Dict[str, Any]]:
        """获取全部技术数量与示例列表"""
        if not self.kg_client.is_connected():
            return None
        try:
            from neo4j import GraphDatabase
        except ImportError:
            return None
        try:
            with self.kg_client.driver.session() as session:
                record = session.run(
                    "MATCH (t:Technology) RETURN count(t) AS total, collect(t.name)[..20] AS examples"
                ).single()
                if record:
                    return {
                        "all_technologies": {
                            "total": record["total"],
                            "examples": record["examples"],
                        }
                    }
        except Exception:
            if config.DEBUG:
                logger.opt(exception=True).debug("图谱统计查询失败，已返回 None")
        return None

    # ==================== 文档检索（向量 + BM25）====================
    def _retrieve_documents(self, question: str) -> List[Dict[str, Any]]:
        """
        用文档混合检索器取出参考资料（含出处），供注入 Prompt 与展示。

        Returns:
            [{"text", "source", "section", "doc_id", "score"}, ...]
            检索器不可用或无命中时返回空列表（不影响问答主流程）。
        """
        if self.document_retriever is None:
            return []
        # HybridRetriever 有 .retrievers 属性（空列表=无可用子检索器）；
        # 测试注入的简单 Retriever 没有该属性，视为可用
        retrievers = getattr(self.document_retriever, "retrievers", None)
        if retrievers is not None and not retrievers:
            return []

        references = []
        try:
            results = self.document_retriever.retrieve(
                question, top_k=config.RETRIEVER_TOP_K
            )
            for r in results:
                sources = "+".join(r.extra.get("sources") or [r.source])
                references.append({
                    "text": r.text,
                    "source": sources,
                    "section": r.section,
                    "doc_id": r.doc_id,
                    "score": r.score,
                })
        except Exception as e:
            logger.warning(f"文档检索失败，跳过：{e}")
        return references

    # ==================== 核心问答接口 ====================
    def answer(self, question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        回答用户问题，返回统一格式响应。

        Args:
            question: 用户问题。
            session_id: 会话 ID，用于维护多轮对话记忆。为空时不保存历史。

        Returns:
            {
                "question": str,          # 用户原始问题
                "resolved_question": str, # 改写后的自包含问题（检索/生成用）
                "query_rewritten": bool,  # 本轮是否发生了查询改写
                "rewrite_used_llm": bool, # 改写是否由 LLM 完成
                "intent_source": str,     # "llm" | "rules"
                "retry_count": int,       # 检索失败后触发了几轮改写重试
                "retry_questions": list,  # 每轮重试实际使用的替代问句
                "answer": str,
                "intent": str,
                "entities": list,
                "llm_used": bool,
                "llm_model": str | None,
                "source": str,
                "references": list,   # 混合检索到的参考资料（含出处）
                "response_time_ms": int,
            }
        """
        start = time.time()

        # Step 1: 读取会话历史
        history = self.memory.get_history(session_id)

        # Step 2: 查询理解 —— LLM 改写（多轮指代消解）+ LLM 意图路由，规则兜底。
        # analysis["question"] 是改写后的"自包含问题"（检索与生成都基于它）；
        # analysis["raw_question"] 保留用户原始输入。
        analysis = self.router.analyze(question, history)
        intent = analysis["intent"]
        entities = analysis["entities"]
        effective_question = analysis["question"]
        raw_question = analysis["raw_question"]
        top_entity = entities[0]["name"] if entities else None

        # Step 3: 首次检索（知识图谱结构化 + 文档混合检索）
        kg_context = self._retrieve_from_kg(entities, intent, effective_question)
        references = self._retrieve_documents(effective_question)

        # Step 3.6: 检索失败 → 改写重试（Week2 D6，最多 config.RETRY_ROUNDS 轮）
        # 触发条件：图谱与文档都没有任何证据 且 不是闲聊/未知 且 有 LLM。
        # 本质是"零召回"的补救：换一种说法/补全实体再检索一次，
        # 为 Week3 LangGraph 的"反思 → 重试"节点提供可替换的最小版本。
        retry_count = 0
        retry_questions: List[str] = []
        while (
            kg_context is None
            and not references
            and intent not in ("chat", "unknown")
            and self.llm_client is not None
            and retry_count < config.RETRY_ROUNDS
        ):
            alt = self.router.retry_rewrite(
                effective_question, history, round_no=retry_count + 1
            )
            if alt is None:
                break  # LLM 无替代问法（或输出与原文相同）→ 停止重试，避免死循环
            retry_questions.append(alt)
            effective_question = alt
            # 新问句可能补全/换用了实体名，重新抽取以保证图谱命中
            if intent not in ("chat", "unknown"):
                entities = self.classifier.extract_entities(effective_question)
                top_entity = entities[0]["name"] if entities else None
            kg_context = self._retrieve_from_kg(entities, intent, effective_question)
            references = self._retrieve_documents(effective_question)
            retry_count += 1
            logger.debug(
                f"[retry] 第 {retry_count} 轮改写重试后："
                f"kg={'是' if kg_context else '否'} docs={len(references)} 条"
            )
        kg_used = kg_context is not None

        # Step 4: 构建 Prompt
        system_prompt = self.prompt_builder.build_system_prompt(intent)
        user_prompt = self.prompt_builder.build_user_prompt(
            question=effective_question,
            intent=intent,
            entities=entities,
            kg_context=kg_context,
            history=history,
            references=[{"text": r["text"], "source": r["source"],
                         "section": r["section"]} for r in references],
        )

        # Step 5: 调用 LLM 生成回答
        llm_result = None
        answer = ""
        source = "local_fallback"
        llm_used = False
        llm_model = None

        if self.llm_client is not None:
            try:
                llm_result = self.llm_client.generate(system_prompt, user_prompt)
                answer = llm_result["content"]
                llm_used = True
                llm_model = llm_result["model"]
                source = "llm"
            except LLMError as e:
                if config.DEBUG:
                    logger.opt(exception=True).debug("LLM 调用失败，切换到本地兜底回答")
                # LLM 失败时，尝试本地兜底（用改写后的自包含问题，命中率更高）
                answer = self.data_store.generate_fallback_answer(
                    question=effective_question,
                    intent=intent,
                    entity_name=top_entity,
                    entities=entities,
                )
                source = "local_fallback"
        else:
            # 未配置 LLM，直接使用本地兜底
            answer = self.data_store.generate_fallback_answer(
                question=effective_question,
                intent=intent,
                entity_name=top_entity,
                entities=entities,
            )

        # Step 6: 保存本轮问答到会话记忆（存原始用户输入，忠实还原对话）
        self.memory.add_exchange(session_id, raw_question, answer)

        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "question": raw_question,
            "resolved_question": effective_question,
            "query_rewritten": analysis["rewrite_applied"],
            "rewrite_used_llm": analysis["rewrite_used_llm"],
            "intent_source": analysis["intent_source"],
            "retry_count": retry_count,
            "retry_questions": retry_questions,
            "answer": answer,
            "intent": intent,
            "entities": entities,
            "llm_used": llm_used,
            "llm_model": llm_model,
            "source": source,
            "kg_used": kg_used,
            "kg_context": kg_context,
            "references": references,
            "session_id": session_id,
            "history_rounds": len(history) // 2,
            "response_time_ms": elapsed_ms,
        }

    def get_status(self) -> Dict[str, Any]:
        """返回引擎状态信息"""
        kg_status = self.kg_client.get_status()
        retrieval = self.document_retriever
        if retrieval is None:
            document_retrieval = False
            document_sources: List[str] = []
        elif hasattr(retrieval, "list_sources"):  # HybridRetriever
            document_retrieval = bool(retrieval.retrievers)
            document_sources = retrieval.list_sources()
        else:  # 测试注入的简单 Retriever
            document_retrieval = True
            document_sources = [retrieval.name]
        return {
            "llm_available": self.llm_client is not None,
            "llm_provider": self.llm_client.provider if self.llm_client else None,
            "llm_model": self.llm_client.model if self.llm_client else None,
            "data_loaded": self.data_store.count(),
            "kg_available": kg_status["available"],
            "kg_connected": kg_status["connected"],
            "kg_node_count": kg_status["node_count"],
            "kg_rel_count": kg_status["rel_count"],
            "kg_uri": kg_status["uri"],
            "document_retrieval": document_retrieval,
            "document_sources": document_sources,
            "memory_sessions": self.memory.count(),
            "memory_max_rounds": config.MAX_HISTORY_ROUNDS,
            "intent_routing_mode": config.INTENT_ROUTING_MODE,
            "query_rewrite_mode": config.QUERY_REWRITE_MODE,
            "retry_max_rounds": config.RETRY_ROUNDS,
        }
