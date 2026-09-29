# -*- coding: utf-8 -*-
"""
RAGAS 风格生成质量四指标（Week2 D3）

与 RAGAS（Retrieval-Augmented Generation Assessment）四个核心指标对齐，
采用"LLM-as-judge + 向量余弦"的自研实现 —— 不直接依赖 ragas 库，而是
把每个指标拆成可讲原理、可离线测试的小步骤（简历叙事：理解原理而非只会 pip）：

    faithfulness       忠实度    : 回答中的事实陈述有多少能被检索上下文支持
                                  （回答有没有"凭空编造"）
    answer_relevancy   答案相关性 : 回答是否切题 —— 官方口径是：让 LLM 由回答
                                  反推若干问题，再用 embedding 计算反推问题与
                                  用户问题的余弦相似度，取平均
    context_precision  上下文精确率: 检索到的上下文里，有多少是回答该问题真正
                                  需要的（考虑排序：越靠前的相关片段权重越高）
    context_recall     上下文召回率: 标准答案的陈述被检索上下文覆盖的比例
                                  （需要 ground_truth，评估"资料够不够全"）

可用性契约（重要）：
    - 任何一步 LLM 调用失败 / 输出不可解析 / 必要输入缺失 → 该指标返回 None，
      绝不误报 0（0 会被当成"很差"写进报告，误导判断）；
    - 汇总时只统计"成功判分"的样本，并在报告中注明样本数与缺失原因。

评分单元（sample）结构：
    {
        "question":     str,
        "answer":       str,                    # 被测系统生成的回答
        "contexts":     [str, ...],             # 检索到的上下文（chunk 文本）
        "ground_truth": str | None,             # 标准答案（context_recall 用）
    }
"""

import json
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

METRIC_LABELS = {
    "faithfulness": "faithfulness · 忠实度",
    "answer_relevancy": "answer_relevancy · 答案相关性",
    "context_precision": "context_precision · 上下文精确率",
    "context_recall": "context_recall · 上下文召回率",
}

# ==================== 裁判 Prompts ====================

# 陈述拆分：答案与 ground_truth 都会用到（RAGAS 里叫 statement decomposition）
CLAIM_EXTRACT_SYSTEM = """你是 RAG 评估助手。请把下面的文本拆成若干条"最小事实陈述"。

要求：
1. 每条陈述必须是能被资料证实或否定的独立断言（去掉问句、感叹、主观评价与建议）；
2. 按文本顺序输出，不要遗漏核心事实，也不要把一件事拆成两句废话；
3. 只输出 JSON：{"claims": ["陈述1", "陈述2", ...]}，不要输出其他内容。"""

# 支持度检查：faithfulness（回答陈述 vs 上下文）与 context_recall（标准答案陈述 vs 上下文）共用
SUPPORT_CHECK_SYSTEM = """你是 RAG 证据裁判。给定若干条"事实陈述"和一组"参考资料"，
逐条判断每条陈述是否被参考资料支持。

判定规则：
- 参考资料原文包含该事实、或对其进行等价重述/明确蕴含，都算"支持"；
- 参考资料完全未提及、或与陈述矛盾，都算"不支持"；
- 不要因为"资料没写"就脑补支持，也不要因为用词不同就判不支持。
只输出 JSON：{"supported": [true, false, ...]}，顺序必须与陈述顺序一一对应。"""

# 答案相关性：由回答反推检索式问题（RAGAS answer_relevancy 的官方做法）
QUESTION_GEN_SYSTEM = """你是 RAG 评估助手。请根据给定的"回答"内容，反推出若干个
与该回答含义一致、能够作为检索查询的自然问题。

要求：
1. 问题数量由指令指定（如 3 个），每个都要是独立可检索的问句；
2. 语言与回答保持一致；不添加回答里没有的信息；
3. 只输出 JSON：{"questions": ["问题1", "问题2", ...]}，不要输出其他内容。"""

# 上下文有用性判定（context_precision 用）
CONTEXT_RELEVANCE_SYSTEM = """你是 RAG 检索裁判。给定一个"用户问题"和若干条按顺序编号的
"检索片段"，逐条判断每条片段是否包含回答该问题所需要的信息。

判定规则：
- 片段提供了可用于回答问题的实体事实/数据 → 相关；
- 片段只是泛泛相关但没有支撑回答的信息 → 不相关；
- 判断必须结合问题本身，不要因为片段"看起来像知识库内容"就判相关。
只输出 JSON：{"relevant": [true, false, ...]}，顺序必须与片段顺序一一对应。"""


# ==================== 通用工具 ====================


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中解析第一个 JSON 对象（容忍前后废话/Markdown 围栏）。"""
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


def _chat_json(llm: Any, system: str, user: str, stage: str) -> Optional[Dict[str, Any]]:
    """
    调用 LLM 裁判并解析 JSON。任何失败返回 None（调用方自行降级/跳过）。
    统一在这里 catch 异常，保证一个样本判分出问题不会中断整批评估。
    """
    if llm is None:
        logger.debug(f"[{stage}] 无可用 LLM 裁判，跳过")
        return None
    try:
        result = llm.generate(system, user)
        content = (result.get("content") or "") if isinstance(result, dict) else str(result)
    except Exception as e:
        logger.warning(f"[{stage}] LLM 裁判调用失败，跳过该样本：{e}")
        return None

    obj = _parse_json_object(content)
    if obj is None:
        logger.warning(f"[{stage}] LLM 裁判输出无法解析（前 120 字）：{content[:120]!r}")
    return obj


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度；任一向量为零向量时返回 0（无方向可比）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# 陈述拆分结果缓存：key = 文本内容。
# 依据：陈述拆分是**纯粹的文本->陈述**映射，与样本、上下文都无关，
# 因此同一段文本重复送 LLM 属于纯浪费（实测评测集里会有多条样本
# 共享同一段 ground_truth，重复调用是确定性的可省开销）。
# 只缓存成功结果；失败不缓存，以便重试。
_CLAIMS_CACHE: Dict[str, List[str]] = {}


def _split_claims(llm: Any, text: str) -> Optional[List[str]]:
    """把一段文本拆成最小事实陈述（JSON {"claims": [...]}）。"""
    if not text or not text.strip():
        return []
    if text in _CLAIMS_CACHE:
        return _CLAIMS_CACHE[text]
    obj = _chat_json(llm, CLAIM_EXTRACT_SYSTEM, text, "陈述拆分")
    if obj is None:
        return None
    claims = obj.get("claims")
    if claims is None:
        claims = obj.get("statements")
    if not isinstance(claims, list):
        return None
    cleaned = [str(c).strip() for c in claims if str(c).strip()]
    # 允许空列表：无陈述是合法结果（如纯闲聊），与"解析失败"区分开
    _CLAIMS_CACHE[text] = cleaned
    return cleaned


def _check_support(
    llm: Any, claims: List[str], contexts: List[str]
) -> Optional[List[bool]]:
    """逐条判断 claims 是否被 contexts 支持（JSON {"supported": [...]}）。"""
    if not contexts:
        # 没有任何上下文时，陈述必然无据可依 → 全 False（不调 LLM）
        return [False] * len(claims)

    lines = [f"共有 {len(claims)} 条事实陈述："]
    for i, c in enumerate(claims, start=1):
        lines.append(f"{i}. {c}")
    lines.append("")
    lines.append(f"共有 {len(contexts)} 条参考资料：")
    for i, ctx in enumerate(contexts, start=1):
        lines.append(f"[{i}] {ctx}")
    obj = _chat_json(llm, SUPPORT_CHECK_SYSTEM, "\n".join(lines), "支持度检查")
    if obj is None:
        return None
    supported = obj.get("supported")
    if not isinstance(supported, list) or len(supported) != len(claims):
        logger.warning("支持度检查输出长度与陈述数不一致，按判分失败处理")
        return None
    return [bool(v) for v in supported]


def _number_list(obj: Optional[Dict[str, Any]], key: str) -> Optional[List[float]]:
    """从 JSON 对象里取数字列表；结构不对返回 None"""
    values = obj.get(key) if obj else None
    if not isinstance(values, list):
        return None
    try:
        return [float(v) for v in values]
    except (TypeError, ValueError):
        return None


# ==================== 四个指标 ====================


def faithfulness(answer: str, contexts: List[str], llm: Any) -> Optional[float]:
    """
    忠实度 = 回答中被上下文支持的陈述数 / 回答的总陈述数。

    范围 [0, 1]：1 = 全部有据可依（无幻觉），0 = 全部编造。
    """
    claims = _split_claims(llm, answer)
    if claims is None:
        return None
    if not claims:
        # 回答不含任何可证实陈述（如纯闲聊"你好"）→ 无幻觉空间，记 1.0
        return 1.0
    supported = _check_support(llm, claims, contexts)
    if supported is None:
        return None
    return round(_mean([1.0 if s else 0.0 for s in supported]), 4)


def answer_relevancy(
    question: str,
    answer: str,
    llm: Any,
    embedder: Optional[Any] = None,
    n_questions: int = 3,
) -> Optional[float]:
    """
    答案相关性（RAGAS 官方口径）：

    1. 让 LLM 从回答反推 n 个问题（这些问题是"能被该回答回答的问题"）；
    2. 计算每个反推问题与用户真实问题的 embedding 余弦相似度；
    3. 取平均。

    语义直觉：如果回答真的切题，那么"由回答反推出来的问题"
    应该和用户问的差不多 —— 反过来，驴唇不对马嘴的回答反推出的
    问题会跟用户问题差很远。
    """
    if not question or not answer:
        return None
    if llm is None:
        return None
    if embedder is None:
        from qa.ingestion.embedder import create_embedder
        try:
            embedder = create_embedder()
        except Exception as e:
            logger.warning(f"无法创建 Embedder，answer_relevancy 跳过：{e}")
            return None

    obj = _chat_json(
        llm, QUESTION_GEN_SYSTEM,
        f"请根据以下回答反推出 {n_questions} 个问题：\n{answer}",
        "问题反推",
    )
    questions = obj.get("questions") if obj else None
    if not isinstance(questions, list) or not questions:
        return None
    questions = [str(q).strip() for q in questions if str(q).strip()][:n_questions]
    if not questions:
        return None

    try:
        question_vec = embedder.embed_one(question)
        scores = []
        for gen_q in questions:
            q_vec = embedder.embed_one(gen_q)
            scores.append(_clamp01(_cosine(question_vec, q_vec)))
    except Exception as e:
        logger.warning(f"answer_relevancy 向量化失败，跳过：{e}")
        return None
    if not scores:
        return None
    return round(_mean(scores), 4)


def context_precision(question: str, contexts: List[str], llm: Any) -> Optional[float]:
    """
    上下文精确率（RAGAS 口径）：对"每一个恰好是相关片段的位置 k"，
    计算 precision@k = 前 k 个片段中相关片段的比例，再对所有这样的 k 取平均。

    直觉：既看片段是不是有用（precision 分母），也看有用的是不是靠前
    （只统计相关位置的 precision@k，第一个相关位置越靠前分越高）。
    """
    if not contexts:
        return None  # 没有上下文可判（不是 0：没有可评估对象）
    if llm is None:
        return None

    lines = [f"用户问题：{question}", "",
             f"共有 {len(contexts)} 条按顺序排列的检索片段："]
    for i, ctx in enumerate(contexts, start=1):
        lines.append(f"[{i}] {ctx}")
    obj = _chat_json(llm, CONTEXT_RELEVANCE_SYSTEM, "\n".join(lines), "片段相关性")
    relevant = obj.get("relevant") if obj else None
    if not isinstance(relevant, list) or len(relevant) != len(contexts):
        return None
    flags = [bool(v) for v in relevant]

    if not any(flags):
        return 0.0
    precisions_at_relevant_rank = []
    for k in range(1, len(flags) + 1):
        if flags[k - 1]:  # 只在"第 k 个片段恰好相关"时统计一次
            precisions_at_relevant_rank.append(sum(flags[:k]) / k)
    return round(_mean(precisions_at_relevant_rank), 4)


def context_recall(
    question: str,
    contexts: List[str],
    ground_truth: Optional[str],
    llm: Any,
) -> Optional[float]:
    """
    上下文召回率 = 标准答案中被上下文覆盖的陈述数 / 标准答案总陈述数。

    需要 ground_truth（标准答案）。语义：检索回来的资料够不够支撑
    "理想回答" —— 即使生成模型很会编，资料没覆盖的事实照样答不对。
    """
    if not ground_truth:
        return None  # 没有标准答案无法评估（不是 0）
    if not contexts:
        return 0.0  # 一条上下文都没有 → 覆盖率为 0
    claims = _split_claims(llm, ground_truth)
    if claims is None:
        return None
    if not claims:
        return 1.0  # 标准答案无可证实陈述（空泛话）→ 视为全覆盖
    covered = _check_support(llm, claims, contexts)
    if covered is None:
        return None
    return round(_mean([1.0 if s else 0.0 for s in covered]), 4)


# ==================== 样本级 / 批量汇总 ====================

# 指标名 → 需要的键（评估前先检查，缺输入就不尝试调用 LLM）
METRIC_REQUIREMENTS = {
    "faithfulness": {"answer", "contexts"},
    "answer_relevancy": {"question", "answer"},
    "context_precision": {"question", "contexts"},
    "context_recall": {"ground_truth", "contexts"},
}

# 指标名 → 评分函数
_METRIC_FUNCS: Dict[str, Callable] = {
    "faithfulness": faithfulness,
    "answer_relevancy": answer_relevancy,
    "context_precision": context_precision,
    "context_recall": context_recall,
}

# 指标名 → 需要传给评分函数的关键字参数（每个函数签名不同，不能一刀切全传）
_METRIC_KWARGS: Dict[str, tuple] = {
    "faithfulness": ("answer", "contexts"),
    "answer_relevancy": ("question", "answer", "embedder"),
    "context_precision": ("question", "contexts"),
    "context_recall": ("question", "contexts", "ground_truth"),
}


def _missing_keys(sample: Dict[str, Any], metric: str) -> List[str]:
    required = METRIC_REQUIREMENTS.get(metric, set())
    missing = [k for k in required if not sample.get(k)]
    # contexts 允许为空数组（faithfulness/recall 可判 0），只要键存在即可
    return [k for k in missing if not (k == "contexts" and k in sample)]


def score_sample(
    sample: Dict[str, Any],
    llm: Any,
    embedder: Optional[Any] = None,
    metrics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    对单个样本计算全部（或指定）生成质量指标。

    Returns:
        {
            "question": ...,
            "metrics": {"faithfulness": float|None, ...},
            "missing": {"faithfulness": ["answer"], ...},  # 缺输入的指标
        }
    """
    metrics = metrics or list(METRIC_LABELS.keys())
    result: Dict[str, Any] = {
        "question": sample.get("question", ""),
        "metrics": {},
        "missing": {},
    }
    for metric in metrics:
        func = _METRIC_FUNCS.get(metric)
        if func is None:
            continue
        missing = _missing_keys(sample, metric)
        if missing:
            result["metrics"][metric] = None
            result["missing"][metric] = missing
            continue
        try:
            kwargs: Dict[str, Any] = {"llm": llm}
            for key in _METRIC_KWARGS.get(metric, ()):
                if key == "embedder":
                    kwargs[key] = embedder
                else:
                    kwargs[key] = sample.get(key)
            result["metrics"][metric] = func(**kwargs)
        except Exception as e:
            logger.warning(f"[score_sample] {metric} 计算异常，跳过：{e}")
            result["metrics"][metric] = None
    return result


def summarize_scores(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    汇总多份样本的指标：

    Returns:
        {
            "samples": 总样本数,
            "faithfulness": {"score": 0.82, "judged": 18, "skipped": 2},
            ...
        }
    """
    summary: Dict[str, Any] = {"samples": len(scored)}
    for metric in METRIC_LABELS:
        values = [r["metrics"].get(metric) for r in scored
                  if r["metrics"].get(metric) is not None]
        summary[metric] = {
            "score": round(_mean(values), 4) if values else None,
            "judged": len(values),
            "skipped": len(scored) - len(values),
        }
    return summary
