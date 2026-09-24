# -*- coding: utf-8 -*-
"""
pytest 共享夹具与 Neo4j 伪客户端（FakeDriver / FakeSession / FakeResult / FakeRecord）

为什么需要 Fake 系列？
    KGClient 依赖真实 Neo4j 服务，单元测试不应该启动数据库。
    我们实现一组与 neo4j 驱动接口“长得一样”的假对象，替换 client.driver，
    从而在内存中验证 Cypher 查询的参数化调用与结果解析逻辑。
"""

import json

import pytest

# 与 data/new_energy.json 同构的 3 条样例技术数据（测试专用，避免依赖真实数据文件）
SAMPLE_TECHNOLOGIES = [
    {
        "name": "磷酸铁锂电池",
        "desc": "使用磷酸铁锂作为正极材料的锂离子电池，安全性高、循环寿命长。",
        "principle": "锂离子在正负极之间嵌入和脱嵌实现充放电。",
        "advantage": ["安全性高", "循环寿命长"],
        "disadvantage": ["能量密度较低"],
        "efficiency": "充放电效率约95%",
        "cost_level": "中低",
        "development_stage": "商业化成熟",
        "market_share": "2024年装机量占比约68%",
        "maturity": "成熟",
        "category": "锂离子电池",
        "companies": ["宁德时代", "比亚迪"],
        "materials": ["磷酸铁锂", "石墨"],
        "equipments": ["涂布机"],
        "applications": ["电动汽车", "储能电站"],
        "indicators": ["能量密度", "循环寿命"],
        "policies": ["新能源汽车产业规划"],
        "compete_technologies": ["三元锂电池"],
    },
    {
        "name": "三元锂电池",
        "desc": "使用镍钴锰三元材料作为正极的锂离子电池，能量密度高。",
        "principle": "锂离子在正负极之间往返嵌入脱嵌。",
        "advantage": ["能量密度高", "低温性能好"],
        "disadvantage": ["成本较高", "热稳定性一般"],
        "efficiency": "充放电效率约92%",
        "cost_level": "中高",
        "development_stage": "商业化成熟",
        "market_share": "2024年装机量占比约29%",
        "maturity": "成熟",
        "category": "锂离子电池",
        "companies": ["宁德时代", "中创新航"],
        "materials": ["三元材料", "石墨"],
        "equipments": ["涂布机"],
        "applications": ["电动汽车"],
        "indicators": ["能量密度"],
        "policies": [],
        "compete_technologies": ["磷酸铁锂电池"],
    },
    {
        "name": "全钒液流电池",
        "desc": "以钒离子价态变化实现储能的长时储能电池。",
        "principle": "通过钒离子在电解液中的价态变化实现充放电。",
        "advantage": ["循环寿命极长", "安全性高"],
        "disadvantage": ["成本高", "能量密度低"],
        "efficiency": "充放电效率约75%",
        "cost_level": "高",
        "development_stage": "示范应用",
        "market_share": "长时储能领域占比稳步提升",
        "maturity": "发展中",
        "category": "液流电池",
        "companies": ["大连融科"],
        "materials": ["钒电解液"],
        "equipments": ["电堆"],
        "applications": ["电网侧储能"],
        "indicators": ["循环寿命", "能量效率"],
        "policies": ["新型储能指导意见"],
        "compete_technologies": ["锂离子电池"],
    },
]


@pytest.fixture
def sample_technologies():
    """返回 3 条样例技术数据（深拷贝，避免测试间相互污染）"""
    return json.loads(json.dumps(SAMPLE_TECHNOLOGIES))


@pytest.fixture
def sample_data_path(tmp_path, sample_technologies):
    """把样例数据写入临时 JSON 文件并返回其路径"""
    p = tmp_path / "sample_data.json"
    p.write_text(json.dumps(sample_technologies, ensure_ascii=False), encoding="utf-8")
    return p


# ==================== Neo4j 伪客户端 ====================


class FakeRecord:
    """模拟 neo4j 的 Record：支持 record[key] 与 record.get(key, default)"""

    def __init__(self, data=None):
        self._data = data or {}

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __repr__(self):
        return f"FakeRecord({self._data})"


class FakeResult:
    """模拟 neo4j 的 Result：目前只用到 .single()"""

    def __init__(self, records=None):
        self._records = records or []

    def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    """模拟 neo4j 的 Session：上下文管理器 + run(query, **params)"""

    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def run(self, query, **params):
        return self._handler(query, params)


class FakeDriver:
    """模拟 neo4j 的 Driver：只提供 .session() 与 .close()"""

    def __init__(self, handler):
        self._handler = handler

    def session(self):
        return FakeSession(self._handler)

    def close(self):
        pass


def make_kg_client(handler, password="test-password", uri="bolt://fake:7687"):
    """
    构造一个已注入 FakeDriver 的 KGClient，避免连接真实 Neo4j。

    Args:
        handler: 形如 (query, params) -> FakeResult 的函数，
                 测试中用它来“扮演”Neo4j 对每条查询的响应。
    """
    from qa.kg_client import KGClient

    client = KGClient(uri=uri, user="neo4j", password=password)
    client.driver = FakeDriver(handler)
    client._available = True
    return client


def ping_handler(query, params):
    """
    通用 handler：先响应健康检查 ping，再按查询关键字分发。

    若测试中没有覆盖某条查询，会抛出 AssertionError，提醒补测试。
    """
    if "ping" in query:
        return FakeResult([FakeRecord({"1": 1})])
    if "count(n)" in query:
        return FakeResult([FakeRecord({"cnt": 10})])
    if "count(r)" in query:
        return FakeResult([FakeRecord({"cnt": 20})])
    raise AssertionError(f"测试未覆盖的查询: {query}")
