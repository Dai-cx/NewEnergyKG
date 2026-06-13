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
from typing import Dict, Any, Optional


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


class PromptBuilder:
    """Prompt 构建器"""

    @staticmethod
    def build_system_prompt(intent: str = "property") -> str:
        """根据意图构建 System Prompt"""
        extra = INTENT_EXTRA_PROMPTS.get(intent, "")
        return SYSTEM_PROMPT.strip() + "\n\n" + extra

    @staticmethod
    def build_user_prompt(
        question: str,
        intent: str = "property",
        entities: Optional[list] = None,
        kg_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        构建 User Prompt。

        Args:
            question: 用户原始问题。
            intent: 意图标签。
            entities: 抽取到的实体列表。
            kg_context: 第二阶段预留：知识图谱检索到的结构化上下文。
        """
        lines = []
        lines.append(f"用户问题：{question}")
        lines.append("")

        if entities:
            lines.append("识别到的实体：")
            for ent in entities:
                lines.append(f"- {ent['name']}（匹配类型：{ent['type']}，得分：{ent['score']}）")
            lines.append("")

        # 第二阶段将注入知识图谱上下文
        if kg_context:
            lines.append("知识图谱数据：")
            lines.append(json.dumps(kg_context, ensure_ascii=False, indent=2))
            lines.append("")

        if kg_context:
            lines.append('请注意：以上"知识图谱数据"是本次回答的主要事实依据，请优先使用。')
        lines.append("请根据以上信息直接回答用户问题，不要重复问题，保持简洁。")

        return "\n".join(lines)

