# -*- coding: utf-8 -*-
"""
qa.eval —— 检索评估与消融实验（LLM-free 部分）

目标：用可量化的离线指标回答工程问题——"混合检索（RRF）到底比单路强多少？"

RAG 的评估通常分两层：
1. **检索质量**（本包负责）：只评"取回来的资料对不对"，不生成回答。
   指标：Recall@K（前 K 名内是否命中）、MRR（第一个命中的名次）、
   Precision@K（前 K 名里多少是相关的）。
   相关性判定（relevance judgment）用 golden set 里的期望实体/章节做
   文本匹配，**不依赖 LLM、不花 API 费用**，可离线、可复现。
2. **生成质量**（faithfulness / answer relevancy）：需要 LLM 判分，
   留待配置 API Key 后接入（可挂在 RAGAS 上）。

模块划分：
    dataset.py  数据集加载与相关性判定
    metrics.py  排名指标计算
    ablation.py 消融实验编排 + Markdown 报告
    run.py      命令行入口
"""

from qa.eval.dataset import build_chunk_dataset, judge_result, load_qa_golden
from qa.eval.metrics import summarize_retrieval

__all__ = [
    "load_qa_golden",
    "build_chunk_dataset",
    "judge_result",
    "summarize_retrieval",
]
