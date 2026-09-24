# -*- coding: utf-8 -*-
"""
KGClient（Neo4j 图谱客户端）单元测试

关键点：测试不连接真实 Neo4j，而是用 conftest 中的 FakeDriver/FakeSession
替换 client.driver，用 handler 函数“扮演”数据库对每条 Cypher 查询的响应。
这样可以在内存中验证：
- 查询是否被正确构造（参数化、关系类型映射）
- 结果是否被正确解析（_safe_list 对 None/[] 的统一处理）
"""

from conftest import FakeRecord, FakeResult, make_kg_client, ping_handler

from qa.kg_client import KGClient, _NullKGClient


class TestStatus:
    """连接状态与规模统计"""

    def test_unconfigured_client_disabled(self):
        # password 为空 → 客户端被禁用，不创建真实驱动
        client = KGClient(uri="bolt://x:7687", user="neo4j", password="")
        assert client.is_available() is False
        status = client.get_status()
        assert status["available"] is False
        assert status["connected"] is False

    def test_status_with_fake_driver(self):
        client = make_kg_client(ping_handler)
        status = client.get_status()
        assert status["connected"] is True
        assert status["node_count"] == 10
        assert status["rel_count"] == 20


class TestTechOverview:
    """get_tech_overview：技术综合信息查询"""

    def test_overview_structure(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            if "OPTIONAL MATCH" in query:
                return FakeResult([FakeRecord({
                    "desc": "描述",
                    "principle": "原理",
                    "efficiency": "95%",
                    "cost_level": "中低",
                    "development_stage": "成熟",
                    "market_share": "68%",
                    "maturity": "成熟",
                    "categories": ["锂离子电池"],
                    "companies": ["宁德时代"],
                    "materials": None,          # 模拟数据库返回 None
                    "equipments": [],           # 模拟数据库返回空列表
                    "applications": ["电动汽车"],
                    "indicators": None,
                    "policies": None,
                    "compete_technologies": ["三元锂电池"],
                })])
            raise AssertionError(f"unexpected query: {query}")

        client = make_kg_client(handler)
        result = client.get_tech_overview("磷酸铁锂电池")

        assert result is not None
        assert result["technology"]["name"] == "磷酸铁锂电池"
        assert result["companies"] == ["宁德时代"]
        # _safe_list 会把 None 和 [] 统一成 []
        assert result["materials"] == []
        assert result["equipments"] == []
        assert result["applications"] == ["电动汽车"]

    def test_overview_missing_tech_returns_none(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            return FakeResult([FakeRecord({"desc": None})])

        client = make_kg_client(handler)
        assert client.get_tech_overview("不存在的技术") is None


class TestRelated:
    """get_related：特定关系类型查询"""

    def test_known_relation(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            if "produced_by" in query:
                return FakeResult([FakeRecord({"items": ["宁德时代", "比亚迪"]})])
            raise AssertionError(f"unexpected query: {query}")

        client = make_kg_client(handler)
        result = client.get_related("磷酸铁锂电池", "produced_by")

        assert result["relation"] == "produced_by"
        assert result["relation_name"] == "生产企业"
        assert result["companies"] == ["宁德时代", "比亚迪"]

    def test_unknown_relation_returns_none(self):
        # 关系类型不在 RELATION_MAP 中 → 直接返回 None，不执行查询
        client = make_kg_client(ping_handler)
        assert client.get_related("磷酸铁锂电池", "no_such_relation") is None


class TestCompare:
    """compare_techs：双技术对比查询"""

    def test_compare_two_techs(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            return FakeResult([FakeRecord({
                "desc1": "A 描述", "efficiency1": "95%", "cost_level1": "低",
                "development_stage1": "成熟", "market_share1": "68%", "maturity1": "成熟",
                "desc2": "B 描述", "efficiency2": "92%", "cost_level2": "高",
                "development_stage2": "成熟", "market_share2": "29%", "maturity2": "成熟",
                "categories1": ["锂离子电池"], "companies1": ["宁德时代"],
                "materials1": ["磷酸铁锂"], "applications1": ["电动汽车"],
                "categories2": ["锂离子电池"], "companies2": ["宁德时代"],
                "materials2": ["三元材料"], "applications2": ["电动汽车"],
            })])
            # 注：raise 分支不可达，handler 只被 ping 与 compare 查询调用

        client = make_kg_client(handler)
        result = client.compare_techs("磷酸铁锂电池", "三元锂电池")

        assert result["tech1"]["name"] == "磷酸铁锂电池"
        assert result["tech2"]["name"] == "三元锂电池"
        assert result["tech1"]["efficiency"] == "95%"
        assert result["tech2"]["cost_level"] == "高"
        assert result["tech2"]["materials"] == ["三元材料"]


class TestFindTech:
    """find_tech_by_name：按名称查找技术"""

    def test_found(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            return FakeResult([FakeRecord({"name": params["name"]})])

        client = make_kg_client(handler)
        assert client.find_tech_by_name("磷酸铁锂电池") == "磷酸铁锂电池"

    def test_not_found(self):
        def handler(query, params):
            if "ping" in query:
                return FakeResult([FakeRecord({"1": 1})])
            return FakeResult([])

        client = make_kg_client(handler)
        assert client.find_tech_by_name("不存在") is None


class TestNullClient:
    """_NullKGClient：KG 禁用时的空实现"""

    def test_null_client_behavior(self):
        null = _NullKGClient()
        assert null.is_available() is False
        assert null.is_connected() is False
        assert null.get_tech_overview("X") is None
        assert null.get_related("X", "produced_by") is None
        assert null.compare_techs("A", "B") is None

        status = null.get_status()
        assert status["available"] is False
        assert status["connected"] is False
        assert status["error"] == "KG 已禁用"
