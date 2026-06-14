#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问答引擎

负责编排整个问答流程：
1. 接收用户问题
2. 意图识别与实体抽取
3. 从知识图谱检索结构化上下文（KG+RAG）
4. 构建 Prompt 并调用 LLM
5. LLM 失败时启用本地 JSON 规则兜底
6. 组装统一响应格式
"""

import time
import traceback
from typing import Dict, Any, Optional, List

from qa import config
from qa.intent_classifier import IntentClassifier
from qa.data_fallback import LocalDataStore
from qa.prompt_builder import PromptBuilder
from qa.llm_client import create_llm_client, LLMError
from qa.kg_client import KGClient, _NullKGClient


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

    def __init__(self, enable_kg: bool = True):
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
        # 初始化 Neo4j 知识图谱客户端（未配置密码时自动禁用）
        self.kg_client = KGClient() if enable_kg else _NullKGClient()
        self.prompt_builder = PromptBuilder()

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
                traceback.print_exc()
        return None

    # ==================== 核心问答接口 ====================
    def answer(self, question: str) -> Dict[str, Any]:
        """
        回答用户问题，返回统一格式响应。

        Returns:
            {
                "question": str,
                "answer": str,
                "intent": str,
                "entities": list,
                "llm_used": bool,
                "llm_model": str | None,
                "source": str,
                "response_time_ms": int,
            }
        """
        start = time.time()

        # Step 1: 意图识别与实体抽取
        analysis = self.classifier.analyze(question)
        intent = analysis["intent"]
        entities = analysis["entities"]
        top_entity = entities[0]["name"] if entities else None

        # Step 2: 知识图谱检索
        kg_context = self._retrieve_from_kg(entities, intent, analysis["question"])
        kg_used = kg_context is not None

        # Step 3: 构建 Prompt
        system_prompt = self.prompt_builder.build_system_prompt(intent)
        user_prompt = self.prompt_builder.build_user_prompt(
            question=analysis["question"],
            intent=intent,
            entities=entities,
            kg_context=kg_context,
        )

        # Step 4: 调用 LLM 生成回答
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
                    traceback.print_exc()
                # LLM 失败时，尝试本地兜底
                answer = self.data_store.generate_fallback_answer(
                    question=analysis["question"],
                    intent=intent,
                    entity_name=top_entity,
                    entities=entities,
                )
                source = "local_fallback"
        else:
            # 未配置 LLM，直接使用本地兜底
            answer = self.data_store.generate_fallback_answer(
                question=analysis["question"],
                intent=intent,
                entity_name=top_entity,
                entities=entities,
            )

        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "question": analysis["question"],
            "answer": answer,
            "intent": intent,
            "entities": entities,
            "llm_used": llm_used,
            "llm_model": llm_model,
            "source": source,
            "kg_used": kg_used,
            "kg_context": kg_context,
            "response_time_ms": elapsed_ms,
        }

    def get_status(self) -> Dict[str, Any]:
        """返回引擎状态信息"""
        kg_status = self.kg_client.get_status()
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
        }
