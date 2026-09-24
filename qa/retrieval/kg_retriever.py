# -*- coding: utf-8 -*-
"""
KG 检索器：把 Neo4j 图谱查询包装成统一 Retriever 接口

原来的 KG 检索逻辑在 answer_engine 内部、输出结构化 dict；
本检索器把它"收敛"成 RetrievalResult（可读文本 + 分数），
让上层可以用同一套代码消费"图谱事实"与"文档 chunk"两类知识。

策略（与问答引擎一致，但输出归一化）：
    - compare 意图 + ≥2 实体 → compare_techs
    - 单实体 + 关系关键词 → get_related（每项一条结果）
    - 单实体 → get_tech_overview（综合概览）
    - 无实体 → 返回空
"""

from typing import List, Optional

from loguru import logger

from qa.intent_classifier import IntentClassifier
from qa.kg_client import KGClient
from qa.retrieval.base import RetrievalResult, Retriever

# 问题关键词 → 图谱关系类型（与问答引擎保持一致的映射）
RELATION_KEYWORDS = {
    "produced_by": ["公司", "企业", "生产", "厂商", "制造商"],
    "uses_material": ["材料"],
    "requires_equipment": ["设备"],
    "applies_to": ["应用", "场景"],
    "has_parameter": ["指标", "参数"],
    "supported_by": ["政策", "支持"],
    "competes_with": ["竞争", "替代"],
}


def _detect_relation_type(question: str) -> Optional[str]:
    for rel_type, keywords in RELATION_KEYWORDS.items():
        for kw in keywords:
            if kw in question:
                return rel_type
    return None


class KGRetriever(Retriever):
    """知识图谱检索器"""

    name = "kg"

    def __init__(
        self,
        kg_client: KGClient,
        entity_names: Optional[List[str]] = None,
        classifier: Optional[IntentClassifier] = None,
        fuzzy_threshold: int = 85,
    ):
        """
        Args:
            kg_client: 已连接的 KGClient（未连接时 available() 返回 False）。
            entity_names: 知识库中全部技术名（供实体识别），
                          传 LocalDataStore().get_all_names() 即可。
            classifier: 可选注入现成 IntentClassifier（便于测试）。
        """
        self.kg_client = kg_client
        self.classifier = classifier or IntentClassifier(
            entity_names=entity_names or [],
            fuzzy_threshold=fuzzy_threshold,
        )

    # ==================== 各种查询 → 文本 ====================

    def _overview_result(self, name: str, entity_score: float) -> Optional[RetrievalResult]:
        data = self.kg_client.get_tech_overview(name)
        if not data:
            return None
        tech = data["technology"]
        lines = [
            f"[技术概览] {name}",
            f"描述：{tech.get('desc', '')}",
            f"原理：{tech.get('principle', '')}",
        ]
        for label, key in (("效率", "efficiency"), ("成本", "cost_level"),
                           ("发展阶段", "development_stage"), ("成熟度", "maturity")):
            value = tech.get(key)
            if value:
                lines.append(f"{label}：{value}")
        # 附上主要关联，方便回答"企业/材料"类追问
        for label, items in (("相关企业", data.get("companies")),
                             ("主要材料", data.get("materials")),
                             ("应用场景", data.get("applications"))):
            if items:
                lines.append(f"{label}：{'、'.join(items[:8])}")
        return RetrievalResult(
            text="\n".join(lines),
            score=entity_score,
            source=self.name,
            section=name,
            extra={"kg_type": "overview"},
        )

    def _relation_results(self, name: str, rel_type: str,
                          entity_score: float) -> List[RetrievalResult]:
        data = self.kg_client.get_related(name, rel_type)
        if not data:
            return []
        items = data.get(data.get("field", ""), []) or []
        relation_label = data.get("relation_name", rel_type)
        results = []
        for item in items:
            results.append(RetrievalResult(
                text=f"[{relation_label}] {name} 的{item}",
                score=entity_score,
                source=self.name,
                section=f"{name} / {relation_label}",
                extra={"kg_type": "relation", "relation": rel_type, "item": item},
            ))
        return results

    def _compare_result(self, name1: str, name2: str) -> Optional[RetrievalResult]:
        data = self.kg_client.compare_techs(name1, name2)
        if not data:
            return None
        lines = [f"[技术对比] {name1} vs {name2}"]
        for side, tech in (("A", data["tech1"]), ("B", data["tech2"])):
            prefix = "前者" if side == "A" else "后者"
            parts = [f"{prefix}（{tech['name']}）"]
            if tech.get("desc"):
                parts.append(f"描述：{tech['desc']}")
            for label, key in (("效率", "efficiency"), ("成本", "cost_level"),
                               ("成熟度", "maturity")):
                if tech.get(key):
                    parts.append(f"{label}：{tech[key]}")
            lines.append("；".join(parts))
        return RetrievalResult(
            text="\n".join(lines),
            score=1.0,
            source=self.name,
            section=f"{name1} vs {name2}",
            extra={"kg_type": "compare"},
        )

    # ==================== 统一接口 ====================

    def available(self) -> bool:
        return self.kg_client.is_connected()

    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]:
        if not self.available():
            return []

        analysis = self.classifier.analyze(query)
        entities = analysis["entities"]
        if not entities:
            return []

        results: List[RetrievalResult] = []
        names = [e["name"] for e in entities]
        top_score = entities[0]["score"] / 100.0

        # 1) 比较意图：优先处理两个实体
        if analysis["intent"] == "compare" and len(names) >= 2:
            compare = self._compare_result(names[0], names[1])
            if compare:
                results.append(compare)
                return results

        # 2) 关系意图：带关键词时展开关联实体
        if analysis["intent"] == "relation":
            rel_type = _detect_relation_type(query)
            if rel_type:
                results.extend(self._relation_results(names[0], rel_type, top_score))
                if results:
                    return results

        # 3) 默认：技术综合概览
        overview = self._overview_result(names[0], top_score)
        if overview:
            results.append(overview)

        return results[:top_k]
