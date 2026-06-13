#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 JSON 数据兜底模块

当 LLM 不可用或需要事实锚点时，从 data/new_energy.json 中检索结构化数据，
生成基于规则的模板回答。本模块同时向意图识别器提供实体名列表。
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from qa import config


class LocalDataStore:
    """本地新能源知识数据存储"""

    def __init__(self, data_path: Optional[Path] = None):
        self.data_path = data_path or config.DATA_PATH
        self.technologies: List[Dict[str, Any]] = []
        self.tech_index: Dict[str, Dict[str, Any]] = {}
        self.entity_names: List[str] = []
        self._load()

    def _load(self):
        """加载并索引 JSON 数据"""
        if not self.data_path.exists():
            raise FileNotFoundError(f"数据文件不存在: {self.data_path}")

        with open(self.data_path, "r", encoding="utf-8") as f:
            self.technologies = json.load(f)

        for tech in self.technologies:
            name = tech.get("name")
            if name:
                self.tech_index[name] = tech
                self.entity_names.append(name)

        print(f"[LocalDataStore] 已加载 {len(self.technologies)} 条技术数据")

    # ==================== 查询接口 ====================
    def get_tech(self, name: str) -> Optional[Dict[str, Any]]:
        """根据名称精确获取技术条目"""
        return self.tech_index.get(name)

    def get_all_names(self) -> List[str]:
        """获取所有技术名称"""
        return list(self.tech_index.keys())

    def count(self) -> int:
        """获取技术总数"""
        return len(self.technologies)

    def get_categories(self) -> List[str]:
        """获取所有不重复的技术类别名称"""
        categories = set()
        for tech in self.technologies:
            category = tech.get("category")
            if category:
                categories.add(category)
        return sorted(categories)

    # ==================== 比较类兜底 ====================
    def _fallback_compare(self, question: str, entities: Optional[List[Dict[str, Any]]] = None) -> str:
        """从已识别实体或问题文本中找出两个技术实体，并生成简单对比描述"""
        found = []

        # 优先使用意图识别器已抽取的实体
        if entities:
            for ent in entities:
                name = ent.get("name")
                if name and name in self.tech_index and name not in found:
                    found.append(name)
                if len(found) >= 2:
                    break

        # 若已识别实体不足两个，再从问题文本中匹配
        if len(found) < 2:
            names = sorted(self.tech_index.keys(), key=len, reverse=True)
            for name in names:
                if name in question and name not in found:
                    found.append(name)
                if len(found) >= 2:
                    break

        if len(found) < 2:
            return f"抱歉，比较问题需要明确两个技术实体，当前只识别到“{found[0] if found else '无'}”。请补充需要对比的技术名称。"

        t1, t2 = found[0], found[1]
        info1, info2 = self.tech_index[t1], self.tech_index[t2]
        adv1 = "、".join(info1.get("advantage", [])[:3]) or "暂无"
        adv2 = "、".join(info2.get("advantage", [])[:3]) or "暂无"
        dis1 = "、".join(info1.get("disadvantage", [])[:3]) or "暂无"
        dis2 = "、".join(info2.get("disadvantage", [])[:3]) or "暂无"

        return (
            f"【{t1}】\n"
            f"  优点：{adv1}\n"
            f"  缺点：{dis1}\n"
            f"  效率：{info1.get('efficiency', '暂无')}\n"
            f"【{t2}】\n"
            f"  优点：{adv2}\n"
            f"  缺点：{dis2}\n"
            f"  效率：{info2.get('efficiency', '暂无')}\n"
            "以上为两者的主要差异对比。"
        )

    # ==================== 列表类兜底 ====================
    # 列表问法中需要清洗的前缀/后缀/语气词
    LIST_NOISE_WORDS = [
        # 前缀
        "请列出", "列出", "请给我", "请告诉我", "告诉我", "给我", "全部", "所有",
        "有哪些", "有什么", "有啥", "包括哪些", "包含哪些",
        # 后缀
        "是什么", "包括哪些", "包含哪些", "有哪些", "有什么", "有啥",
        "技术", "相关技术", "的技术",
        # 标点
        "？", "?", "。", "，", ",",
    ]

    def _clean_list_topic(self, question: str) -> str:
        """从列表问法中清洗出核心主题词"""
        q = question.strip()
        # 循环移除噪声词，直到没有变化
        changed = True
        while changed:
            changed = False
            for noise in self.LIST_NOISE_WORDS:
                if q.startswith(noise):
                    q = q[len(noise):].strip()
                    changed = True
                    break
                if q.endswith(noise):
                    q = q[:-len(noise)].strip()
                    changed = True
                    break
        return q or question.strip("？?。，, ")

    def _fallback_list(self, question: str) -> str:
        """根据关键词列出相关技术名称"""
        keywords = ["光伏", "储能", "风电", "氢能", "核能", "新能源汽车", "汽车",
                    "电池", "发电", "太阳能"]
        matched = []
        for kw in keywords:
            if kw in question:
                matched.extend([
                    t["name"] for t in self.technologies
                    if kw in t.get("name", "") or kw in t.get("category", "")
                ])

        # 去重并保持顺序
        seen = set()
        result = []
        for name in matched:
            if name not in seen:
                seen.add(name)
                result.append(name)

        topic = self._clean_list_topic(question)
        if result:
            return f"与“{topic}”相关的技术包括：" + "、".join(result[:30]) + "。"

        # 无关键词时返回全部技术名称（限制数量）
        names = [t["name"] for t in self.technologies]
        return f"当前知识库共收录 {len(names)} 种新能源技术，例如：" + "、".join(names[:20]) + "等。"

    # ==================== 模板回答生成 ====================
    def generate_fallback_answer(
        self,
        question: str,
        intent: str,
        entity_name: Optional[str] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        根据意图和实体生成规则化兜底回答。
        """
        tech = self.tech_index.get(entity_name) if entity_name else None

        if intent == "chat":
            return "你好！我是新能源知识图谱问答助手，可以回答关于新能源技术、企业、材料、应用等方面的问题。"

        if intent == "unknown":
            return "抱歉，我没有理解您的问题。请尝试询问具体的新能源技术，例如：磷酸铁锂电池有哪些优点？"

        # 比较类问题：尝试获取两个实体并做简单对比
        if intent == "compare":
            return self._fallback_compare(question, entities=entities)

        # 聚合统计类问题：直接返回技术总数
        if intent == "aggregate" and ("技术" in question or "种" in question):
            return f"当前知识库共收录 {len(self.technologies)} 种新能源技术。"

        # 列表类问题：按关键词筛选或返回全部技术名称
        if intent == "list":
            return self._fallback_list(question)

        if not tech:
            return f"抱歉，我暂时没有找到与“{entity_name or question}”相关的本地数据。请检查名称是否准确，或稍后重试。"

        q = question.lower()

        # 属性类意图
        if "优点" in question or "优势" in question:
            items = tech.get("advantage", [])
            if items:
                return f"{tech['name']}的主要优点包括：" + "；".join(items) + "。"
            return f"{tech['name']}的优点信息暂未收录。"

        if "缺点" in question or "不足" in question:
            items = tech.get("disadvantage", [])
            if items:
                return f"{tech['name']}的主要缺点包括：" + "；".join(items) + "。"
            return f"{tech['name']}的缺点信息暂未收录。"

        if "原理" in question:
            return f"{tech['name']}的工作原理：{tech.get('principle', '暂未收录')}"

        if "效率" in question or "性能" in question or "参数" in question:
            return f"{tech['name']}的效率/性能参数：{tech.get('efficiency', '暂未收录')}"

        if "成本" in question or "价格" in question:
            return f"{tech['name']}的成本水平：{tech.get('cost_level', '暂未收录')}"

        if "市场份额" in question:
            return f"{tech['name']}的市场份额：{tech.get('market_share', '暂未收录')}"

        if "成熟度" in question:
            return f"{tech['name']}的成熟度：{tech.get('maturity', '暂未收录')}"

        # 关系类意图
        if "公司" in question or "企业" in question or "生产" in question or "厂商" in question:
            items = tech.get("companies", [])
            if items:
                return f"{tech['name']}的主要生产企业包括：" + "、".join(items) + "。"
            return f"{tech['name']}的生产企业信息暂未收录。"

        if "材料" in question:
            items = tech.get("materials", [])
            if items:
                return f"{tech['name']}使用的主要材料包括：" + "、".join(items) + "。"
            return f"{tech['name']}的材料信息暂未收录。"

        if "设备" in question:
            items = tech.get("equipments", [])
            if items:
                return f"{tech['name']}所需的主要设备包括：" + "、".join(items) + "。"
            return f"{tech['name']}的设备信息暂未收录。"

        if "应用" in question or "场景" in question:
            items = tech.get("applications", [])
            if items:
                return f"{tech['name']}的主要应用场景包括：" + "、".join(items) + "。"
            return f"{tech['name']}的应用场景信息暂未收录。"

        if "政策" in question or "支持" in question:
            items = tech.get("policies", [])
            if items:
                return f"{tech['name']}相关的支持政策包括：" + "、".join(items) + "。"
            return f"{tech['name']}的政策信息暂未收录。"

        if "竞争" in question or "替代" in question:
            items = tech.get("compete_technologies", [])
            if items:
                return f"{tech['name']}的竞争/替代技术包括：" + "、".join(items) + "。"
            return f"{tech['name']}的竞争技术信息暂未收录。"

        if "指标" in question:
            items = tech.get("indicators", [])
            if items:
                return f"{tech['name']}的关键技术指标包括：" + "、".join(items) + "。"
            return f"{tech['name']}的指标信息暂未收录。"

        # 默认返回描述
        return f"{tech['name']}：{tech.get('desc', '暂无描述')}"
