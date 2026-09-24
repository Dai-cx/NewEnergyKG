# 🔋 NewEnergyKG — 新能源领域知识图谱与智能问答系统

> 基于 Neo4j 的新能源技术知识图谱构建与 KG+RAG 智能问答系统。

本项目是一个面向**新能源领域**的知识工程实践，涵盖储能、氢能、新能源汽车、核能、光伏、风电等技术方向。通过结构化 JSON 数据构建 Neo4j 知识图谱，并在此基础上实现了一个支持 **知识图谱检索 + 大语言模型生成（KG+RAG）** 的智能问答系统。

---

## ✨ 核心特性

- **多源异构数据融合**：覆盖 6 大新能源技术领域，整合技术、企业、材料、设备、政策等多维实体
- **大规模知识图谱**：基于 Neo4j 构建，包含 63 项核心技术、255+ 企业、300+ 材料/设备等实体，以及 2500+ 关系边
- **KG+RAG 多路混合检索**：知识图谱 + 向量 + BM25 多路召回，RRF 融合去重，gte-rerank 两阶段精排，回答带 `[1]` 编号引用与来源
- **查询理解层**：LLM 查询改写（多轮指代消解）+ LLM 意图路由（规则兜底），检索零命中时自动"改写重试"
- **文档摄取管道**：PDF/Markdown/TXT → 标题感知切分 → Embedding → Qdrant，内容哈希增量入库
- **评估闭环**：100 题 golden set + 检索指标（Recall@K/MRR/Precision@K）+ RAGAS 风格生成质量四指标 + 检索消融/参数扫描
- **图谱质量评估**：从准确性、完整性、一致性三个维度输出量化质量报告
- **Web 交互界面**：基于 FastAPI + 静态页面提供可视化问答服务

---

## 🏗️ 项目架构

```
NewEnergyKG/
├── AGENTS.md                      # 面向 AI 编程助手的项目约定文档
├── 知识工程实验.pdf                # 知识工程实验文档
├── data/                          # 数据层：原始 JSON 与合并脚本
│   ├── new_energy.json            # 主数据文件（63 条技术记录）
│   ├── energy_storage_10.json     # 储能技术数据（9 条记录）
│   ├── hydrogen_10.json           # 氢能技术数据（10 条记录）
│   ├── ne_vehicle_10.json         # 新能源汽车数据（10 条记录）
│   ├── nuclear_8.json             # 核能技术数据（8 条记录）
│   ├── pv_batch_15.json           # 光伏技术数据（15 条记录）
│   ├── wind_power_7.json          # 风电技术数据（7 条记录）
│   └── merge_data.py              # JSON 数据合并与去重脚本
├── graph/                         # 图谱层：构建与评估
│   ├── build_newenergy_graph.py   # 官方 neo4j 驱动版入库脚本
│   ├── quality_evaluation.py      # 图谱质量评估脚本
│   ├── config.py                  # 图谱模块配置管理（环境变量 / .env）
│   ├── .env.example               # 图谱模块环境变量示例
│   └── graph_data_export.json     # 图谱导出与统计文件
├── static/                        # 前端静态页面
│   ├── index.html                 # 系统启动器页面
│   └── chat.html                  # 智能问答助手页面
└── qa/                            # 应用层：智能问答系统
    ├── config.py                  # 配置管理（API Key / Neo4j / 检索/重试参数）
    ├── logging_setup.py           # loguru 日志初始化
    ├── llm_client.py              # LLM 客户端（DashScope / OpenAI 兼容）
    ├── llm_router.py              # LLM 查询改写 + 意图路由 + 检索失败改写（Week2）
    ├── intent_classifier.py       # 规则意图分类与实体抽取（LLM 路由的兜底）
    ├── kg_client.py               # Neo4j 知识图谱客户端
    ├── prompt_builder.py          # 系统/用户 Prompt + 编号引用 + 改写/路由 Prompt
    ├── data_fallback.py           # 本地 JSON 规则兜底回答
    ├── memory.py                  # 会话记忆（内存版）
    ├── answer_engine.py           # 问答编排（理解 → 多路检索 → 生成 → 重试）
    ├── main.py                    # FastAPI 服务入口（/qa /chat /status）
    ├── ingestion/                 # 文档摄取：parser/chunker/embedder/qdrant_store
    ├── retrieval/                 # 检索层：KG/向量/BM25 统一接口 + RRF + 重排
    ├── eval/                      # 评测：指标/数据集/消融/参数扫描/RAGAS 四指标
    ├── eval_dataset.json          # 评测集主文件（60 题）
    ├── eval_dataset_extra.json    # 评测集扩展（+40 题，合计 100 题）
    ├── test_qa.py                 # 命令行测试脚本
    ├── evaluate.py                # 纯 LLM vs KG+RAG 对比评估
    └── .env.example               # 环境变量模板
```

---

## 📊 知识图谱规模

| 实体/关系类型 | 数量 |
|:--|--:|
| **Technology（技术）** | 63 |
| **Company（企业）** | 255 |
| **Material（材料）** | 312 |
| **Equipment（设备）** | 275 |
| **Application（应用）** | 215 |
| **Policy（政策）** | 139 |
| **Indicator（指标）** | 215 |
| **Category（类别）** | 18 |
| `belongs_to` | 63 |
| `uses_material` | 423 |
| `produced_by` | 394 |
| `applies_to` | 349 |
| `has_parameter` | 370 |
| `supported_by` | 272 |
| `requires_equipment` | 411 |
| `competes_with` | 215 |

---

## 🚀 快速开始

### 1. 环境准备

- Python 3.6+
- Neo4j 数据库（本地默认 `bolt://localhost:7687`）

### 2. 安装依赖

```bash
# 安装官方 Neo4j 驱动
pip install neo4j

# 安装问答系统依赖（推荐在虚拟环境中）
cd qa
pip install -r requirements.txt
```

### 3. 配置环境变量

首次运行前，请复制环境变量示例文件并填写真实密码：

```bash
# 图谱模块
cd graph
cp .env.example .env
# 编辑 .env，填写 NEO4J_PASSWORD

# 问答系统
cd qa
cp .env.example .env
# 编辑 .env，填写：
#   DASHSCOPE_API_KEY 或 OPENAI_API_BASE + OPENAI_API_KEY
#   NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
```

> 💡 未配置 API Key 时，系统将自动使用本地 JSON 数据生成规则兜底回答；未配置 Neo4j 时，仍可依靠本地 JSON 与 LLM 回答。

### 4. 合并批次数据（可选）

```bash
cd data
python merge_data.py
```

- 默认以 `new_energy.json` 为基础，合并 `ne_vehicle_10.json`，输出 `new_energy_merged.json`。
- 如需合并其他批次，修改脚本中的 `source_files` 列表后运行。

### 5. 构建知识图谱

```bash
cd graph
python build_newenergy_graph.py
```

运行流程：
1. 连接 Neo4j 并创建索引
2. 创建实体节点（技术、企业、材料、设备等）
3. 创建关系边（8 种关系类型）
4. 导出统计到 `graph_data_export.json`
5. 在 Neo4j 中验证统计结果

### 6. 质量评估

```bash
cd graph
python quality_evaluation.py
```

### 7. 启动问答服务

```bash
# 命令行测试（单问题）
cd NewEnergyKG
python -m qa.test_qa "磷酸铁锂电池有哪些优点？"

# 命令行交互模式
python -m qa.test_qa

# 启动 FastAPI 服务
python -m qa.main
```

服务启动后访问：
- 🏠 系统首页：`http://127.0.0.1:8000/`
- 💬 问答界面：`http://127.0.0.1:8000/chat`
- 📖 API 文档：`http://127.0.0.1:8000/docs`
- ✅ 健康检查：`http://127.0.0.1:8000/health`

### 8. 批量对比评估

```bash
python -m qa.evaluate --mode both --output qa/eval_report.md --csv qa/eval_result.csv
```

支持三种模式：
- `llm_only`：纯 LLM 回答
- `kg_rag`：知识图谱增强回答
- `both`：两者对比评估

### 9. 检索评估闭环（Week2：消融 / 参数扫描 / RAGAS 四指标）

评测集已扩到 **100 题**（`qa/eval_dataset.json` 60 题 + `qa/eval_dataset_extra.json` 40 题，
后者带 `reference_answer` 标准答案），配套命令：

```bash
# ① 检索消融：纯KG / 纯向量 / 纯BM25 / 混合(RRF) / 混合(RRF)+重排
python -m qa.eval.run --output qa/eval/retrieval_ablation_report.md

# ② 参数扫描（RRF 平滑常数 k；配置 Key 后加扫重排候选池）
python -m qa.eval.tune --fusion-k-values 30,60,100 --limit 30

# ③ 生成质量四指标（faithfulness 等，需 DASHSCOPE_API_KEY）：
#    先现场收集真实问答样本（回答 + 检索引用），再评分出报告
python -m qa.eval.gen_run --collect-live --samples-output qa/eval/collected_samples.json
python -m qa.eval.gen_run --samples qa/eval/collected_samples.json
```

- **检索指标**：Recall@K / MRR / Precision@K（LLM-free 近似判定）；
- **生成指标**：faithfulness / answer_relevancy / context_precision / context_recall
  （自研 RAGAS 风格，LLM-as-judge）；
- **切分粒度实验**：`python -m qa.ingestion.run --collection <集合名> --chunk-size N`
  分别摄取同一语料后，用 `python -m qa.eval.run --collection <集合名>` 对比各集合的检索指标。

---

## 📝 数据格式

每个技术条目包含 18 个结构化字段：

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `name` | string | 技术名称（唯一标识） |
| `desc` | string | 技术描述 |
| `principle` | string | 工作原理 |
| `advantage` | list[string] | 优点 |
| `disadvantage` | list[string] | 缺点 |
| `efficiency` | string | 效率说明 |
| `cost_level` | string | 成本等级 |
| `development_stage` | string | 发展阶段 |
| `market_share` | string | 市场份额 |
| `maturity` | string | 成熟度 |
| `category` | string | 所属技术类别 |
| `companies` | list[string] | 相关企业 |
| `materials` | list[string] | 所用材料 |
| `equipments` | list[string] | 所需设备 |
| `applications` | list[string] | 应用场景 |
| `indicators` | list[string] | 技术指标 |
| `policies` | list[string] | 相关政策 |
| `compete_technologies` | list[string] | 竞争技术 |

---

## 🧪 测试说明

本仓库已建立 **pytest 自动化测试体系**（`tests/`），全部离线可跑（Fake Neo4j / 内存 Qdrant / 脚本化 LLM）：

```bash
python -m pytest -q          # 当前 180+ 用例全绿
pip install -e ".[dev]"      # 安装测试依赖（pytest）
```

覆盖范围：配置 / 意图分类 / 图谱客户端 / Prompt 与记忆 / 兜底回答 / 问答引擎（含
改写与检索失败重试）/ 摄取管道（增量去重）/ 检索与重排（含召回池回归）/ 评测
（指标核算、消融、参数扫描、RAGAS 四指标）。

手动冒烟（可选）：`python -m qa.test_qa "..."` 单问题问答、Neo4j Browser 中
执行 Cypher 交叉验证。

---

## ⚠️ 安全注意事项

1. **密码管理**：`graph/` 与 `qa/` 模块均已从硬编码改为从环境变量 / `.env` 文件读取密码。运行前请复制 `.env.example` 为 `.env` 并填写真实密码，**切勿将含真实密码的 `.env` 提交到版本控制**。
2. **数据库清空**：`build_newenergy_graph.py::clear_database()` 会删除 Neo4j 中全部节点和关系，生产环境请慎用。
3. **Cypher 注入风险**：`build_newenergy_graph.py` 中关系类型使用字符串拼接构造，虽然节点匹配已参数化，但应避免直接拼接用户输入。

---

## 📚 技术栈

- **后端**：Python 3, FastAPI
- **图数据库**：Neo4j（官方 `neo4j` Python 驱动）
- **大语言模型**：DashScope（通义千问）/ OpenAI 兼容接口
- **自然语言处理**：jieba 分词, RapidFuzz 模糊匹配
- **数据格式**：JSON（UTF-8）

---

## ❗ 已知约束与常见问题

- 项目没有 `pyproject.toml`、`setup.py` 或 `requirements.txt`（根目录）；`qa/requirements.txt` 为问答系统专用依赖清单，其他脚本依赖仍需手动安装。
- `graph_data_export.json` 是构建产物，不需要手工编辑。
- 当前数据文件均为中文内容，编码为 UTF-8，读取时需指定 `encoding='utf-8'`。

---

## 📄 许可证

本项目为课程学习与实践项目，仅供学习交流使用。

---

> 🌱 探索新能源技术的知识边界，让结构化知识赋能智能问答。
