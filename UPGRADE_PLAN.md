# 新能源知识库智能助手 · 升级执行计划（Agentic RAG）

> 文档状态：已确认方案，待开工
> 目标周期：3 周（课余 + 周末）
> 方向：Agentic RAG 融合（检索按 RAG 标准做深，编排按 Agent 标准做活）
> 技术路线：混合自研（检索自研 + LangGraph 编排）

---

## 1. 背景与目标

本项目（NewEnergyKG）目前是一个基于 Neo4j 知识图谱的 KG+RAG 问答系统，覆盖储能、氢能、新能源汽车、核能、光伏、风电六大领域，包含 63 项技术、2500+ 关系边，已有规则意图识别、图谱检索、LLM 生成、本地兜底与对比评估脚本。

目标：在 3 周内升级为一个可写进简历的 **Agentic RAG 智能知识助手**，具备：

- 文档摄取与多路混合检索（图谱 + 向量 + BM25 + Rerank）
- 基于 LangGraph 的"规划—检索—生成—反思"Agent 编排
- RAGAS 量化评估闭环与检索消融实验
- 流式输出、引用溯源、多轮记忆、Docker 一键部署

---

## 2. 现状盘点

### 2.1 已有资产（保留）

| 模块 | 现状 | 评价 |
|---|---|---|
| 数据层 `data/` | 63 条技术记录、18 个字段，手工 JSON | 结构清晰，但只覆盖结构化技术卡片 |
| 图谱层 `graph/` | Neo4j，8 类实体、8 种关系，2500+ 关系边 | 最大的差异化资产 |
| 检索层 `qa/kg_client.py` | 5 类 Cypher 查询（概览/关系/类别/对比/统计） | 单一检索通道，无向量、无重排 |
| 理解层 `qa/intent_classifier.py` | 规则 + jieba + rapidfuzz 五级匹配 | 可解释性好，但泛化差 |
| 生成层 `qa/answer_engine.py` | KG 上下文注入 Prompt → LLM → 本地兜底 | 线性 pipeline，适合改造成 Agent 状态机 |
| 评估 `qa/evaluate.py` | 60 题 golden set，关键词命中 + LLM-as-judge | 强于大多数课程项目，是升级基础 |
| 前端 `static/chat.html` | 原生 HTML + fetch，会话存 localStorage | 可用，但无流式、无引用溯源 |

### 2.2 主要差距

1. 没有文档摄取管道（知识只来自手工 JSON）
2. 没有向量检索 / 多路召回 / 重排
3. 意图与实体识别是纯规则的
4. 问答是线性 pipeline，非 Agent 状态机
5. 无测试、无 Docker、无 CI、无链路追踪
6. 记忆仅内存 deque，重启即丢
7. 评估缺少 RAGAS 检索质量指标

---

## 3. 方向与选型（已确认）

| 决策项 | 选择 | 理由 |
|---|---|---|
| 项目定位 | **Agentic RAG 融合** | 检索深度 + Agent 编排广度，最贴合 AI 应用工程师秋招 |
| 技术路线 | **混合自研**：检索自研 + LangGraph 编排 | 检索原理可讲透，编排接业界标准 |
| 向量化 / 重排 | **DashScope API**（text-embedding-v3 / gte-rerank） | 零 GPU 成本、开发快；接口抽象后可切本地 BGE-M3 |
| 前端 | **不重写**，在现有 chat.html 上加 SSE 流式 + 引用展示 | 保住时间给 Agent 核心 |
| 部署 | Docker Compose 全栈 + 尽力在线部署 | 加分项，不能卡进度 |

### 3.1 目标架构

```
用户 → Web 前端（流式对话） → FastAPI 网关
                                  │
                    ┌─────────────▼──────────────┐
                    │   Agent 编排层 (LangGraph)  │
                    │  规划 → 选工具 → 执行 → 反思 │
                    └──────┬──────────┬──────────┘
                           │          │
              ┌────────────▼───┐  ┌───▼──────────────┐
              │ RAG 工具集      │  │ 辅助工具          │
              │ · 图谱检索(Neo4j)│  │ · 会话记忆(Redis) │
              │ · 向量检索(Qdrant)│ │ · 长期记忆(向量)  │
              │ · 全文检索(BM25) │  │ · 计算/查询工具   │
              │ · 混合 + Rerank  │  └──────────────────┘
              └────────┬────────┘
                       │
        ┌──────────────▼───────────────────┐
        │  知识生产管道（离/在线）            │
        │ PDF/网页 → 解析 → 切分 → Embedding │
        │          → LLM 抽取 → 图谱更新      │
        └──────────────────────────────────┘
        （评估闭环 RAGAS + 可观测 Langfuse + Docker Compose 部署）
```

### 3.2 推荐技术栈

| 层次 | 选型 |
|---|---|
| 语言 / Web | Python 3.11 + FastAPI + Pydantic v2 |
| 编排 | LangGraph（有状态图；不顺则退化为自写状态机） |
| 向量库 | Qdrant（Docker 单容器，首选）/ Milvus / pgvector |
| Embedding | DashScope text-embedding-v3（备选本地 BGE-M3） |
| 重排 | DashScope gte-rerank（备选 BGE-reranker-v2-m3） |
| 全文检索 | BM25（内置实现）/ Meilisearch |
| 图数据库 | Neo4j（保留现有资产，查询包装为检索工具） |
| 文档解析 | pypdf + MarkItDown / Unstructured |
| 记忆 | Redis（短期会话）+ 向量记忆（长期） |
| 评估 | 保留 evaluate.py + RAGAS |
| 可观测 | Langfuse（自托管，可选） |
| 前端 | 现有 chat.html + SSE 流式 + 引用卡片 |
| 工程 | pytest + Docker Compose + GitHub Actions |

---

## 4. 参考项目调研

| 参考项目 | 定位 | 值得借鉴的点 |
|---|---|---|
| Langchain-Chatchat（github.com/chatchat-space/Langchain-Chatchat） | 本地知识库问答 | 文档摄取 → 切分 → 向量化 → 检索的完整管道 |
| RAGFlow（github.com/infiniflow/RAGFlow） | 深度文档理解型 RAG | 模板化切分（表格/版面）、引用溯源 |
| QAnything（github.com/netease-youdao/QAnything） | 两阶段检索 RAG | embedding 粗排 + rerank 精排的标准姿势 |
| Microsoft GraphRAG（github.com/microsoft/graphrag） | 图谱 + 社区检测 | 图谱数据的全局问答思路（本期砍掉，留作后续） |
| LightRAG（github.com/HKUDS/LightRAG） | 轻量图谱增强 RAG | 双层检索（局部实体 + 全局主题） |
| LangGraph（github.com/langchain-ai/langgraph） | Agent 状态机编排 | 有状态图编排、条件路由、人机回环 |
| QASystemOnMedicalKG（github.com/liuhuanyong/QASystemOnMedicalKG） | 医疗图谱问答 | 本项目建设脚本即参考此项目，经典范式 |
| Dify / FastGPT（github.com/langgenius/dify） | RAG/Agent 应用平台 | 产品化形态：知识库管理、编排、应用发布 |
| RAGAS（github.com/explodinggradients/ragas） | RAG 评估框架 | faithfulness / answer relevancy / context precision 指标 |

学习策略：不照搬框架全家桶；检索层自研（透明可讲），编排层用 LangGraph（体现业界工程能力）。

---

## 5. 三周执行计划

> 每阶段结束必须：跑通评估 + 更新 README，保证随时可展示。

### 5.1 第一周：工程基座 + 多路检索 v1

**目标**：把"单一图谱检索"升级为"图谱 + 向量 + 全文 + 重排"四件套，补上文档摄取能力。

| 天 | 任务 | 落点 |
|---|---|---|
| D1 | pyproject.toml 统一依赖；pytest 骨架 + mock Neo4j 单元测试；loguru 日志 | 新增 `pyproject.toml`、`tests/`；改造 `qa/config.py` |
| D2 | Docker Compose 骨架（neo4j + qdrant + redis + api） | 新增 `docker-compose.yml`、`Dockerfile` |
| D3-4 | 文档摄取管道：PDF/Markdown → 解析 → 语义切分 → DashScope embedding → 写入 Qdrant | 新增 `qa/ingestion/`（parser/chunker/embedder） |
| D5 | 向量检索器 + BM25 + RRF 混合融合 | 新增 `qa/retrieval/vector_retriever.py`、`bm25_retriever.py`、`hybrid.py` |
| D6 | 重排：gte-rerank 对 Top-50 精排取 Top-10 | 新增 `qa/retrieval/reranker.py` |
| D7 | 统一 Retriever 抽象接口；KG 查询包装成工具；端到端检索测试跑通 | 改造 `qa/kg_client.py`，新增 `qa/retrieval/__init__.py` |

**验收**：`python -m qa.ingestion.run --input docs/xxx.pdf` 能入库；`python -m qa.test_qa "钙钛矿电池的转化效率是多少"` 回答带引用来源。

### 5.2 第二周：检索质量 + 评估闭环

**目标**：用数据证明检索方案更好，产出面试量化弹药。

| 天 | 任务 | 落点 |
|---|---|---|
| D1 | LLM 查询改写（多轮指代消解）+ LLM 意图路由，规则识别降级为兜底 | 改造 `qa/intent_classifier.py`、`qa/prompt_builder.py` |
| D2-3 | golden set 扩到 100 题；接入 RAGAS（faithfulness、answer relevancy、context precision/recall） | 改造 `qa/evaluate.py`，新增 `qa/eval/` |
| D4-5 | 检索消融实验：纯KG / 纯向量 / 混合 / 混合+重排 四组对比，产出报告 | 新增 `qa/eval/ablation.md`（自动化脚本） |
| D6-7 | 依据消融结果调参（RRF 的 k、Top-N、切分粒度）；补检索失败→改写重试用例；更新 README 评估章节 | 调 `qa/retrieval/` 参数 |

**验收**：`python -m qa.evaluate --mode both --judge` 输出 RAGAS 四指标 + 消融对比表；混合+重排组在 faithfulness / context precision 上全面优于单路。

### 5.3 第三周：Agent 化 + 产品化收尾

**目标**：把 answer_engine 线性流程改造成 LangGraph 状态机，Demo 可演示、可讲述。

| 天 | 任务 | 落点 |
|---|---|---|
| D1-2 | LangGraph 改造：plan → retrieve → answer → reflect 四节点有状态图；工具注册；检索失败自动改写 query 重试 ≤2 轮 | 重构 `qa/answer_engine.py` → 新增 `qa/agent/graph.py`、`qa/agent/tools.py` |
| D3 | 多轮记忆升级：Redis 短期会话 + 向量长期记忆 | 新增 `qa/memory/redis_memory.py`（保留 memory.py 兜底） |
| D4-5 | SSE 流式输出：后端 `/qa/stream` + 前端打字机 + 引用卡片 + 检索过程展示 | 改造 `qa/main.py`、`static/chat.html` |
| D6 | Docker Compose 全栈跑通；GitHub Actions CI；README 重写（架构图 + 指标 + 演示） | 新增 `.github/workflows/ci.yml` |
| D7 | 录 3 分钟演示视频；更新简历 bullet；Demo 收尾 | 文档类 |

**验收**：浏览器流式对话，回答带引用与检索过程；`docker compose up` 一键起全套；CI 绿；简历可投。

---

## 6. 取舍清单（3 周约束）

| 项 | 决策 |
|---|---|
| 向量化 / 重排 | DashScope API（text-embedding-v3 / gte-rerank），不跑本地大模型 |
| 前端 | 不重写，现有 chat.html 加 SSE 流式 + 引用展示 |
| GraphRAG 社区检测 | 砍掉（留作简历"后续计划"一句话） |
| MCP / 多智能体 / 语义缓存 | 砍掉 |
| 在线部署 | 第 3 周末尽力而为，至少产出 Docker Compose + 本地演示视频 |
| Langfuse 可观测 | 可选，时间不足则跳过（检索 trace 用日志替代） |

---

## 7. 风险与预案

| 风险 | 预案 |
|---|---|
| DashScope embedding/rerank 限流或额度不够 | 检索层抽象好接口，一键切换本地 sentence-transformers（BGE-M3），接口不变 |
| LangGraph 学习曲线 | 第一天先跑官方 quickstart；不顺则退化为自写状态机（功能等价） |
| 时间被挤占 | 砍单顺序：引用溯源展示 → 向量长期记忆 → 在线部署；检索 + Agent 状态机 + 评估绝不动 |
| 评估分数不升反降 | 属正常现象；消融报告本身就是面试素材（哪些场景向量优于图谱、反之亦然） |

---

## 8. 简历写法（升级完成后示例）

> **新能源领域智能知识助手（Agentic RAG）** · FastAPI / Neo4j / Qdrant / BGE / LangGraph
> - 构建多路混合检索（知识图谱 + 向量 + BM25 + Rerank），将答案关键词准确率从 69% 提升至 88%，RAGAS Faithfulness ≥ 0.9；
> - 基于 LangGraph 实现"规划—检索—生成—反思"的 Agent 编排，支持多跳问答与检索失败自动改写重试；
> - 自建 100+ 题评测集 + RAGAS 指标闭环，产出四组检索策略消融实验报告；支持文档摄取、引用溯源、流式输出；
> - Docker Compose 一键部署 + GitHub Actions CI + Langfuse 全链路追踪，Demo 在线可访问。

---

## 9. 面试深挖点（提前准备）

1. 切分策略：为什么用语义切分而非固定长度？中文场景的边界问题？
2. 混合检索融合：RRF 的 k 值怎么调？向量与 BM25 各自在什么场景失效？
3. 幻觉控制：图谱事实与检索证据都为空时，Agent 如何诚实回答"不知道"？（设计显式 unknown 出口）

---

## 10. 开工前准备（用户侧）

1. 确认 DashScope 账号有 `text-embedding-v3` 和 `gte-rerank` 模型权限（没有则用 BGE 本地方案兜底）；
2. 准备 3~5 份真实的新能源 PDF/Markdown 文档（行业报告、政策文件等）作为摄取管道测试素材。
