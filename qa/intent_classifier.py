#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
意图识别与实体抽取模块

本阶段采用轻量级规则实现（jieba 分词 + 关键词匹配 + 模糊匹配），
与 PDF 中描述的架构保持一致，同时保留后续升级为 BERT 模型的扩展点。
"""

import re
from typing import List, Dict, Any, Optional

try:
    import jieba
except ImportError:
    jieba = None

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = None
    process = None


# ==================== 意图关键词表 ====================
INTENT_KEYWORDS = {
    "property": ["是什么", "什么是", "什么叫", "定义", "概念", "原理", "工作原理", "优点", "优势", "缺点", "不足",
                 "效率", "性能", "参数", "成本", "价格", "市场份额", "成熟度", "描述"],
    "relation": ["材料", "企业", "公司", "厂商", "生产", "应用", "场景", "设备", "指标",
                 "参数", "政策", "支持", "关系", "相关"],
    "compare":  ["比较", "区别", "差异", "对比", "vs", "versus", "哪个好", "优劣"],
    "aggregate":["有多少", "数量", "总数", "几种", "几个", "统计"],
    "list":     ["列表", "全部", "列出", "列举", "罗列", "举出", "所有", "有什么", "有啥", "哪些"],
    "path":     ["路径", "链路", "怎么关联", "产业链", "上游", "下游"],
    "chat":     ["你好", "您好", "嗨", "hello", "hi", " hey", "在吗", "谢谢", "再见"],
}

# 同义词映射：口语/简称 -> 图谱中的标准技术名
# 只保留无歧义或最常用对应关系；有歧义的泛称不映射，交由模糊匹配处理。
SYNONYMS = {
    # 电池类
    "锂电": "磷酸铁锂电池",
    "锂电池": "磷酸铁锂电池",
    "铁锂电池": "磷酸铁锂电池",
    "磷酸铁锂": "磷酸铁锂电池",
    "三元锂": "三元锂电池",
    "三元电池": "三元锂电池",
    "钠电池": "钠离子电池",
    "钒电池": "全钒液流电池",
    "铁铬电池": "铁铬液流电池",
    "锌溴电池": "锌溴液流电池",
    # 光伏类
    "逆变器": "光伏逆变器",
    # 氢能类
    "燃料电池": "质子交换膜燃料电池",
    "氢燃料电池": "质子交换膜燃料电池",
    "氢燃料电池汽车": "氢燃料电池汽车",
    "电解水制氢": "碱性电解水制氢",
    # 新能源汽车类
    "纯电车": "纯电动汽车",
    "电动车": "纯电动汽车",
    "插混": "插电式混合动力汽车",
    "增程": "增程式电动车",
    # 核电类
    "小型堆": "小型模块化反应堆",
}


class IntentClassifier:
    """基于规则的意图分类器"""

    def __init__(self, entity_names: Optional[List[str]] = None):
        """
        Args:
            entity_names: 知识库中所有实体名称，用于实体匹配。
        """
        self.entity_names = sorted(set(entity_names or []), key=len, reverse=True)

    # ==================== 文本预处理 ====================
    @staticmethod
    def preprocess(text: str) -> str:
        """去除首尾空白，统一小写英文部分"""
        return text.strip()

    @staticmethod
    def segment(text: str) -> List[str]:
        """jieba 精确分词；若未安装 jieba 则按字符切分"""
        if jieba is None:
            return list(text)
        return list(jieba.cut(text, cut_all=False))

    @staticmethod
    def segment_search(text: str) -> List[str]:
        """jieba 搜索引擎模式分词"""
        if jieba is None:
            return list(text)
        return list(jieba.cut_for_search(text))

    # ==================== 意图分类 ====================
    def classify(self, question: str) -> str:
        """
        返回意图标签：property / relation / compare / aggregate / list / path / chat / unknown
        """
        q = self.preprocess(question)

        # 问候语优先检测（需在长度检测之前，否则“你好”会被判为过短）
        lower_q = q.lower()
        for greeting in INTENT_KEYWORDS["chat"]:
            if greeting.strip().lower() in lower_q:
                return "chat"

        # 过短文本
        if len(q) <= 2:
            return "unknown"

        # 关键词匹配（按意图优先级）
        # 优先级：compare > aggregate > path > property > relation > list
        # 说明：property 和 relation 的专属关键词（如“优点”“材料”）优先级高于 list 的泛化词，
        #      避免“有哪些优点”被误判为 list。
        priority = ["compare", "aggregate", "path", "property", "relation", "list"]
        for intent in priority:
            for kw in INTENT_KEYWORDS[intent]:
                if kw in q:
                    return intent

        return "unknown"

    # ==================== 实体抽取 ====================
    # 用于识别比较、对比等结构中的多个实体
    COMPARE_CONNECTORS = ["和", "与", "跟", "同", "以及", "相比", "比较", "对比", "区别", "差异", "vs", "versus"]
    # 比较片段中需要过滤的疑问/语气助词（按长度降序，优先匹配长词）
    QUESTION_PARTICLES = ["有什么区别", "有什么差异", "有什么不同", "哪个好", "怎么样",
                          "是什么", "有什么", "哪些", "怎么", "如何", "为什么", "什么",
                          "吗", "呢", "吧", "啊", "？", "?", "的"]

    def _extract_entities_from_text(self, text: str, seen_names: set) -> List[Dict[str, Any]]:
        """对单段文本抽取实体，返回候选列表（不排序、不截断）"""
        candidates = []
        q = self.preprocess(text)
        if not q:
            return candidates

        # 1. 整句精确匹配
        if q in self.entity_names and q not in seen_names:
            candidates.append({
                "name": q,
                "score": 100,
                "type": "exact_whole",
            })
            seen_names.add(q)

        # 准备分词 token
        tokens = set(self.segment(q) + self.segment_search(q))
        tokens = [t for t in tokens if len(t) >= 2]

        # 2. Token 精确匹配
        for token in tokens:
            for name in self.entity_names:
                if token == name and name not in seen_names:
                    score = 100 - max(0, 50 - len(token) * 2)
                    candidates.append({
                        "name": name,
                        "score": min(100, score),
                        "type": "exact_token",
                    })
                    seen_names.add(name)

            # 3. 同义词解析
            if token in SYNONYMS:
                mapped = SYNONYMS[token]
                if mapped in self.entity_names and mapped not in seen_names:
                    candidates.append({
                        "name": mapped,
                        "score": 95,
                        "type": "synonym",
                    })
                    seen_names.add(mapped)

        # 4. 模糊匹配（rapidfuzz）
        if process is not None and self.entity_names:
            results = process.extract(q, self.entity_names, scorer=fuzz.WRatio, limit=5)
            for name, score, _ in results:
                if score >= 70 and name not in seen_names:
                    candidates.append({
                        "name": name,
                        "score": int(score),
                        "type": "fuzzy",
                    })
                    seen_names.add(name)

        return candidates

    def _clean_segment(self, segment: str) -> str:
        """去除片段尾部的疑问词和语气助词"""
        seg = segment.strip()
        # 按长度降序移除尾部语气词
        particles = sorted(self.QUESTION_PARTICLES, key=len, reverse=True)
        changed = True
        while changed:
            changed = False
            for p in particles:
                if seg.endswith(p):
                    seg = seg[:-len(p)].strip()
                    changed = True
                    break
        return seg

    def _split_compare_segments(self, question: str) -> List[str]:
        """按比较连接词切分问题，并清理疑问助词，得到多个候选片段"""
        segments = [question]
        for conn in self.COMPARE_CONNECTORS:
            new_segments = []
            for seg in segments:
                parts = [p.strip() for p in seg.split(conn) if p.strip()]
                new_segments.extend(parts)
            segments = new_segments
        # 清理每个片段的疑问词，并过滤过短片段
        cleaned = []
        for seg in segments:
            seg = self._clean_segment(seg)
            if len(seg) >= 2:
                cleaned.append(seg)
        return cleaned

    def extract_entities(self, question: str) -> List[Dict[str, Any]]:
        """
        五级降级实体匹配策略：
        1. 比较结构切分后分别匹配（新增）
        2. 整句精确匹配
        3. Token 精确匹配
        4. 同义词解析
        5. 模糊匹配（rapidfuzz，阈值 70）
        """
        q = self.preprocess(question)
        candidates = []
        seen_names = set()

        # 1. 比较/对比结构：先按连接词切分，分别抽取，提高多实体识别率
        has_connector = any(conn in q for conn in self.COMPARE_CONNECTORS)
        if has_connector:
            segments = self._split_compare_segments(q)
            for seg in segments:
                seg_candidates = self._extract_entities_from_text(seg, seen_names)
                candidates.extend(seg_candidates)

        # 2-5. 对整句再做一次完整抽取，补充可能遗漏的实体
        whole_candidates = self._extract_entities_from_text(q, seen_names)
        candidates.extend(whole_candidates)

        # 排序：得分降序，名称长度降序
        candidates.sort(key=lambda x: (-x["score"], -len(x["name"])))

        # 返回 Top 3
        return candidates[:3]

    # ==================== 统一分析接口 ====================
    def analyze(self, question: str) -> Dict[str, Any]:
        """
        综合分析问题，返回意图与实体。

        Returns:
            {
                "question": str,
                "intent": str,
                "entities": List[Dict],
            }
        """
        q = self.preprocess(question)
        intent = self.classify(q)
        entities = self.extract_entities(q) if intent != "unknown" else []

        return {
            "question": q,
            "intent": intent,
            "entities": entities,
        }
