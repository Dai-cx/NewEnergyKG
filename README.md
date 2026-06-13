# 🔋 NewEnergyKG — 新能源领域知识图谱与智能问答系统

> 基于 Neo4j 的新能源技术知识图谱构建与 KG+RAG 智能问答系统。

本项目是一个面向**新能源领域**的知识工程实践，涵盖储能、氢能、新能源汽车、核能、光伏、风电等技术方向。通过结构化 JSON 数据构建 Neo4j 知识图谱，并在此基础上实现了一个支持 **知识图谱检索 + 大语言模型生成（KG+RAG）** 的智能问答系统。

---

## ✨ 核心特性

- **多源异构数据融合**：覆盖 6 大新能源技术领域，整合技术、企业、材料、设备、政策等多维实体
- **大规模知识图谱**：基于 Neo4j 构建，包含 63 项核心技术、255+ 企业、300+ 材料/设备等实体，以及 2500+ 关系边
- **KG+RAG 智能问答**：支持意图识别、实体抽取、图谱检索、LLM 生成与本地兜底的多级问答架构
- **图谱质量评估**：从准确性、完整性、一致性三个维度输出量化质量报告
- **Web 交互界面**：基于 FastAPI + 静态页面提供可视化问答服务

---

## 🏗️ 项目架构

```
NewEnergyKG/
├── data/                          # 数据层：原始 JSON 与合并脚本
│   ├── new_energy.json            # 主数据文件（63 条技术记录）
│   ├── energy_storage_10.json     # 储能技术数据
│   ├── hydrogen_10.json           # 氢能技术数据
│   ├── ne_vehicle_10.json         # 新能源汽车数据
│   ├── nuclear_8.json             # 核能技术数据
│   ├── pv_batch_15.json           # 光伏技术数据
│   ├── wind_power_7.json          # 风电技术数据
│   ├── merge_data.py              # JSON 数据合并与去重脚本
│   └── generate_mock_data.py      # 旧版 py2neo 构建脚本（参考用）
├── graph/                         # 图谱层：构建与评估
│   ├── build_newenergy_graph.py   # 官方 neo4j 驱动版入库脚本
│   ├── quality_evaluation.py      # 图谱质量评估脚本
│   └── graph_data_export.json     # 图谱导出与统计文件
├── qa/                            # 应用层：智能问答系统
│   ├── config.py                  # 配置管理（API Key / Neo4j 连接）
│   ├── llm_client.py              # LLM 客户端（DashScope / OpenAI）
│   ├── kg_client.py               # Neo4j 知识图谱客户端
│   ├── intent_classifier.py       # 意图识别与实体抽取
│   ├── prompt_builder.py          # Prompt 构建与 KG 上下文注入
│   ├── answer_engine.py           # 问答引擎（KG 检索 + LLM 生成）
│   ├── main.py                    # FastAPI 服务入口
│   ├── test_qa.py                 # 命令行测试脚本
│   ├── evaluate.py                # 纯 LLM vs KG+RAG 对比评估
│   ├── eval_dataset.json          # 评估测试集
│   ├── requirements.txt           # 问答系统依赖
│   └── .env.example               # 环境变量模板
└── static/                        # 前端静态页面
    ├── index.html                 # 系统启动器
    └── chat.html                  # 智能问答助手界面
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

### 3. 配置环境变量（可选）

```bash
cd qa
cp .env.example .env
# 编辑 .env 文件，填写：
#   DASHSCOPE_API_KEY 或 OPENAI_API_BASE + OPENAI_API_KEY
#   NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
```

> 💡 未配置 API Key 时，系统将自动使用本地 JSON 数据生成规则兜底回答；未配置 Neo4j 时，仍可依靠本地 JSON 与 LLM 回答。

### 4. 构建知识图谱

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

### 5. 质量评估

```bash
cd graph
python quality_evaluation.py
```

### 6. 启动问答服务

```bash
# 命令行测试
cd NewEnergyKG
python -m qa.test_qa "磷酸铁锂电池有哪些优点？"

# 启动 FastAPI 服务
python -m qa.main
```

服务启动后访问：
- 🏠 系统首页：`http://127.0.0.1:8000/`
- 💬 问答界面：`http://127.0.0.1:8000/chat`
- 📖 API 文档：`http://127.0.0.1:8000/docs`
- ✅ 健康检查：`http://127.0.0.1:8000/health`

### 7. 批量对比评估

```bash
python -m qa.evaluate --mode both --output qa/eval_report.md --csv qa/eval_result.csv
```

支持三种模式：
- `llm_only`：纯 LLM 回答
- `kg_rag`：知识图谱增强回答
- `both`：两者对比评估

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

## ⚠️ 注意事项

1. **路径配置**：`graph/build_newenergy_graph.py` 中默认使用绝对路径 `D:\NewEnergyKG\data\new_energy.json`，请根据实际项目位置修改或使用相对路径。
2. **数据库安全**：`clear_database()` 方法会清空 Neo4j 中全部数据，生产环境请慎用。
3. **密码管理**：建议将 Neo4j 密码等敏感信息通过环境变量或 `.env` 文件管理，避免硬编码。
4. **Windows 路径**：部分脚本使用 `/` 分割路径，在 Windows 下建议改用 `pathlib` 处理。

---

## 📚 技术栈

- **后端**：Python 3, FastAPI
- **图数据库**：Neo4j（官方 `neo4j` Python 驱动）
- **大语言模型**：DashScope（通义千问）/ OpenAI 兼容接口
- **自然语言处理**：jieba 分词, RapidFuzz 模糊匹配
- **数据格式**：JSON（UTF-8）

---

## 📄 许可证

本项目为课程学习与实践项目，仅供学习交流使用。

---

> 🌱 探索新能源技术的知识边界，让结构化知识赋能智能问答。
