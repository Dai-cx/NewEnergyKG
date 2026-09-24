# 检索消融实验报告

> K = 3（Recall@3 / Precision@3）

| 检索配置 | 查询数 | Recall@K | MRR | Precision@K |
|---|---|---|---|---|
| 向量检索 | 8 | 0.8750 | 0.7083 | 0.4583 |
| BM25检索 | 8 | 1.0000 | 1.0000 | 0.6667 |
| 混合(RRF) | 8 | 1.0000 | 1.0000 | 0.6250 |

## 说明

- 本报告为离线演示（DebugHash 伪向量 + 示例文档 8 chunks 自评测）。
- 真实运行：docker compose 启动后摄取真实文档，再执行 python -m qa.eval.run。
