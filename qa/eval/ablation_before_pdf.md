# 检索消融实验报告

> K = 5（Recall@5 / Precision@5）

> 评测集：qa/eval_dataset.json + qa/eval_dataset_extra.json（共 100 题参与，100 题中自动过滤多轮指代题）
> 文档集合：config.QDRANT_COLLECTION（默认）
> RRF 平滑常数 k = 60
> 重排候选池 = 50；真实重排组：已启用（dashscope_rerank）

| 检索配置 | 查询数 | Recall@K | MRR | Precision@K |
|---|---|---|---|---|
| 纯KG图谱 | 100 | 0.6700 | 0.6700 | 0.2960 |
| 纯向量 | 100 | 0.2800 | 0.1968 | 0.0740 |
| 纯BM25 | 100 | 0.3900 | 0.2943 | 0.1200 |
| 混合(RRF) | 100 | 0.3500 | 0.2508 | 0.0940 |
| 混合(RRF)+重排 | 100 | 0.3200 | 0.2500 | 0.1000 |

## 说明

- 相关性判定为 LLM-free 近似：期望实体/章节出现在检索文本或章节路径中即视为相关。
- 纯KG 组只评估图谱能答的问题；文档组（向量/BM25/混合）评估文档语料召回。
- 生成质量四指标（faithfulness 等）用 LLM-as-judge：python -m qa.eval.gen_run。
