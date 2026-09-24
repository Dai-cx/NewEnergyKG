#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 查询理解路由（Week2 D1）：查询改写 + LLM 意图路由

解决的问题：
1. 多轮对话里的指代问句（"它的优点呢？"）脱离上下文无法检索 —— 先让 LLM
   基于会话历史做「查询改写」，把问句补成自包含的完整问题，再交给检索层。
2. 规则意图分类靠关键词表，泛化差 —— 升级为「LLM 意图路由」做主分类，
   原规则版降级为兜底（无 Key / LLM 失败 / 输出不可解析 / 评测对比时使用）。

流水线（Analyze → AnswerEngine 消费）：

    用户问题 + 会话历史
           │
           ▼
    ┌─ 查询改写（LLM）────────┐  无历史 / 无指代 / LLM 不可用
    │  输出自包含问句 effective  ─▶ 直接用原问题
    └───────────┬───────────────┘
                ▼
    ┌─ 意图路由（LLM）────────┐  解析失败 / LLM 不可用
    │  输出 8 类标签之一        ─▶ 规则意图分类兜底
    └───────────┬───────────────┘
                ▼
      实体抽取（始终用规则：实体表来自知识库，
      保证实体名与图谱中的精确名称一致，提高 Cypher 命中率）

整个模块遵循"能降级就降级、绝不因理解失败中断问答"的设计：
任何一步 LLM 出错，只影响该步质量，不影响整条链路可用性。
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from qa import config
from qa.intent_classifier import INTENT_LABELS, IntentClassifier
from qa.llm_client import LLMError
from qa.prompt_builder import (
    QUERY_RETRY_SYSTEM_PROMPT,
    QUERY_REWRITER_SYSTEM_PROMPT,
    PromptBuilder,
)

# 指代词/限定语：命中其一且会话有历史 → 值得做一次查询改写
COREF_TOKENS = (
    "它", "这个", "这些", "这种", "那个", "那些", "两者", "二者", "俩",
    "哪个", "哪种", "哪类", "其", "上述", "上文", "刚才", "之前", "该技术", "该电池",
)

# 意图输出别名归一化：LLM 可能用中文或英文口语表述，统一映射到 INTENT_LABELS
INTENT_ALIASES = {
    "属性": "property", "特性": "property", "特征": "property", "attribute": "property",
    "关系": "relation", "关联": "relation", "related": "relation", "relationship": "relation",
    "对比": "compare", "比较": "compare", "区别": "compare", "comparison": "compare",
    "统计": "aggregate", "数量": "aggregate", "总数": "aggregate", "aggregate": "aggregate",
    "列举": "list", "列出": "list", "列表": "list", "list": "list", "listing": "list",
    "链路": "path", "路径": "path", "产业链": "path", "path": "path", "chain": "path",
    "闲聊": "chat", "寒暄": "chat", "问候": "chat", "chat": "chat", "smalltalk": "chat",
    "其他": "unknown", "其它": "unknown", "无关": "unknown", "无法判断": "unknown",
    "unknown": "unknown", "other": "unknown", "none": "unknown",
}


def normalize_intent(raw: Any) -> Optional[str]:
    """
    把 LLM 返回的意图文本归一化为标准标签。

    Args:
        raw: LLM 输出中 intent 字段的原始值。

    Returns:
        标准意图标签；无法识别时返回 None（由调用方降级规则兜底）。
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in INTENT_LABELS:
        return text
    return INTENT_ALIASES.get(text)


class LLMRouter:
    """
    查询理解路由器：LLM 优先、规则兜底的统一入口。

    Args:
        llm_client: LLMClient 实例；为 None 表示无 LLM，自动走纯规则路径。
        classifier: 已注入知识库实体名的规则 IntentClassifier
                    （用于兜底分类与实体抽取）。
        intent_mode: "auto"(默认，LLM 可用则用 LLM，否则规则) / "rules"(纯规则)
                     / "llm"(强制 LLM，不可用时告警并降级规则)。
        rewrite_mode: "auto"(默认，LLM 可用 + 有历史 + 疑似指代才改写)
                      / "on" / "off"(禁用改写)。
    """

    def __init__(
        self,
        llm_client: Any,
        classifier: IntentClassifier,
        intent_mode: str = "auto",
        rewrite_mode: str = "auto",
    ):
        self.llm_client = llm_client
        self.classifier = classifier
        self.intent_mode = (intent_mode or "auto").lower()
        self.rewrite_mode = (rewrite_mode or "auto").lower()

    # ==================== 能力与开关 ====================

    def llm_available(self) -> bool:
        return self.llm_client is not None

    # ==================== 查询改写 ====================

    @staticmethod
    def _needs_rewrite(raw_question: str) -> bool:
        """
        判断一条问句是否需要"指代消解式改写"：
        1. 包含指代词/限定语（它、这个、两者、该技术……）；
        2. 或者是很短的问句（"为什么？""那循环寿命呢？"），大概率依赖上文。
        """
        if len(raw_question) <= 10:
            return True
        return any(token in raw_question for token in COREF_TOKENS)

    @staticmethod
    def clean_rewritten(text: str) -> str:
        """
        清理 LLM 改写输出，尽量还原成"一条干净的问句"。

        常见脏输出：
        - ``` 代码块围栏 / 前后引号
        - "改写后的问题：xxx" 这类说明前缀
        - 模型多行输出（先解释再给结果）→ 取最后一个非空行
        """
        if not text:
            return ""
        t = text.strip()
        # 去掉 Markdown 代码块围栏
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
        t = t.strip().strip("\"'“”‘’`")
        # 去掉常见前缀："改写后：/ 改写的问题：/ 独立问句：/ rewritten question:" 等
        prefix = re.compile(
            r"^(改写(后|成)?(的)?问题|重写(后|成)?(的)?问题|独立问句|整理后(的)?问(句|题)"
            r"|rewritten\s*(question|query)?)\s*[:：]\s*",
            re.IGNORECASE,
        )
        t = prefix.sub("", t)
        t = re.sub(r"^(问题|问句|query)[:：]\s*", "", t, flags=re.IGNORECASE)
        # 若仍是多行，取最后一个非空行（模型倾向把最终结果放最后）
        lines = [line.strip() for line in t.splitlines() if line.strip()]
        if lines:
            t = lines[-1]
        return t.strip()

    def _rewrite(self, raw_question: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        """执行查询改写。返回 {"text": 改写后问句, "used_llm": bool}"""
        original = raw_question
        # 1) 开关闸门
        if self.rewrite_mode == "off":
            return {"text": original, "used_llm": False}
        if self.rewrite_mode != "on" and not self.llm_available():
            return {"text": original, "used_llm": False}
        # 2) 场景闸门：无历史、纯闲聊、无指代嫌疑 → 不需要改写
        has_user_turn = any(m.get("role") == "user" for m in history)
        if not history or not has_user_turn:
            return {"text": original, "used_llm": False}
        if self.classifier.classify(original) == "chat":
            return {"text": original, "used_llm": False}
        if not self._needs_rewrite(original):
            return {"text": original, "used_llm": False}
        if not self.llm_available():
            # mode == "on" 但无 LLM：告警并降级，保证链路不断
            logger.warning("QUERY_REWRITE_MODE=on 但无可用 LLM，查询改写降级为原问题")
            return {"text": original, "used_llm": False}

        # 3) 调用 LLM
        system = QUERY_REWRITER_SYSTEM_PROMPT
        user = PromptBuilder.build_rewrite_prompt(original, history)
        content = self._call_llm(system, user, stage="查询改写")
        if content is None:
            return {"text": original, "used_llm": False}

        cleaned = self.clean_rewritten(content)
        if not cleaned:
            return {"text": original, "used_llm": False}
        logger.debug(f"查询改写：'{original}' → '{cleaned}'")
        return {"text": cleaned, "used_llm": True}

    # ==================== 检索失败改写重试（Week2 D6）====================

    def retry_rewrite(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        round_no: int = 1,
    ) -> Optional[str]:
        """
        检索零命中后的"改写重试"：让 LLM 生成一个更易命中资料的替代问法。

        与 _rewrite 的区别：
            _rewrite      针对"多轮指代"（把 '它' 补成实体），正常问答前执行；
            retry_rewrite 针对"零召回"（换说法/补全/必要时翻译），检索失败后执行。
        返回 None 表示无可用的替代问法（无 LLM / 输出为空 / 与原文相同），
        调用方应停止重试而不是死循环。
        """
        if not self.llm_available():
            return None
        system = QUERY_RETRY_SYSTEM_PROMPT
        user = PromptBuilder.build_retry_prompt(question, history, round_no=round_no)
        content = self._call_llm(system, user, stage="检索失败改写")
        if not content:
            return None
        cleaned = self.clean_rewritten(content)
        if not cleaned or cleaned == question:
            return None
        logger.debug(f"检索失败改写（第{round_no}轮）：'{question}' → '{cleaned}'")
        return cleaned

    # ==================== LLM 意图路由 ====================

    @staticmethod
    def parse_intent_json(text: str) -> Optional[Dict[str, Any]]:
        """
        从 LLM 输出中提取意图 JSON。

        容错点：
        - 输出可能包着 Markdown 代码块（```json ... ```）；
        - 可能在 JSON 前后夹杂一句解释；
        因此先找第一个 "{"，再用 json.JSONDecoder.raw_decode 只解析该对象，
        解析失败返回 None（调用方降级规则兜底）。
        """
        if not text:
            return None
        start = text.find("{")
        if start == -1:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    def _route_intent_llm(self, question: str) -> Optional[str]:
        """用 LLM 做意图分类，返回标准标签；任何失败返回 None"""
        system = PromptBuilder.build_intent_router_system_prompt()
        user = PromptBuilder.build_intent_router_prompt(question)
        content = self._call_llm(system, user, stage="意图路由")
        if content is None:
            return None

        obj = self.parse_intent_json(content)
        label = normalize_intent(obj.get("intent") if obj else None)
        if label is None:
            # 万一模型直接输出裸标签（如 property），也尝试归一化一次
            label = normalize_intent(content)
        if label is None:
            logger.warning(
                f"LLM 意图输出无法解析（前 120 字）：{content[:120]!r}，降级规则兜底"
            )
        return label

    # ==================== 统一 LLM 调用（含异常兜底）====================

    def _call_llm(self, system: str, user: str, stage: str) -> Optional[str]:
        """调用 LLM 并返回文本；失败返回 None（不向上层抛异常）。"""
        if not self.llm_available():
            return None
        try:
            result = self.llm_client.generate(system, user)
            return (result.get("content") or "").strip() or None
        except LLMError as e:
            if config.DEBUG:
                logger.opt(exception=True).debug(f"{stage}失败（LLMError）：{e}")
            else:
                logger.debug(f"{stage}失败：{e}")
            return None
        except Exception as e:  # 网络/解析等未知异常也一律降级
            if config.DEBUG:
                logger.opt(exception=True).debug(f"{stage}失败（未知异常）：{e}")
            else:
                logger.debug(f"{stage}失败：{e}")
            return None

    # ==================== 统一分析入口 ====================

    def analyze(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        综合分析用户问题（改写 → 意图 → 实体）。

        Args:
            question: 用户原始问题。
            history: 会话历史 [{role, content}, ...]，多轮指代消解用。

        Returns:
            {
                "raw_question": 原始问题（去首尾空白后）,
                "question":     改写后的自包含问题（检索/生成用）,
                "intent":       意图标签,
                "entities":     实体列表（规则抽取，命中知识库精确名）,
                "intent_source": "llm" | "rules",
                "rewrite_applied": bool,
                "rewrite_used_llm": bool,
            }
        """
        raw = self.classifier.preprocess(question)
        if not raw:
            return {
                "raw_question": raw,
                "question": raw,
                "intent": "unknown",
                "entities": [],
                "intent_source": "rules",
                "rewrite_applied": False,
                "rewrite_used_llm": False,
            }

        # Step 1: 查询改写（多轮指代消解）
        rewrite = self._rewrite(raw, list(history or []))
        effective = rewrite["text"]

        # Step 2: 意图路由（LLM 优先，规则兜底）
        intent, intent_source = self._route_intent(effective)

        # Step 3: 实体抽取（始终规则：实体表来自知识库，保精确命中）
        entities = []
        if intent not in ("chat", "unknown"):
            entities = self.classifier.extract_entities(effective)

        return {
            "raw_question": raw,
            "question": effective,
            "intent": intent,
            "entities": entities,
            "intent_source": intent_source,
            "rewrite_applied": effective != raw,
            "rewrite_used_llm": rewrite["used_llm"],
        }

    # ==================== 意图路由内部实现 ====================

    def _route_intent(self, question: str):
        """返回 (intent, source)；source ∈ {"llm", "rules"}"""
        # 纯规则模式：直接走兜底，一个 LLM 调用都不发（离线评测/对比实验用）
        if self.intent_mode == "rules":
            return self.classifier.classify(question), "rules"

        if self.llm_available():
            label = self._route_intent_llm(question)
            if label is not None:
                return label, "llm"
        elif self.intent_mode == "llm":
            logger.warning("INTENT_ROUTING_MODE=llm 但无可用 LLM，意图识别降级为规则")

        return self.classifier.classify(question), "rules"
