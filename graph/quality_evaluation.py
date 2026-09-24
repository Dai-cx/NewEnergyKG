"""
知识图谱质量评估脚本（官方 neo4j 驱动版）
"""

import sys
from pathlib import Path
from neo4j import GraphDatabase

# 确保脚本所在目录在 sys.path 中，支持从任意目录运行
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import config


class GraphQualityEvaluator:

    def __init__(self, uri=None, user=None, password=None):
        # 优先使用传入参数，其次从环境变量 / .env 读取
        self.uri = uri or config.NEO4J_URI
        self.user = user or config.NEO4J_USER
        self.password = password or config.NEO4J_PASSWORD
        if not self.password:
            raise ValueError(
                "Neo4j 密码未配置。请设置环境变量 NEO4J_PASSWORD，"
                "或在 graph/.env 文件中填写 NEO4J_PASSWORD=your-password"
            )
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self):
        self.driver.close()

    def evaluate_accuracy(self):
        print("\n📏 准确性评估:")

        with self.driver.session() as session:
            # 空名称节点
            empty_nodes = session.run("""
                MATCH (n) 
                WHERE n.name IS NULL OR n.name = ''
                RETURN count(n) as count
            """).single()['count']

            print(f"   空名称节点: {empty_nodes} 个 {'✅ 合格' if empty_nodes == 0 else '❌ 需清理'}")

            # 孤立节点
            isolated = session.run("""
                MATCH (n) 
                WHERE NOT (n)--()
                RETURN count(n) as count
            """).single()['count']

            print(f"   孤立节点: {isolated} 个 {'✅ 正常' if isolated < 5 else '⚠️ 较多孤立节点'}")

    def evaluate_completeness(self):
        print("\n📊 完整性评估:")

        with self.driver.session() as session:
            result = session.run("""
                MATCH (n) 
                RETURN labels(n)[0] as type,
                       count(n) as total,
                       count(n.description) as has_desc
            """)
            for record in result:
                total = record['total']
                has_desc = record['has_desc']
                desc_rate = (has_desc / total * 100) if total > 0 else 0
                print(f"   {record['type']}: 共{total}个, 有描述{has_desc}个, 描述完整率{desc_rate:.1f}%")

    def evaluate_consistency(self):
        print("\n🔄 一致性评估:")

        with self.driver.session() as session:
            duplicates = session.run("""
                MATCH (n)
                WITH n.name as name, collect(DISTINCT labels(n)) as types
                WHERE size(types) > 1
                RETURN name, size(types) as type_count
            """)

            dup_list = list(duplicates)
            if dup_list:
                print(f"   ⚠️ 发现 {len(dup_list)} 个同名不同类型实体:")
                for d in dup_list:
                    print(f"      - {d['name']} ({d['type_count']} 种类型)")
            else:
                print("   ✅ 未发现同名冲突")

            # 关系类型分布
            print(f"\n   关系类型分布:")
            rel_types = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count DESC
            """)
            for record in rel_types:
                print(f"      - {record['rel_type']}: {record['count']} 条")

    def generate_report(self):
        print("=" * 50)
        print("新能源知识图谱质量评估报告")
        print("=" * 50)

        with self.driver.session() as session:
            total_nodes = session.run("MATCH (n) RETURN count(n) as count").single()['count']
            total_rels = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()['count']

            print(f"\n📌 图谱规模:")
            print(f"   实体总数: {total_nodes}")
            print(f"   关系总数: {total_rels}")
            if total_nodes > 0:
                print(f"   平均度数: {total_rels * 2 / total_nodes:.2f}")

        self.evaluate_accuracy()
        self.evaluate_completeness()
        self.evaluate_consistency()

        print("\n" + "=" * 50)
        print("评估完成！")
        print("=" * 50)


if __name__ == '__main__':
    evaluator = GraphQualityEvaluator()  # 密码从环境变量 NEO4J_PASSWORD 或 graph/.env 读取
    evaluator.generate_report()
    evaluator.close()