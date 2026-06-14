# AGENTS.md —— NewEnergyKG-myself

> 本文件面向 AI 编程助手，记录项目的实际结构、运行方式与开发约定。内容完全基于项目当前真实文件编写，不引入外部假设。

## 1. 项目概览

本项目是一个**新能源领域知识图谱（Knowledge Graph）构建与评估工程**，工作目录为 `d:\NewEnergyKG-myself`。

- **目标**：将新能源相关技术（储能、氢能、新能源汽车、核能、光伏、风电等）以 JSON 形式组织，导入 Neo4j 图数据库，形成 `Technology`、`Company`、`Material`、`Equipment`、`Application`、`Policy`、`Indicator`、`Category` 等实体及它们之间的关系。
- **当前状态**：
  - `data/` 存放原始/批量 JSON 数据与数据合并脚本。
  - `graph/` 存放基于官方 `neo4j` Python 驱动的入库脚本与质量评估脚本。
  - `qa/` 存放新能源领域问答系统（KG+RAG 版：Neo4j 知识图谱检索 + LLM 自然语言生成）。
  - 项目**没有** `README.md`、`pyproject.toml`、`setup.py`、`Makefile` 等常规工程配置文件；`qa/requirements.txt` 为问答系统依赖清单，其他依赖仍需手动安装。
- **附加文件**：根目录下存在 `知识工程实验.pdf`，为项目对应的知识工程实验文档。

## 2. 目录结构

```
NewEnergyKG-myself/
├── AGENTS.md                       # 本文件
├── 知识工程实验.pdf                 # 知识工程实验文档（PDF）
├── data/                           # 数据目录
│   ├── new_energy.json             # 主数据文件（63 条技术记录）
│   ├── energy_storage_10.json      # 储能批次数据（9 条记录）
│   ├── hydrogen_10.json            # 氢能批次数据（10 条记录）
│   ├── ne_vehicle_10.json          # 新能源汽车批次数据（10 条记录）
│   ├── nuclear_8.json              # 核能批次数据（8 条记录）
│   ├── pv_batch_15.json            # 光伏批次数据（15 条记录）
│   ├── wind_power_7.json           # 风电批次数据（7 条记录）
│   └── merge_data.py               # JSON 数据合并脚本
├── graph/                          # 图数据库构建与评估
│   ├── build_newenergy_graph.py    # 官方 neo4j 驱动版入库脚本
│   ├── quality_evaluation.py       # 图谱质量评估脚本
│   ├── config.py                   # 图谱模块配置管理（环境变量 / .env）
│   ├── .env.example                # 图谱模块环境变量示例
│   └── graph_data_export.json      # build_newenergy_graph.py 生成的导出/统计文件
├── static/                         # 前端静态页面
│   ├── index.html                  # 系统启动器页面
│   └── chat.html                   # 智能问答助手页面
└── qa/                             # 新能源问答系统
    ├── __init__.py
    ├── config.py                   # 配置管理（从环境变量读取 API Key、Neo4j 密码等）
    ├── llm_client.py               # LLM 客户端（DashScope / OpenAI 兼容接口）
    ├── kg_client.py                # Neo4j 知识图谱客户端
    ├── intent_classifier.py        # 意图识别与实体抽取
    ├── data_fallback.py            # 本地 JSON 数据兜底
    ├── prompt_builder.py           # Prompt 构建（支持注入 kg_context）
    ├── answer_engine.py            # 问答引擎（KG 检索 + LLM 生成 + 本地兜底）
    ├── main.py                     # FastAPI 服务入口
    ├── test_qa.py                  # 命令行测试脚本
    ├── evaluate.py                 # 纯 LLM 与 KG+RAG 对比评估脚本
    ├── eval_dataset.json           # 评估测试集
    ├── requirements.txt            # 问答系统依赖
    └── .env.example                # 环境变量示例
```

## 3. 技术栈

- **编程语言**：Python 3（代码使用 f-string，建议 Python 3.6+）。
- **图数据库**：Neo4j（本地默认地址 `bolt://localhost:7687`）。
- **Neo4j Python 驱动**：
  - 推荐/当前使用：`neo4j` 官方驱动（`graph/build_newenergy_graph.py`、`graph/quality_evaluation.py`、`qa/kg_client.py`）。
- **数据交换格式**：JSON（UTF-8，中文内容，`ensure_ascii=False`）。
- **开发工具**：`data/` 与 `graph/` 下均包含 `.idea/` 目录，说明使用 JetBrains PyCharm / IntelliJ IDEA 系列 IDE。

### 3.1 依赖安装

项目没有包管理清单，运行前请手动安装：

```bash
# 官方驱动版图谱脚本所需
pip install neo4j

# 如需运行旧版 data/generate_mock_data.py
pip install py2neo

# 问答系统依赖（推荐在虚拟环境中安装）
cd qa
pip install -r requirements.txt
```

## 4. 数据格式

`data/new_energy.json` 及其批次文件均为对象列表，每个技术条目包含以下 18 个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 技术名称（去重键） |
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

## 5. 代码组织与主要模块

### 5.1 `graph/build_newenergy_graph.py`

- 类：`NewEnergyGraph`
- 职责：读取 `new_energy.json`，连接 Neo4j，创建索引、节点、关系，导出统计 JSON，并验证入库结果。
- 关键方法：
  - `read_nodes()`：解析 JSON，生成去重实体集合与 8 类关系列表。
  - `create_indexes()`：为 8 类实体在 `name` 属性上创建索引，兼容 Neo4j 3.x / 4.x / 5.x。
  - `create_graphnodes()`：创建带属性的 `Technology` 节点与其他简单节点。
  - `create_graphrels()`：调用 `create_relationships()` 创建 8 种关系。
  - `export_and_stats()`：将实体与关系导出到 `graph/graph_data_export.json` 并打印规模统计。
  - `verify_in_neo4j()`：查询数据库核对节点/关系数量、关系类型分布及孤立节点数。
  - `clear_database()`：删除 Neo4j 中全部节点和关系（慎用）。

### 5.2 `graph/quality_evaluation.py`

- 类：`GraphQualityEvaluator`
- 职责：连接 Neo4j 后从准确性、完整性、一致性三个维度输出质量报告。
- 评估指标：空名称节点、孤立节点、各类节点描述完整率、同名冲突、关系类型分布、平均度数。

### 5.3 `data/merge_data.py`

- 函数：`merge_json_files(source_files, target_file, output_file)`
- 职责：以 `name` 为去重键，将多个批次 JSON 合并到目标文件，输出 `new_energy_merged.json`。
- 当前 `if __name__ == '__main__'` 示例仅合并 `ne_vehicle_10.json` 到 `new_energy.json`。

### 5.4 `qa/` 问答系统

- 已实现 **KG+RAG** 架构：先通过 `qa/kg_client.py` 从 Neo4j 检索结构化知识，再调用 LLM 生成自然语言回答；LLM 不可用时自动切换到本地 JSON 规则兜底。
- 核心模块：
  - `qa/config.py`：从环境变量读取 LLM API Key、模型名、服务地址、Neo4j 连接信息等，支持 `.env` 文件。
  - `qa/llm_client.py`：统一封装 DashScope（通义千问）与 OpenAI 兼容接口。
  - `qa/kg_client.py`：封装 `neo4j` 官方驱动，提供连接测试、技术综合查询、关系查询、类别查询、技术对比等接口。
  - `qa/intent_classifier.py`：基于 jieba 分词 + 关键词匹配 + RapidFuzz 模糊匹配 + 比较结构切分的意图识别与多实体抽取。
  - `qa/prompt_builder.py`：根据意图构建 System Prompt 与 User Prompt，支持将 `kg_context` 注入 User Prompt。
  - `qa/answer_engine.py`：编排问答流程；根据意图调用 `kg_client` 检索知识图谱上下文，再调用 LLM 生成回答；失败时启用本地 JSON 兜底。
  - `qa/main.py`：FastAPI 服务，挂载项目根目录 `static/` 为 `/static`，提供 `/`（系统启动器）、`/chat`（问答助手）、`/qa`、`/health`、`/status`、`/kg/status` 接口。
  - `qa/test_qa.py`：命令行交互式/单问题测试脚本。
  - `qa/evaluate.py`：批量对比评估脚本，支持纯 LLM 与 KG+RAG 两种模式，输出 Markdown 报告与 CSV 原始数据。


## 6. 构建与运行方式

> 项目没有统一的构建命令，按以下步骤手动执行。

### 6.1 准备 Neo4j

1. 启动本地 Neo4j 服务，默认端口 `7687`。
2. 确认用户名/密码。默认用户为 `neo4j`，密码需通过环境变量或 `.env` 文件配置。
3. 复制并填写环境变量示例文件：

   ```bash
   cp .env.example .env
   # 编辑 .env，至少填写：
   #   NEO4J_PASSWORD=your-neo4j-password
   ```

### 6.2 合并批次数据（可选）

```bash
cd data
python merge_data.py
```

- 默认读取 `new_energy.json` 作为现有数据，合并 `ne_vehicle_10.json` 后输出 `new_energy_merged.json`。
- 如需合并其他批次，修改 `source_files` 列表后运行。

### 6.3 构建知识图谱

```bash
cd graph
cp .env.example .env  # 首次运行需配置 Neo4j 密码
python build_newenergy_graph.py
```

运行流程：

1. 连接 Neo4j。
2. 创建索引。
3. 创建实体节点。
4. 创建关系边。
5. 导出统计到 `graph_data_export.json`。
6. 在 Neo4j 中验证统计结果。

### 6.4 质量评估

```bash
cd graph
python quality_evaluation.py
```

### 6.5 运行问答系统

#### 安装依赖

```bash
cd qa
pip install -r requirements.txt
```

#### 配置 LLM 与 Neo4j（可选）

复制环境变量示例文件并填写真实 API Key 与 Neo4j 密码：

```bash
cd qa
cp .env.example .env
# 编辑 .env，填写：
#   DASHSCOPE_API_KEY 或 OPENAI_API_BASE + OPENAI_API_KEY（启用 LLM 生成）
#   NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD（启用知识图谱检索）
```

- 未配置 API Key 时，系统将自动使用本地 JSON 数据生成规则兜底回答。
- 未配置 Neo4j 密码时，问答系统不会连接图数据库，但仍可依靠本地 JSON 与 LLM 回答。

#### 命令行测试

```bash
cd d:\NewEnergyKG-myself
python -m qa.test_qa "磷酸铁锂电池有哪些优点？"
```

或进入交互模式：

```bash
python -m qa.test_qa
```

#### 启动 FastAPI 服务

```bash
cd d:\NewEnergyKG-myself
python -m qa.main
```

服务启动后访问：

- 系统启动器：`http://127.0.0.1:8000/`
- 问答助手：`http://127.0.0.1:8000/chat`
- 问答接口：`POST http://127.0.0.1:8000/qa`
- 接口文档：`http://127.0.0.1:8000/docs`
- 健康检查：`GET http://127.0.0.1:8000/health`

#### 批量对比评估

```bash
cd d:\NewEnergyKG-myself
python -m qa.evaluate --mode both --output qa/eval_report.md --csv qa/eval_result.csv
```

- `--mode` 可选 `llm_only`（纯 LLM）、`kg_rag`（KG+RAG）、`both`（两者对比）
- 输出 `qa/eval_report.md`：汇总指标对比表、逐题时间对比表、完整答案附录
- 输出 `qa/eval_result_llm.csv` 与 `qa/eval_result_kg.csv`：原始结果
- 指标包括：意图准确率、实体识别精确率/召回率/F1、答案准确率、KG 召回率、KG 命中率、平均响应时间、回答来源分布等


## 7. 代码风格约定

- 文件头注释使用三引号文档字符串（中文）。
- 类名使用大驼峰（`NewEnergyGraph`、`GraphQualityEvaluator`），方法名使用下划线命名（`create_graphnodes`）。
- 注释以中文为主，使用 `====================` 分隔代码块。
- 字符串格式化混合使用 f-string 与 `%` 格式化。
- 使用 `if __name__ == '__main__':` 作为脚本入口。
- JSON 写入统一使用 `ensure_ascii=False, indent=2`。

## 8. 测试说明

- **项目当前没有自动化测试框架**（无 `pytest`、`unittest`、`tox` 等）。
- 验证方式以脚本输出和 Neo4j 查询为主：
  - 运行 `build_newenergy_graph.py` 查看节点/关系数量统计。
  - 运行 `quality_evaluation.py` 查看准确性、完整性、一致性报告。
  - 运行 `qa/test_qa.py` 测试问答系统意图识别、实体抽取与兜底回答。
  - 在 Neo4j Browser 中执行 Cypher 查询交叉验证。

## 9. 安全注意事项

- **明文凭据（已修复）**：`build_newenergy_graph.py`、`quality_evaluation.py` 与 `data/generate_mock_data.py` 中的 Neo4j 密码已从硬编码改为从环境变量 / `.env` 文件读取。运行前请复制 `.env.example` 为 `.env` 并填写真实密码，切勿将含真实密码的 `.env` 提交到版本控制。
- **硬编码绝对路径（已修复）**：`build_newenergy_graph.py` 的数据路径已改为基于脚本位置的相对路径（默认 `d:\NewEnergyKG-myself\data\new_energy.json`），并支持通过环境变量 `GRAPH_DATA_PATH` 自定义。
- **数据库清空操作**：`build_newenergy_graph.py::clear_database()` 与 `generate_mock_data.py::clear_graph()` 会删除 Neo4j 中全部节点和关系，生产环境慎用。
- **Cypher 注入风险**：`build_newenergy_graph.py` 中 `_create_relationship_tx` 使用字符串拼接构造关系类型，虽然节点匹配使用参数化查询，但关系类型未参数化；当前数据来自受控 JSON，风险较低，但应避免直接拼接用户输入。

## 10. 已知约束与常见问题

- 没有 `pyproject.toml`、`setup.py` 或 `README.md`；`qa/requirements.txt` 为问答系统依赖清单，其他脚本依赖仍需手动安装。
- `build_newenergy_graph.py` 的 `export_and_stats()` 已改用 `pathlib` 推导导出路径。
- 当前数据文件均为中文内容，编码为 UTF-8，读取时需指定 `encoding='utf-8'`。
- `graph_data_export.json` 是构建产物，不需要手工编辑。
- `data/energy_storage_10.json` 实际包含 9 条记录，文件名中的“10”与当前内容不符。

## 11. 附录：图谱规模参考

基于当前 `graph/graph_data_export.json` 的实际统计（供快速参考）：

| 实体/关系类型 | 数量 |
|--------------|------|
| Technology（技术） | 63 |
| Company（企业） | 255 |
| Material（材料） | 312 |
| Equipment（设备） | 275 |
| Application（应用） | 215 |
| Policy（政策） | 139 |
| Indicator（指标） | 215 |
| Category（类别） | 18 |
| belongs_to | 63 |
| uses_material | 423 |
| produced_by | 394 |
| applies_to | 349 |
| has_parameter | 370 |
| supported_by | 272 |
| requires_equipment | 411 |
| competes_with | 215 |
