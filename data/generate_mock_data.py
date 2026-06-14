"""
知识图谱构建脚本（旧版 py2neo 实现，已弃用）
将清洗后的数据导入 Neo4j 图数据库

敏感配置（密码）建议通过环境变量注入，避免硬编码。
"""

import os
from py2neo import Graph, Node, Relationship, NodeMatcher
import json


class NewEnergyGraphBuilder:
    """
    新能源知识图谱构建器
    """

    def __init__(
        self,
        uri="bolt://localhost:7687",
        user="neo4j",
        password=None,
    ):
        """
        连接 Neo4j 数据库。

        密码优先从参数传入，其次从环境变量 NEO4J_PASSWORD 读取。
        """
        password = password or os.getenv("NEO4J_PASSWORD")
        if not password:
            raise ValueError(
                "Neo4j 密码未配置。请设置环境变量 NEO4J_PASSWORD，"
                "或在项目根目录 / graph/ / data/ 的 .env 文件中填写 NEO4J_PASSWORD=your-password"
            )
        try:
            self.graph = Graph(uri, auth=(user, password))
            self.matcher = NodeMatcher(self.graph)
            print("✅ 成功连接到 Neo4j 数据库")
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            print("请检查：1.Neo4j是否已启动 2.密码是否正确")
            raise

    def clear_graph(self):
        """
        清空图谱（谨慎使用！）
        """
        self.graph.run("MATCH (n) DETACH DELETE n")
        print("🗑️ 已清空图谱")

    def create_entity(self, name, entity_type, **properties):
        """
        创建实体节点
        如果节点已存在则返回已有节点
        """
        # 先查找是否已存在
        existing = self.matcher.match(entity_type, name=name).first()
        if existing:
            return existing

        # 创建新节点
        node = Node(entity_type, name=name, **properties)
        self.graph.create(node)
        return node

    def create_relation(self, from_node, to_node, rel_type, **properties):
        """
        创建两个节点之间的关系
        """
        rel = Relationship(from_node, rel_type, to_node, **properties)
        self.graph.create(rel)
        return rel

    def build_from_data(self, data_file='../data/cleaned_data.json'):
        """
        从JSON数据文件构建完整图谱
        """
        # 1. 加载数据
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        entities = data.get('entities', [])
        relations = data.get('relations', [])

        print(f"\n📊 数据加载完成:")
        print(f"   实体: {len(entities)} 个")
        print(f"   关系: {len(relations)} 条")

        # 2. 创建所有实体节点
        print("\n🔨 开始创建实体节点...")
        node_map = {}  # 用于存储已创建的节点，方便后续建关系

        for entity in entities:
            name = entity['name']
            entity_type = entity['type']
            desc = entity.get('desc', '')
            props = entity.get('properties', {})

            # 合并属性
            node_props = {
                'description': desc,
                **{k: v for k, v in props.items() if isinstance(v, (str, int, float))}
            }

            node = self.create_entity(name, entity_type, **node_props)
            node_map[name] = node
            print(f"  ✅ {entity_type}: {name}")

        # 3. 创建关系
        print("\n🔗 开始创建关系...")

        # 手动添加一些核心关系（因为自动提取的关系可能不够）
        manual_relations = [
            # 技术-材料关系
            ('锂离子电池', 'uses_material', '锂'),
            ('锂离子电池', 'uses_material', '钴'),
            ('磷酸铁锂电池', 'uses_material', '磷酸铁锂'),
            ('三元锂电池', 'uses_material', '镍'),
            ('固态电池', 'uses_material', '锂'),

            # 技术-企业关系
            ('刀片电池', 'produced_by', '比亚迪'),
            ('麒麟电池', 'produced_by', '宁德时代'),
            ('4680电池', 'produced_by', '特斯拉'),

            # 产品-车型关系
            ('刀片电池', 'equipped_in', '比亚迪汉'),

            # 技术层级关系
            ('磷酸铁锂电池', 'belongs_to', '锂离子电池'),
            ('三元锂电池', 'belongs_to', '锂离子电池'),
            ('固态电池', 'belongs_to', '锂离子电池'),
        ]

        for rel in manual_relations:
            from_name, rel_type, to_name = rel

            if from_name in node_map and to_name in node_map:
                self.create_relation(
                    node_map[from_name],
                    node_map[to_name],
                    rel_type
                )
                print(f"  ✅ ({from_name}) -[{rel_type}]-> ({to_name})")
            else:
                print(f"  ⚠️ 跳过: {from_name} 或 {to_name} 不存在")

        print("\n🎉 图谱构建完成！")

    def get_statistics(self):
        """
        获取图谱统计信息
        """
        # 统计各类实体数量
        result = self.graph.run("""
            MATCH (n) 
            RETURN labels(n)[0] as type, count(n) as count
            ORDER BY count DESC
        """).data()

        print("\n📈 图谱统计:")
        for item in result:
            print(f"   {item['type']}: {item['count']} 个")

        # 统计关系数量
        rel_count = self.graph.run("MATCH ()-[r]->() RETURN count(r) as count").data()[0]['count']
        print(f"   关系总数: {rel_count} 条")

    def query_demo(self):
        """
        演示查询
        """
        print("\n🔍 查询演示:")

        # 查询1：所有电池技术
        print("\n1. 所有电池技术:")
        results = self.graph.run("""
            MATCH (t:Technology) 
            RETURN t.name as name, t.description as desc 
            LIMIT 5
        """).data()
        for r in results:
            print(f"   • {r['name']}: {r['desc'][:50]}...")

        # 查询2：某技术使用哪些材料
        print("\n2. 锂离子电池使用哪些材料:")
        results = self.graph.run("""
            MATCH (t:Technology {name: '锂离子电池'})-[:uses_material]->(m:Material)
            RETURN m.name as material
        """).data()
        for r in results:
            print(f"   • {r['material']}")

        # 查询3：某企业生产哪些技术
        print("\n3. 比亚迪生产哪些技术:")
        results = self.graph.run("""
            MATCH (t:Technology)-[:produced_by]->(c:Company {name: '比亚迪'})
            RETURN t.name as tech
        """).data()
        for r in results:
            print(f"   • {r['tech']}")


def main():
    # 创建构建器实例（密码从环境变量 NEO4J_PASSWORD 读取）
    builder = NewEnergyGraphBuilder(
        uri="bolt://localhost:7687",
        user="neo4j",
    )

    # 清空旧数据（第一次运行可以打开，后续注释掉）
    # builder.clear_graph()

    # 构建图谱
    builder.build_from_data()

    # 查看统计
    builder.get_statistics()

    # 演示查询
    builder.query_demo()


if __name__ == '__main__':
    main()