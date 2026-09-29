#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt 构建模块

采用 PDF 中描述的三段式 Prompt 结构：
1. System Prompt：角色设定 + 领域背景 + 回答要求
2. User Prompt：用户问题 + 可选上下文（预留 kg_context 供第二阶段使用）
3. 任务指令：明确输出格式与约束
"""

import json
from typing import Dict, Any, List, Optional

from qa.intent_classifier import INTENT_LABELS


# ==================== System Prompt ====================
SYSTEM_PROMPT = """你是一个新能源知识图谱问答助手，负责回答用户关于新能源领域的问题。

你的知识覆盖以下领域：光伏、储能、风电、氢能、核能、新能源汽车等。

回答要求：
1. 用简洁、自然的中文回答用户问题。
2. 如果提供了"知识图谱数据"，请优先依据其中的结构化事实进行回答，确保信息准确。
3. 如果知识图谱数据不足以回答问题，请明确告知用户哪些信息暂不可得，不要编造不确定的内容。
4. 如果问题涉及具体技术，请优先使用专业术语，并给出准确的技术描述。
5. 如果是列表类问题（如"有哪些公司"），请使用条目清晰的列表形式。
6. 如果问题涉及比较（如"有什么区别"），请使用对比方式回答。
7. 回答控制在 3-5 句话以内，避免冗长。
"""

# 针对不同意图的微调提示
INTENT_EXTRA_PROMPTS = {
    "property": "请重点回答该技术的基本属性、定义、原理、优缺点、效率或性能参数。",
    "relation": "请重点回答该技术相关的企业、材料、设备、应用场景、政策或竞争技术。",
    "compare": "请从多个维度对比两种或多种技术，使用表格或分点形式更清晰。",
    "aggregate": "请给出统计性的回答，如果涉及数量请直接说明。",
    "list": "请以列表形式列出相关内容，确保条目清晰。",
    "path": "请描述产业链或技术路径的上下游关系。",
    "chat": "这是闲聊场景，请友好、简短地回应。",
    "unknown": "用户意图不明确，请礼貌地请用户补充问题细节。",
}


# ==================== 查询理解 Prompt（Week2 D1）====================
# 意图标签 → 一句话说明，拼进 LLM 意图路由的 System Prompt。
# 顺序与 INTENT_LABELS 保持一致，方便对照理解。
INTENT_LABEL_DESCRIPTIONS = {
    "property": "询问某个技术/产品的属性：定义、原理、优缺点、效率、参数、成本等",
    "relation": "询问与某个技术相关的事物：企业、材料、设备、应用、政策、竞争技术等",
    "compare": "比较两种或多种技术的区别、差异、优劣",
    "aggregate": "统计类：数量、总数、种类数",
    "list": "列举类：列出、罗列某类技术/成员",
    "path": "询问产业链、上下游、技术路径等关联链路",
    "chat": "问候、闲聊、致谢等非知识性问题",
    "unknown": "无法归入以上任何类别的意图",
}

# 查询改写：把"它/这个/哪个"这类指代问句，改写成脱离上下文也能独立理解的完整问句
QUERY_REWRITER_SYSTEM_PROMPT = """你是多轮对话中的「查询改写器」。

任务：把用户最新问题改写为一条不依赖上下文也能独立理解的完整问句。

规则：
1. 只做指代消解与必要的信息补全（例如把"它/这个/哪个/两者"补成具体的实体名或限定语），不改动用户本意；
2. 不回答该问题，不添加原文没有的新信息，不评价；
3. 用户用什么语言提问，就输出什么语言；
4. 只输出改写后的问句本身：单行、不加引号、不加"改写后："之类前缀、不输出任何解释。"""

# LLM 意图路由：System Prompt 在 build_intent_router_system_prompt() 中按
# INTENT_LABEL_DESCRIPTIONS 动态拼出，避免标签清单散落多处。

# 检索失败改写器（Week2 D6）：上一轮检索零命中时，生成一个"更易检索到资料"
# 的替代问法（换说法/补全限定语/必要翻译），供引擎重试检索。
QUERY_RETRY_SYSTEM_PROMPT = """你是「检索失败改写器」。

背景：系统已经用上一个问句检索过知识库与文档，但没有找到任何相关资料。
请把问题改写成一个更容易命中资料的问句，用于再次检索。

改写方向（至少做其中一项）：
1. 换一种更常见/更口语的说法表达同一个问题；
2. 若问题可能指代某实体但名字不完整，补全到知识库常见的完整名称；
3. 若资料库为英文而问题是中文，附上对应的英文检索问句；
4. 去掉可能干扰检索的修辞，保留核心实体与核心疑问。

规则：
1. 不回答问题，不编造资料；
2. 只输出一条改写后的问句：单行、不加引号、不加前缀；
3. 若你认为原问题已经无可优化，直接原样输出原问题。"""


class PromptBuilder:
    """Prompt 构建器"""

    @staticmethod
    def build_system_prompt(intent: str = "property") -> str:
        """根据意图构建 System Prompt"""
        extra = INTENT_EXTRA_PROMPTS.get(intent, "")
        return SYSTEM_PROMPT.strip() + "\n\n" + extra

    @staticmethod
    def format_kg_context(kg_context: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        把图谱上下文渲染成注入 Prompt 的那段文本（JSON 缩进格式）。

        单独抽出来的原因：**评估必须看到与 LLM 完全相同的文本**。
        `qa/eval/gen_run.py` 采集 RAGAS 的 `contexts` 时若只取文档检索结果、
        不含图谱事实，则任何来自图谱的陈述都会被判为"无上下文支持"，
        faithfulness 会被系统性低估（实测 0.40，见 qa/eval/ragas_live_report.md）。
        因此渲染只此一处，Prompt 与评估共用。

        Returns:
            渲染后的文本；无图谱上下文时返回 None。
        """
        if not kg_context:
            return None
        return json.dumps(kg_context, ensure_ascii=False, indent=2)

    @staticmethod
    def build_user_prompt(
        question: str,
        intent: str = "property",
        entities: Optional[list] = None,
        kg_context: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        references: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        构建 User Prompt。

        Args:
            question: 用户原始问题。
            intent: 意图标签。
            entities: 抽取到的实体列表。
            kg_context: 知识图谱检索到的结构化上下文。
            history: 当前会话历史消息列表，用于多轮对话上下文理解。
            references: 混合检索到的参考资料列表（文档/图谱文本片段），
                        每项含 text/source/section；构建时自动编号 [1][2]…，
                        供 LLM 生成带出处引用的回答。
        """
        lines = []

        # 注入历史对话，帮助 LLM 进行指代消解和上下文理解
        if history:
            lines.append("以下是当前会话的历史对话，请结合上下文理解后续问题：")
            for msg in history:
                role_label = "用户" if msg.get("role") == "user" else "助手"
                lines.append(f"{role_label}：{msg.get('content', '')}")
            lines.append("")

        lines.append(f"当前问题：{question}")
        lines.append("")

        if entities:
            lines.append("识别到的实体：")
            for ent in entities:
                lines.append(f"- {ent['name']}（匹配类型：{ent['type']}，得分：{ent['score']}）")
            lines.append("")

        # 第二阶段将注入知识图谱上下文（渲染统一走 format_kg_context）
        kg_text = PromptBuilder.format_kg_context(kg_context)
        if kg_text:
            lines.append("知识图谱数据：")
            lines.append(kg_text)
            lines.append("")

        # 混合检索的参考资料（自动编号，供引用）
        if references:
            lines.append("参考资料（若引用其中内容，请在对应句子后标注编号，如[1]）：")
            for i, ref in enumerate(references, start=1):
                head = f"[{i}]"
                if ref.get("source"):
                    head += f" 来源：{ref['source']}"
                if ref.get("section"):
                    head += f"（章节：{ref['section']}）"
                lines.append(head)
                lines.append(ref.get("text", ""))
            lines.append("")

        if kg_context:
            lines.append('请注意：以上"知识图谱数据"是本次回答的主要事实依据，请优先使用。')
        lines.append("请根据以上信息直接回答用户问题，不要重复问题，保持简洁。")

        return "\n".join(lines)

    # ==================== 查询理解 Prompt（Week2 D1）====================

    @staticmethod
    def build_rewrite_prompt(
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        构建查询改写的 User Prompt：把会话历史 + 最新问题喂给 LLM，
        让它输出一条"自包含"的问句（完成多轮指代消解）。

        Args:
            question: 用户最新问题（可能含"它/这个"等指代）。
            history: 当前会话历史 [{role, content}, ...]，通常来自会话记忆。
        """
        lines = []
        lines.append("以下是当前会话中最近的历史对话：")
        lines.append("")
        for msg in history or []:
            role_label = "用户" if msg.get("role") == "user" else "助手"
            lines.append(f"{role_label}：{msg.get('content', '')}")
        lines.append("")
        lines.append(f"用户最新问题：{question}")
        lines.append("")
        lines.append("请只输出改写后的完整问句（单行，无引号无前缀）：")
        return "\n".join(lines)

    @staticmethod
    def build_intent_router_system_prompt() -> str:
        """
        构建 LLM 意图路由的 System Prompt。

        把标签与一句话定义动态拼进提示词，并强制输出 JSON：
            {"intent": "property"}
        供 llm_router 解析；解析失败会自动降级到规则意图分类。
        """
        lines = [
            "你是对话系统的意图分类器。请判断用户问题属于哪一种意图，只输出一个标签。",
            "",
            "可选的意图标签与含义：",
        ]
        for label in INTENT_LABELS:
            desc = INTENT_LABEL_DESCRIPTIONS.get(label, "")
            lines.append(f"- {label}: {desc}")
        lines.append("")
        lines.append('只输出一个 JSON 对象：{"intent": "<标签>"}，不要输出其他任何内容。')
        return "\n".join(lines)

    @staticmethod
    def build_intent_router_prompt(question: str) -> str:
        """
        构建 LLM 意图路由的 User Prompt：当前就是问题本身。
        独立成方法是为了日后（如需要示例、few-shot）可平滑扩展。
        """
        return question.strip()

    @staticmethod
    def build_retry_prompt(
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        round_no: int = 1,
    ) -> str:
        """
        构建"检索失败改写"的 User Prompt。

        Args:
            question: 上一轮用于检索但零命中的问句。
            history: 会话历史（可选，帮助消解指代）。
            round_no: 当前是第几轮重试（从 1 开始），让模型知道这是重试。
        """
        lines = []
        if history:
            lines.append("以下是当前会话最近的历史对话：")
            for msg in history:
                role_label = "用户" if msg.get("role") == "user" else "助手"
                lines.append(f"{role_label}：{msg.get('content', '')}")
            lines.append("")
        lines.append(f"这是第 {round_no} 次重试。上一轮零命中的问句：{question}")
        lines.append("")
        lines.append("请只输出改写后的检索问句（单行，无引号无前缀）：")
        return "\n".join(lines)

