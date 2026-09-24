#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新能源知识图谱构建脚本（官方 Neo4j Driver 版）
参考：刘焕勇 QASystemOnMedicalKG 项目
使用 neo4j 官方 Python 驱动替代 py2neo，更稳定、维护更好

安装依赖：pip install neo4j
"""

import os
import sys
import json
from pathlib import Path
from neo4j import GraphDatabase

# 确保脚本所在目录在 sys.path 中，支持从任意目录运行
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import config

class NewEnergyGraph:
    def __init__(self):
        # ==================== 数据路径配置 ====================
        # 优先从环境变量 GRAPH_DATA_PATH 读取，否则使用项目默认路径
        self.data_path = str(config.DATA_PATH)

        # ==================== Neo4j 连接配置 ====================
        # 从环境变量 / .env 文件读取，避免硬编码敏感信息
        self.uri = config.NEO4J_URI
        self.user, self.password = config.get_neo4j_auth()

        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

        # 测试连接
        try:
            with self.driver.session() as session:
                result = session.run("RETURN 'Neo4j连接成功' AS msg")
                print(result.single()["msg"])
        except Exception as e:
            print(f"Neo4j连接失败: {e}")
            print("请检查：1)Neo4j是否已启动  2)密码是否正确  3)端口是否被占用")
            raise

    # ==================== 关闭驱动 ====================
    def close(self):
        self.driver.close()

    # ==================== 实体列表 ====================
    def read_nodes(self):
        """读取JSON数据，整理各类实体和关系"""
        technologies = []      # 技术
        companies = []         # 企业
        materials = []         # 材料
        equipments = []        # 设备
        applications = []      # 应用场景
        policies = []          # 政策
        indicators = []        # 技术指标
        categories = []        # 技术类别（用于层级）

        # 关系列表
        rels_belongs_to = []       # 技术->类别
        rels_uses_material = []    # 技术->材料
        rels_produced_by = []      # 技术->企业
        rels_applies_to = []       # 技术->应用
        rels_has_parameter = []   # 技术->指标
        rels_supported_by = []     # 技术->政策
        rels_requires_equipment = [] # 技术->设备
        rels_competes_with = []    # 技术->竞争技术

        count = 0
        with open(self.data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for tech in data:
                count += 1
                tech_name = tech['name']
                technologies.append(tech_name)

                # 类别关系
                if 'category' in tech and tech['category']:
                    categories.append(tech['category'])
                    rels_belongs_to.append([tech_name, tech['category']])

                # 企业关系
                if 'companies' in tech:
                    for company in tech['companies']:
                        companies.append(company)
                        rels_produced_by.append([tech_name, company])

                # 材料关系
                if 'materials' in tech:
                    for material in tech['materials']:
                        materials.append(material)
                        rels_uses_material.append([tech_name, material])

                # 设备关系
                if 'equipments' in tech:
                    for equipment in tech['equipments']:
                        equipments.append(equipment)
                        rels_requires_equipment.append([tech_name, equipment])

                # 应用关系
                if 'applications' in tech:
                    for app in tech['applications']:
                        applications.append(app)
                        rels_applies_to.append([tech_name, app])

                # 指标关系
                if 'indicators' in tech:
                    for indicator in tech['indicators']:
                        indicators.append(indicator)
                        rels_has_parameter.append([tech_name, indicator])

                # 政策关系
                if 'policies' in tech:
                    for policy in tech['policies']:
                        policies.append(policy)
                        rels_supported_by.append([tech_name, policy])

                # 竞争关系
                if 'compete_technologies' in tech:
                    for compete in tech['compete_technologies']:
                        rels_competes_with.append([tech_name, compete])

        # 去重
        return (set(technologies), set(companies), set(materials), set(equipments), 
                set(applications), set(policies), set(indicators), set(categories), 
                rels_belongs_to, rels_uses_material, rels_produced_by, rels_applies_to, 
                rels_has_parameter, rels_supported_by, rels_requires_equipment, rels_competes_with)

    # ==================== 清空旧数据（可选） ====================
    def clear_database(self):
        """清空数据库所有节点和关系（慎用！）"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("数据库已清空")

    # ==================== 创建索引（加速查询） ====================
    def create_indexes(self):
        """为各实体类型的name属性创建索引，大幅提升关系创建速度"""
        labels = ['Technology', 'Company', 'Material', 'Equipment', 
                  'Application', 'Policy', 'Indicator', 'Category']
        with self.driver.session() as session:
            for label in labels:
                try:
                    # Neo4j 4.x/5.x 语法
                    session.run(f"CREATE INDEX {label.lower()}_name IF NOT EXISTS FOR (n:{label}) ON (n.name)")
                    print(f"索引创建/已存在: {label}.name")
                except Exception as e:
                    # 兼容旧版 Neo4j 3.x
                    try:
                        session.run(f"CREATE INDEX ON :{label}(name)")
                    except:
                        pass

    # ==================== 创建实体节点 ====================
    def _create_technology_node_tx(self, tx, tech):
        """事务函数：创建带属性的Technology节点（使用参数化查询防注入）"""
        query = """
        MERGE (n:Technology {name: $name})
        SET n.desc = $desc,
            n.principle = $principle,
            n.efficiency = $efficiency,
            n.cost_level = $cost_level,
            n.development_stage = $development_stage,
            n.market_share = $market_share,
            n.maturity = $maturity
        """
        tx.run(query, 
               name=tech['name'],
               desc=tech.get('desc', ''),
               principle=tech.get('principle', ''),
               efficiency=tech.get('efficiency', ''),
               cost_level=tech.get('cost_level', ''),
               development_stage=tech.get('development_stage', ''),
               market_share=tech.get('market_share', ''),
               maturity=tech.get('maturity', ''))

    def _create_simple_node_tx(self, tx, label, name):
        """事务函数：创建简单节点（仅name属性）"""
        query = f"MERGE (n:{label} {{name: $name}})"
        tx.run(query, name=name)

    def create_graphnodes(self):
        """创建所有实体节点"""
        techs, companies, materials, equipments, apps, policies, indicators, categories, \
        rels_belongs_to, rels_uses_material, rels_produced_by, rels_applies_to, \
        rels_has_parameter, rels_supported_by, rels_requires_equipment, rels_competes_with = self.read_nodes()

        # 读取完整技术信息
        with open(self.data_path, 'r', encoding='utf-8') as f:
            tech_infos = json.load(f)

        # 创建Technology节点（含属性）
        print("正在创建 Technology 节点...")
        with self.driver.session() as session:
            for tech in tech_infos:
                session.execute_write(self._create_technology_node_tx, tech)
        print(f"已创建/更新 {len(tech_infos)} 个 Technology 节点")

        # 创建其他简单节点
        entities = {
            'Company': companies,
            'Material': materials,
            'Equipment': equipments,
            'Application': apps,
            'Policy': policies,
            'Indicator': indicators,
            'Category': categories
        }

        for label, nodes in entities.items():
            print(f"正在创建 {label} 节点...")
            with self.driver.session() as session:
                for node_name in nodes:
                    session.execute_write(self._create_simple_node_tx, label, node_name)
            print(f"已创建/更新 {len(nodes)} 个 {label} 节点")

    # ==================== 创建关系边 ====================
    def _create_relationship_tx(self, tx, start_label, end_label, rel_type, rel_name, start_name, end_name):
        """事务函数：创建关系（参数化查询）"""
        query = """
        MATCH (a:%s {name: $start_name}), (b:%s {name: $end_name})
        MERGE (a)-[r:%s {name: $rel_name}]->(b)
        """ % (start_label, end_label, rel_type)
        tx.run(query, start_name=start_name, end_name=end_name, rel_name=rel_name)

    def create_relationships(self, start_label, end_label, rels, rel_type, rel_name):
        """批量创建关系"""
        count = 0
        total = len(rels)
        print(f"正在创建 {rel_type} 关系（共 {total} 条）...")

        with self.driver.session() as session:
            for rel in rels:
                try:
                    session.execute_write(
                        self._create_relationship_tx,
                        start_label, end_label, rel_type, rel_name,
                        rel[0], rel[1]
                    )
                    count += 1
                    if count % 50 == 0:
                        print(f"  进度: {count}/{total}")
                except Exception as e:
                    print(f"  创建关系失败: {rel[0]} -[{rel_type}]-> {rel[1]}, 错误: {e}")

        print(f"{rel_type} 关系创建完成，成功 {count}/{total} 条")
        return count

    def create_graphrels(self):
        """创建所有关系"""
        techs, companies, materials, equipments, apps, policies, indicators, categories, \
        rels_belongs_to, rels_uses_material, rels_produced_by, rels_applies_to, \
        rels_has_parameter, rels_supported_by, rels_requires_equipment, rels_competes_with = self.read_nodes()

        self.create_relationships("Technology", "Category", rels_belongs_to, "belongs_to", "属于")
        self.create_relationships("Technology", "Material", rels_uses_material, "uses_material", "使用材料")
        self.create_relationships("Technology", "Company", rels_produced_by, "produced_by", "由...生产")
        self.create_relationships("Technology", "Application", rels_applies_to, "applies_to", "应用于")
        self.create_relationships("Technology", "Indicator", rels_has_parameter, "has_parameter", "具有参数")
        self.create_relationships("Technology", "Policy", rels_supported_by, "supported_by", "受...政策支持")
        self.create_relationships("Technology", "Equipment", rels_requires_equipment, "requires_equipment", "需要设备")
        self.create_relationships("Technology", "Technology", rels_competes_with, "competes_with", "与...竞争")

    # ==================== 数据统计与导出 ====================
    def export_and_stats(self):
        """导出数据统计"""
        techs, companies, materials, equipments, apps, policies, indicators, categories, \
        rels_belongs_to, rels_uses_material, rels_produced_by, rels_applies_to, \
        rels_has_parameter, rels_supported_by, rels_requires_equipment, rels_competes_with = self.read_nodes()

        data = {
            "technologies": list(techs),
            "companies": list(companies),
            "materials": list(materials),
            "equipments": list(equipments),
            "applications": list(apps),
            "policies": list(policies),
            "indicators": list(indicators),
            "categories": list(categories),
            "relationships": {
                "belongs_to": rels_belongs_to,
                "uses_material": rels_uses_material,
                "produced_by": rels_produced_by,
                "applies_to": rels_applies_to,
                "has_parameter": rels_has_parameter,
                "supported_by": rels_supported_by,
                "requires_equipment": rels_requires_equipment,
                "competes_with": rels_competes_with
            }
        }

        output_path = Path(__file__).resolve().parent / 'graph_data_export.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"数据导出完成: {output_path}")

        # 打印统计信息
        print("\n========== 图谱规模统计 ==========")
        print(f"Technology 技术: {len(techs)}")
        print(f"Company 企业: {len(companies)}")
        print(f"Material 材料: {len(materials)}")
        print(f"Equipment 设备: {len(equipments)}")
        print(f"Application 应用: {len(apps)}")
        print(f"Policy 政策: {len(policies)}")
        print(f"Indicator 指标: {len(indicators)}")
        print(f"Category 类别: {len(categories)}")
        print(f"--------------------------------")
        total_nodes = len(techs)+len(companies)+len(materials)+len(equipments)+len(apps)+len(policies)+len(indicators)+len(categories)
        total_rels = len(rels_belongs_to)+len(rels_uses_material)+len(rels_produced_by)+len(rels_applies_to)+len(rels_has_parameter)+len(rels_supported_by)+len(rels_requires_equipment)+len(rels_competes_with)
        print(f"总节点数: {total_nodes}")
        print(f"总关系数: {total_rels}")
        print("==================================\n")

    # ==================== 从Neo4j中统计（验证入库结果） ====================
    def verify_in_neo4j(self):
        """从Neo4j数据库中查询实际统计"""
        print("========== Neo4j 实际入库统计 ==========")
        with self.driver.session() as session:
            # 节点统计
            labels = ['Technology', 'Company', 'Material', 'Equipment', 
                      'Application', 'Policy', 'Indicator', 'Category']
            for label in labels:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                cnt = result.single()["cnt"]
                print(f"{label}: {cnt} 个节点")

            # 总节点
            result = session.run("MATCH (n) RETURN count(n) AS cnt")
            print(f"总节点数: {result.single()['cnt']}")

            # 总关系
            result = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt")
            print(f"总关系数: {result.single()['cnt']}")

            # 各类关系
            result = session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt ORDER BY cnt DESC")
            print("\n关系类型分布:")
            for record in result:
                print(f"  {record['t']}: {record['cnt']} 条")

            # 孤立节点
            result = session.run("MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt")
            isolated = result.single()["cnt"]
            print(f"\n孤立节点数: {isolated}")
        print("=======================================\n")


if __name__ == '__main__':
    handler = NewEnergyGraph()

    # 如需清空旧数据，取消下面这行的注释
    # handler.clear_database()

    print("步骤1: 创建索引...")
    handler.create_indexes()

    print("步骤2: 创建实体节点...")
    handler.create_graphnodes()

    print("步骤3: 创建关系边...")
    handler.create_graphrels()

    print("步骤4: 导出数据...")
    handler.export_and_stats()

    print("步骤5: 验证Neo4j入库结果...")
    handler.verify_in_neo4j()

    handler.close()
    print("知识图谱构建完成！")
