#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neo4j 知识图谱客户端

封装官方 neo4j 驱动，为问答引擎提供结构化的图谱检索能力。
所有查询均采用参数化 Cypher，防止注入。
"""

import traceback
from typing import Dict, Any, List, Optional

from qa import config


def _safe_list(value: Any) -> List[Any]:
    """统一把可能为 None 的查询结果转换为列表"""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None]
    return [value]


class _NullKGClient:
    """空 KG 客户端：用于纯 LLM 对比实验，始终返回不可用状态"""

    def is_available(self) -> bool:
        return False

    def is_connected(self) -> bool:
        return False

    def close(self):
        pass

    def get_status(self) -> Dict[str, Any]:
        return {
            "available": False,
            "connected": False,
            "uri": None,
            "user": None,
            "node_count": 0,
            "rel_count": 0,
            "error": "KG 已禁用",
        }

    def get_tech_overview(self, name: str) -> Optional[Dict[str, Any]]:
        return None

    def get_related(self, name: str, rel_type: str) -> Optional[Dict[str, Any]]:
        return None

    def get_techs_by_category(self, category: str) -> Optional[Dict[str, Any]]:
        return None

    def compare_techs(self, name1: str, name2: str) -> Optional[Dict[str, Any]]:
        return None

    def find_tech_by_name(self, name: str) -> Optional[str]:
        return None


class KGClient:
    """Neo4j 知识图谱客户端"""

    # 关系类型到中文语义与返回字段名的映射
    RELATION_MAP = {
        "produced_by": {"label": "Company", "field": "companies", "name": "生产企业"},
        "uses_material": {"label": "Material", "field": "materials", "name": "使用材料"},
        "requires_equipment": {"label": "Equipment", "field": "equipments", "name": "所需设备"},
        "applies_to": {"label": "Application", "field": "applications", "name": "应用场景"},
        "has_parameter": {"label": "Indicator", "field": "indicators", "name": "技术指标"},
        "supported_by": {"label": "Policy", "field": "policies", "name": "支持政策"},
        "competes_with": {"label": "Technology", "field": "compete_technologies", "name": "竞争技术"},
        "belongs_to": {"label": "Category", "field": "categories", "name": "所属类别"},
    }

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        初始化 Neo4j 连接。

        Args:
            uri: Neo4j Bolt URI，默认从 config.NEO4J_URI 读取
            user: 用户名，默认从 config.NEO4J_USER 读取
            password: 密码，默认从 config.NEO4J_PASSWORD 读取
        """
        self.uri = uri or config.NEO4J_URI
        self.user = user or config.NEO4J_USER
        self.password = password or config.NEO4J_PASSWORD
        self.driver = None

        if not self.password:
            # 未配置密码时认为知识图谱未启用
            self._available = False
            return

        try:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self._available = True
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            self._available = False
            self._last_error = str(e)

    # ==================== 连接管理 ====================
    def is_available(self) -> bool:
        """是否已配置并初始化驱动"""
        return self._available and self.driver is not None

    def is_connected(self) -> bool:
        """测试当前是否能与 Neo4j 建立有效会话"""
        if not self.is_available():
            return False
        try:
            with self.driver.session() as session:
                session.run("RETURN 1 AS ping")
            return True
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            self._last_error = str(e)
            return False

    def close(self):
        """关闭驱动连接"""
        if self.driver:
            try:
                self.driver.close()
            except Exception:
                pass
            self.driver = None

    # ==================== 状态统计 ====================
    def get_status(self) -> Dict[str, Any]:
        """返回知识图谱连接状态与规模统计"""
        status = {
            "available": self.is_available(),
            "connected": False,
            "uri": self.uri,
            "user": self.user,
            "node_count": 0,
            "rel_count": 0,
            "error": None,
        }
        if not self.is_available():
            status["error"] = getattr(self, "_last_error", "Neo4j 未配置或驱动初始化失败")
            return status

        try:
            with self.driver.session() as session:
                node_count = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
                rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
                status["connected"] = True
                status["node_count"] = node_count
                status["rel_count"] = rel_count
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            status["error"] = str(e)

        return status

    # ==================== 图谱查询 ====================
    def get_tech_overview(self, name: str) -> Optional[Dict[str, Any]]:
        """
        查询某个技术的综合信息及其所有关联实体。

        Returns:
            包含技术属性与关联列表的字典；若技术不存在返回 None。
        """
        if not self.is_connected():
            return None

        query = """
        MATCH (t:Technology {name: $name})
        OPTIONAL MATCH (t)-[:belongs_to]->(c:Category)
        OPTIONAL MATCH (t)-[:produced_by]->(co:Company)
        OPTIONAL MATCH (t)-[:uses_material]->(m:Material)
        OPTIONAL MATCH (t)-[:requires_equipment]->(e:Equipment)
        OPTIONAL MATCH (t)-[:applies_to]->(a:Application)
        OPTIONAL MATCH (t)-[:has_parameter]->(i:Indicator)
        OPTIONAL MATCH (t)-[:supported_by]->(p:Policy)
        OPTIONAL MATCH (t)-[:competes_with]->(ct:Technology)
        RETURN t.desc AS desc,
               t.principle AS principle,
               t.efficiency AS efficiency,
               t.cost_level AS cost_level,
               t.development_stage AS development_stage,
               t.market_share AS market_share,
               t.maturity AS maturity,
               collect(DISTINCT c.name) AS categories,
               collect(DISTINCT co.name) AS companies,
               collect(DISTINCT m.name) AS materials,
               collect(DISTINCT e.name) AS equipments,
               collect(DISTINCT a.name) AS applications,
               collect(DISTINCT i.name) AS indicators,
               collect(DISTINCT p.name) AS policies,
               collect(DISTINCT ct.name) AS compete_technologies
        """
        try:
            with self.driver.session() as session:
                record = session.run(query, name=name).single()
                if record is None or record["desc"] is None:
                    return None
                return {
                    "technology": {
                        "name": name,
                        "desc": record["desc"],
                        "principle": record["principle"],
                        "efficiency": record["efficiency"],
                        "cost_level": record["cost_level"],
                        "development_stage": record["development_stage"],
                        "market_share": record["market_share"],
                        "maturity": record["maturity"],
                    },
                    "categories": _safe_list(record["categories"]),
                    "companies": _safe_list(record["companies"]),
                    "materials": _safe_list(record["materials"]),
                    "equipments": _safe_list(record["equipments"]),
                    "applications": _safe_list(record["applications"]),
                    "indicators": _safe_list(record["indicators"]),
                    "policies": _safe_list(record["policies"]),
                    "compete_technologies": _safe_list(record["compete_technologies"]),
                }
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            return None

    def get_related(self, name: str, rel_type: str) -> Optional[Dict[str, Any]]:
        """
        查询某技术在特定关系下的关联实体。

        Args:
            name: 技术名称
            rel_type: 关系类型，如 produced_by、uses_material 等

        Returns:
            若关系类型未知或技术不存在，返回 None；否则返回包含关系信息的字典。
        """
        if not self.is_connected():
            return None

        meta = self.RELATION_MAP.get(rel_type)
        if meta is None:
            return None

        query = f"""
        MATCH (t:Technology {{name: $name}})-[:{rel_type}]->(n:{meta['label']})
        RETURN collect(DISTINCT n.name) AS items
        """
        try:
            with self.driver.session() as session:
                record = session.run(query, name=name).single()
                if record is None:
                    return None
                items = _safe_list(record["items"])
                return {
                    "relation": rel_type,
                    "relation_name": meta["name"],
                    "field": meta["field"],
                    meta["field"]: items,
                }
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            return None

    def get_techs_by_category(self, category: str) -> Optional[Dict[str, Any]]:
        """查询某个类别下有哪些技术"""
        if not self.is_connected():
            return None

        query = """
        MATCH (t:Technology)-[:belongs_to]->(c:Category {name: $category})
        RETURN collect(DISTINCT t.name) AS technologies
        """
        try:
            with self.driver.session() as session:
                record = session.run(query, category=category).single()
                if record is None:
                    return None
                technologies = _safe_list(record["technologies"])
                return {
                    "category": category,
                    "technologies": technologies,
                    "count": len(technologies),
                }
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            return None

    def compare_techs(self, name1: str, name2: str) -> Optional[Dict[str, Any]]:
        """对比两个技术的属性与关联信息"""
        if not self.is_connected():
            return None

        query = """
        MATCH (t1:Technology {name: $name1})
        MATCH (t2:Technology {name: $name2})
        OPTIONAL MATCH (t1)-[:belongs_to]->(c1:Category)
        OPTIONAL MATCH (t2)-[:belongs_to]->(c2:Category)
        OPTIONAL MATCH (t1)-[:produced_by]->(co1:Company)
        OPTIONAL MATCH (t2)-[:produced_by]->(co2:Company)
        OPTIONAL MATCH (t1)-[:uses_material]->(m1:Material)
        OPTIONAL MATCH (t2)-[:uses_material]->(m2:Material)
        OPTIONAL MATCH (t1)-[:applies_to]->(a1:Application)
        OPTIONAL MATCH (t2)-[:applies_to]->(a2:Application)
        RETURN t1.desc AS desc1, t1.efficiency AS efficiency1, t1.cost_level AS cost_level1,
               t1.development_stage AS development_stage1, t1.market_share AS market_share1, t1.maturity AS maturity1,
               t2.desc AS desc2, t2.efficiency AS efficiency2, t2.cost_level AS cost_level2,
               t2.development_stage AS development_stage2, t2.market_share AS market_share2, t2.maturity AS maturity2,
               collect(DISTINCT c1.name) AS categories1, collect(DISTINCT co1.name) AS companies1,
               collect(DISTINCT m1.name) AS materials1, collect(DISTINCT a1.name) AS applications1,
               collect(DISTINCT c2.name) AS categories2, collect(DISTINCT co2.name) AS companies2,
               collect(DISTINCT m2.name) AS materials2, collect(DISTINCT a2.name) AS applications2
        """
        try:
            with self.driver.session() as session:
                record = session.run(query, name1=name1, name2=name2).single()
                if record is None or record["desc1"] is None or record["desc2"] is None:
                    return None
                return {
                    "tech1": {
                        "name": name1,
                        "desc": record["desc1"],
                        "efficiency": record["efficiency1"],
                        "cost_level": record["cost_level1"],
                        "development_stage": record["development_stage1"],
                        "market_share": record["market_share1"],
                        "maturity": record["maturity1"],
                        "categories": _safe_list(record["categories1"]),
                        "companies": _safe_list(record["companies1"]),
                        "materials": _safe_list(record["materials1"]),
                        "applications": _safe_list(record["applications1"]),
                    },
                    "tech2": {
                        "name": name2,
                        "desc": record["desc2"],
                        "efficiency": record["efficiency2"],
                        "cost_level": record["cost_level2"],
                        "development_stage": record["development_stage2"],
                        "market_share": record["market_share2"],
                        "maturity": record["maturity2"],
                        "categories": _safe_list(record["categories2"]),
                        "companies": _safe_list(record["companies2"]),
                        "materials": _safe_list(record["materials2"]),
                        "applications": _safe_list(record["applications2"]),
                    },
                }
        except Exception as e:
            if config.DEBUG:
                traceback.print_exc()
            return None

    def find_tech_by_name(self, name: str) -> Optional[str]:
        """
        精确查找技术名是否存在，存在则返回标准名称，否则返回 None。
        后续可扩展为模糊匹配。
        """
        if not self.is_connected():
            return None

        query = "MATCH (t:Technology {name: $name}) RETURN t.name AS name"
        try:
            with self.driver.session() as session:
                record = session.run(query, name=name).single()
                return record["name"] if record else None
        except Exception:
            return None
