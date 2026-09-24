# 升级学习日志（LEARNING LOG）

> 本文件记录项目升级过程中每天完成的内容，**掰碎了讲解**每个改动的目的、原理和用法，供你学习与复习。
> 每天更新一节。格式：`D{n}: {当天主题}`。

---

## D1：工程化基座 —— pyproject.toml、统一日志、测试骨架

### 0. 今天做了什么（一句话总结）

把项目从"散装脚本 + print 调试"升级为"**统一依赖管理 + 结构化日志 + 自动化测试**"的工程化形态，为后续所有升级打好地基。

今天共改动/新建 12 个文件：

| 文件 | 类型 | 作用 |
|---|---|---|
| `pyproject.toml` | 新建 | 统一依赖清单与 pytest/ruff 配置 |
| `qa/logging_setup.py` | 新建 | 基于 loguru 的统一日志配置模块 |
| `qa/config.py` | 修改 | 新增 `LOG_LEVEL` / `LOG_FILE`，导入时初始化日志 |
| `qa/data_fallback.py` | 修改 | `print` → `logger.info` |
| `qa/answer_engine.py` | 修改 | `traceback.print_exc()` → 结构化日志 |
| `qa/kg_client.py` | 修改 | 同上（7 处） |
| `qa/llm_client.py` | 修改 | 同上（1 处） |
| `qa/.env.example`、`.env.example` | 修改 | 补充日志相关环境变量说明 |
| `qa/requirements.txt` | 修改 | 加注释说明以 pyproject 为准，补 loguru |
| `.gitignore` | 修改 | 忽略 `.pytest_cache/` |
| `tests/`（6 个文件） | 新建 | pytest 测试骨架 |

测试结果：**61 个测试全部通过**（`python -m pytest -q` → `61 passed`）。

---

### 1. pyproject.toml —— 为什么需要它？

#### 1.1 背景：requirements.txt 的问题

之前项目用 `qa/requirements.txt` 管理依赖。它能用，但有两个痛点：

1. **只有依赖列表，没有"配置"能力**——pytest 该怎么找测试、项目根目录要不要进 `sys.path`、代码风格规范，全都无处安放；
2. **入口分散**——每个子目录（qa/、graph/）各自为政，装依赖要到处找文件。

`pyproject.toml` 是 PEP 621 定义的**项目元数据标准文件**，一个文件同时承载：依赖清单、打包配置、工具配置（pytest/ruff/black 等），是现代 Python 工程的事实标准。

#### 1.2 逐段拆解我们的 pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```
这一段告诉 pip："这个项目用什么工具构建"。我们用的是 setuptools（Python 最传统的打包工具）。**平时不需要关心它**，pip 自动处理。

```toml
[project]
name = "newenergykg"
version = "0.2.0"
description = "新能源领域知识图谱与 Agentic RAG 智能问答系统"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [ ... ]
```
- `name` / `version` / `description`：项目身份信息；
- `readme = "README.md"`：打包时自动附带 README；
- `requires-python = ">=3.10"`：**最低 Python 版本**。注意：你的代码里用了 `str | None`（Python 3.10 语法），所以不能写 README 里说的 3.6，这里按真实代码修正；
- `dependencies`：运行时依赖，内容来自原来的 requirements.txt，**新增了 `loguru>=0.7.0`**。

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=5.0"]
```
"可选依赖"：只有开发/测试才需要的包。安装命令是 `pip install -e ".[dev]"`（`-e` 表示可编辑安装，改代码即时生效）。

```toml
[tool.setuptools.packages.find]
include = ["qa*"]
```
告诉 setuptools："我要打包 `qa` 这个包"（graph/、data/ 是脚本目录不是包，不打包）。

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra"
```
这是 **pytest 的配置段**（工具名 `pytest`，配置名 `ini_options`）：
- `testpaths`：pytest 去哪里找测试；
- `pythonpath = ["."]`：**把项目根目录加入 sys.path**。这样测试里 `import qa` 不需要任何 sys.path 补丁（以前 `evaluate.py` 里要 `sys.path.insert(0, ...)` 就是干这个的）；
- `addopts = "-ra"`：测试结束后汇总显示每个用例的结果符号。

```toml
[tool.ruff]
line-length = 100
target-version = "py310"
```
ruff 配置（代码规范工具，可选安装）。`E/F/I` 三类规则：E 是风格错误、F 是逻辑错误（如未使用的导入）、I 是 import 排序。

#### 1.3 怎么用

```bash
# 安装运行时依赖
pip install -e .

# 安装开发依赖（含 pytest）
pip install -e ".[dev]"

# 跑测试（等价于 python -m pytest）
pytest
```

---

### 2. loguru 日志 —— 为什么和怎么用

#### 2.1 背景：print 的问题

之前项目用 `print()` 输出调试信息。print 的三个问题：
1. **无法分级**——调试信息、正常信息、错误信息混在一起，刷屏时找不到关键行；
2. **无法定位**——不知道是哪一行打的；
3. **无法记录异常堆栈**——`except` 里想打印完整错误只能靠 `traceback.print_exc()`，代码很啰嗦。

#### 2.2 loguru 是什么

loguru 是一个"开箱即用"的 Python 日志库。核心心智：**全项目共用一个全局 `logger` 对象**，谁要用直接：

```python
from loguru import logger

logger.debug("调试")
logger.info("正常")
logger.warning("警告")
logger.error("错误")
logger.exception("出错了")   # 自动附带完整异常堆栈
```

对比标准库 logging 需要配置 Handler/Formatter/Logger 一大堆，loguru 一行 `logger.add(...)` 就完成配置。

#### 2.3 我们新建的 qa/logging_setup.py

```python
DEFAULT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
```
这是**日志输出格式模板**，含义：`时间 | 级别 | 模块:函数:行号 - 消息`。`<green>`、`<level>` 是 loguru 的颜色标记（终端友好）。

```python
def setup_logging(level="INFO", log_file=None, rotation="10 MB", retention="7 days"):
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = "INFO"

    logger.remove()          # 1. 移除 loguru 自带的默认 handler
    logger.add(sys.stderr, level=level, format=DEFAULT_FORMAT, colorize=True)  # 2. 输出到控制台
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(str(log_file), level=level, format=DEFAULT_FORMAT,
                   rotation=rotation, retention=retention, encoding="utf-8")   # 3. 可选输出到文件
    logger.debug(f"日志系统已初始化: level={level}, file={log_file}")
```

逐句理解：
- `logger.remove()`：loguru 默认自带一个输出到 stderr 的 handler，先移除它，避免后面重复添加导致一条日志打两遍；
- `logger.add(sys.stderr, ...)`：添加我们的控制台 handler，指定级别和格式；
- `logger.add(文件路径, ...)`：可选的文件 handler。`rotation="10 MB"` 表示文件超过 10MB 自动滚动新文件，`retention="7 days"` 保留 7 天——**生产环境日志不会无限膨胀**；
- `mkdir(parents=True, exist_ok=True)`：日志目录不存在就自动创建。

#### 2.4 qa/config.py 的改造（重点）

日志配置要在**程序一启动就生效**，所以我们把初始化放在 `config.py`（几乎每个模块都导入它）：

```python
# ==================== 日志配置 ====================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "")

from qa.logging_setup import setup_logging  # noqa: E402

setup_logging(
    level=LOG_LEVEL,
    log_file=Path(LOG_FILE) if LOG_FILE else None,
)
```

几个细节：
- `LOG_LEVEL` / `LOG_FILE` 和项目其他配置一样走环境变量（默认 `INFO` / 空=只输出控制台），并已同步写进 `.env.example`；
- `from qa.logging_setup import setup_logging` 放在文件中部：因为**只要 config 被导入，日志就先配置好**，后面任何模块用 logger 时格式已经就绪；
- `# noqa: E402`：这是给 ruff 的注释，意思是"忽略 E402（模块级导入不在文件顶部）这条规则"——因为我们是有意放在这里的。

#### 2.5 对现有模块的日志化改造

| 文件 | 改前 | 改后 | 为什么 |
|---|---|---|---|
| `data_fallback.py` | `print(f"[LocalDataStore] 已加载...")` | `logger.info(...)` | 信息类输出用 info 级，可被级别过滤 |
| `answer_engine.py`（2 处） | `traceback.print_exc()` | `logger.opt(exception=True).debug("...")` | 带堆栈的调试日志 |
| `kg_client.py`（7 处） | `traceback.print_exc()` | 同上 | 统一风格 |
| `llm_client.py`（1 处） | `traceback.print_exc()` | 同上 | 统一风格 |

关于 `logger.opt(exception=True).debug(...)` 的解释：
- `logger.exception()` 等价于"ERROR 级别 + 完整堆栈"，但原代码**只有 DEBUG 模式才打印**堆栈；
- 为了保持原有行为（平时安静、`QA_DEBUG=true` 才输出），我们用 `logger.opt(exception=True).debug("消息")`：**级别是 DEBUG，但附带异常堆栈**。这样平时不刷屏，调试时信息完整。

> 💡 顺手清理：原来 `import traceback` 已经没有用处了（被删掉），保持代码干净。

> 🪟 Windows 小贴士：如果控制台里日志中文显示乱码，是终端代码页问题（GBK vs UTF-8），在终端执行 `chcp 65001` 即可，日志内容本身是正确的 UTF-8。

---

### 3. pytest 测试骨架 —— 核心知识

#### 3.1 pytest 的几个核心概念（先记住）

| 概念 | 是什么 |
|---|---|
| **测试文件** | 以 `test_` 开头的 `.py` 文件，pytest 会自动发现 |
| **测试函数** | 以 `test_` 开头的函数，pytest 自动收集并逐个执行 |
| **断言** | `assert 条件`，条件为假则测试失败并显示失败信息 |
| **fixture** | 测试的"前置准备"函数，pytest 自动注入；`tmp_path`、`monkeypatch` 是内置的 |
| **conftest.py** | 放共享 fixture 的文件，同目录及子目录的测试都能用 |

#### 3.2 tests/conftest.py —— 共享夹具

```python
@pytest.fixture
def sample_technologies():
    return json.loads(json.dumps(SAMPLE_TECHNOLOGIES))  # 深拷贝
```
- `sample_technologies`：给测试提供 3 条**与真实数据同构**的样例技术（磷酸铁锂电池/三元锂电池/全钒液流电池）。深拷贝是为了防止一个测试改坏了数据影响其他测试；
- `sample_data_path`：用 pytest 内置的 `tmp_path`（每个测试独立的临时目录）把样例写入 JSON 文件，返回路径。**这样测试完全不依赖真实 data/new_energy.json**，可移植、可重复；
- 关键代码：`LocalDataStore(data_path=...)`、`IntentClassifier(entity_names=...)` 都支持注入数据，这是当初设计好的接口，测试正是利用这一点。

#### 3.3 最精彩的部分：Fake Neo4j（mock 的核心思想）

`KGClient` 依赖真实 Neo4j 服务。**单元测试的原则是：被测代码的依赖用假对象替代**——我们只测自己的逻辑（查询构造、结果解析），不测 Neo4j 本身。

我们实现了一组"长得和 neo4j 驱动一样"的假对象：

```python
class FakeRecord:   # 模仿 neo4j.Record：支持 record["key"]、record.get()
class FakeResult:   # 模仿 neo4j.Result：提供 .single()
class FakeSession:  # 模仿 neo4j.Session：上下文管理器 + run(query, **params)
class FakeDriver:   # 模仿 neo4j.Driver：提供 .session()
```

调用链模拟：`driver.session()` → `session.run(query, **params)` → `result.single()` → `record["字段"]`。

```python
def make_kg_client(handler, password="test-password", uri="bolt://fake:7687"):
    from qa.kg_client import KGClient
    client = KGClient(uri=uri, user="neo4j", password=password)
    client.driver = FakeDriver(handler)   # 关键：替换真实驱动
    client._available = True
    return client
```

`handler` 是测试自己写的函数，扮演"数据库"：
```python
def ping_handler(query, params):
    if "ping" in query:                       # 健康检查查询
        return FakeResult([FakeRecord({"1": 1})])
    if "count(n)" in query:                   # 节点统计
        return FakeResult([FakeRecord({"cnt": 10})])
    ...
    raise AssertionError(f"测试未覆盖的查询: {query}")  # 意外查询立刻报错
```

这个设计的好处：**如果被测代码发了一条测试没预期的查询，测试会直接失败**，等于免费帮我们审查了 Cypher 构造逻辑。

#### 3.4 tests/test_kg_client.py —— 我们验证了什么

- **get_status**：未配置密码时客户端禁用（`available=False`）；伪驱动下能正确统计节点/关系数；
- **get_tech_overview**：验证返回字典的结构；验证 `_safe_list` 把数据库返回的 `None` 和 `[]` 都统一成 `[]`（这是真实系统里常见的脏数据处理）；
- **get_related**：`produced_by` 映射到中文名"生产企业"；未知关系类型直接返回 None 不查库；
- **compare_techs**：双技术对比的字段解析；
- **find_tech_by_name**：命中/未命中；
- **_NullKGClient**：KG 禁用时的空实现行为。

#### 3.5 其他测试文件一览

| 文件 | 验证内容 |
|---|---|
| `test_config.py` | 默认配置值；`get_llm_provider` 优先级（openai > dashscope > None）；数据文件校验 |
| `test_intent_classifier.py` | 8 种意图分类；实体五级匹配（精确/同义词/比较切分） |
| `test_prompt_and_memory.py` | Prompt 系统/用户提示词拼接；会话记忆轮数与截断 |
| `test_data_fallback.py` | 数据加载索引；chat/unknown/aggregate/list/compare 五类兜底回答 |
| `test_answer_engine.py` | 端到端降级路径：无 LLM 无 Neo4j 时走本地兜底，返回字段完整 |

`test_answer_engine.py` 里的 monkeypatch 值得单独讲：
```python
@pytest.fixture
def no_llm_engine(sample_data_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_PATH", sample_data_path)      # 数据指向样例
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")            # 假装没有 LLM Key
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_BASE", "")
    return AnswerEngine(enable_kg=False)                            # 禁用 KG
```
`monkeypatch.setattr` 的作用是"**临时修改某个对象的属性，测试结束后自动还原**"。这里把 config 模块的属性改成测试值，让 `AnswerEngine` 走"无 LLM + 无 KG"的兜底路径——不用花一分钱 API 费用，就能端到端验证引擎的完整链路。

#### 3.6 运行测试

```bash
cd 项目根目录
python -m pytest -q          # 安静模式，只看结果
python -m pytest             # 详细模式
python -m pytest tests/test_kg_client.py -k overview   # 只跑名字含 overview 的用例
```

今天的结果：`61 passed in 1.36s`。

---

### 4. 今天的关键收获（面试/简历可用）

1. **依赖管理**：能从 `requirements.txt` 讲到 `pyproject.toml`（PEP 621、可选依赖、工具配置一体化）；
2. **测试思维**：能解释"单元测试不依赖外部服务，用 Fake/Mock 替代依赖"——这是所有后端岗位都会问的；
3. **日志思维**：能解释"为什么用结构化日志替代 print"（分级、定位、堆栈、文件轮转）；
4. **工程闭环**：`pip install -e ".[dev]"` + `pytest` 一条命令验证全项目，是 CI（第三周）的前置。

### 5. 明日预告（D2）

Docker Compose 骨架：`docker-compose.yml` + `Dockerfile`，把 neo4j / qdrant / redis / api 一键编排起来——为第三周"一键部署"打基础。

---

## D2：Docker Compose 全栈骨架

### 0. 今天做了什么（一句话总结）

为项目建立"一键启动"的容器化骨架：**4 个服务（Neo4j + Qdrant + Redis + 问答 API）通过 docker compose 一条命令全部拉起**，并解决了数据持久化、服务健康检查、敏感配置注入三个工程问题。

今天新建 3 个文件：

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 定义 API 服务镜像（如何构建出可运行的环境） |
| `docker-compose.yml` | 编排 4 个服务（镜像、端口、依赖顺序、数据卷、配置注入） |
| `.dockerignore` | 构建时排除的文件清单（敏感配置绝不进镜像） |

本机验证结果：`docker-compose.yml` YAML 语法校验通过、4 个服务结构正确；`pip install --dry-run .` 验证了 Dockerfile 依赖的打包路径可正常构建（`Would install newenergykg-0.2.0`）。**注意：当前开发环境没有 Docker，真正启动镜像需要在你本机安装 Docker Desktop 后执行**（见第 5 节命令）。

### 1. 前置概念：镜像 / 容器 / Compose

| 概念 | 一句话解释 | 类比 |
|---|---|---|
| **镜像 (image)** | 只读的应用模板：代码 + Python 环境 + 依赖，一次构建处处运行 | 安装光盘 |
| **容器 (container)** | 镜像运行起来的实例，互相隔离，可启停 | 用光盘装好的电脑 |
| **Dockerfile** | 描述"如何构建镜像"的脚本 | 光盘的制作说明书 |
| **docker compose** | 编排多个容器的工具：一条命令启动整套系统 | 机房的总控台 |

为什么项目要容器化？
1. **环境一致**：Neo4j/Qdrant/Redis 这些中间件不用你手动装，镜像里都配好了；
2. **一键启动**：`docker compose up` 搞定 4 个服务，同学/面试官要跑你的项目不再需要踩环境坑；
3. **可复现**：镜像内容固定，换台电脑结果一样。

### 2. Dockerfile 逐段拆解

```dockerfile
FROM python:3.11-slim
```
基础镜像：官方 Python 3.11 精简版（Debian slim）。选 3.11 是因为项目代码要求 `>=3.10`，且 3.11 比 3.13 兼容性更稳。

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
```
两个 Python 运行环境变量：
- `PYTHONDONTWRITEBYTECODE=1`：不生成 `__pycache__`，减小镜像体积；
- `PYTHONUNBUFFERED=1`：stdout 不缓冲，`docker logs` 能实时看到日志（不然日志会攒到进程退出才输出）。

```dockerfile
WORKDIR /app
```
容器内的工作目录，后续命令都在这里执行。

**这一段是整个 Dockerfile 的精髓（层缓存）：**

```dockerfile
COPY pyproject.toml README.md ./
RUN mkdir -p qa && touch qa/__init__.py \
    && pip install --no-cache-dir .
```

Docker 构建是按"行"缓存分层的：**只要某一层的内容没变，这一层及其依赖层就不会重新执行**。所以：
- 把依赖清单（pyproject.toml）单独先复制 → 安装依赖；
- 以后你**只改代码**，依赖层缓存命中，构建只需要几秒；如果清单和代码一起复制，那每次改代码都会重新下载全部依赖（几分钟）。

`mkdir -p qa && touch qa/__init__.py` 是技巧：`pip install .` 需要能"发现" qa 包才能完成安装，先放一个空的 `__init__.py` 让它发现，真正代码在下一层才复制进来。

```dockerfile
COPY qa ./qa
COPY data ./data
COPY static ./static
```
复制应用代码、数据、前端页面。

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"
```
健康检查：每 30 秒请求一次 `/health`，连续 3 次失败视为容器不健康。这个接口我们项目里**本来就有**（`qa/main.py` 里的 `GET /health`），直接复用，这就是"先有健康检查接口、后有容器化"的顺畅衔接。

```dockerfile
CMD ["python", "-m", "qa.main"]
```
容器启动时执行的命令，和本地开发完全一致。

### 3. docker-compose.yml 逐服务拆解

#### 3.1 全局心智：Compose 文件里"服务名 = 容器间主机名"

```yaml
services:
  neo4j:
  qdrant:
  redis:
  api:
```
4 个服务在同一张 Docker 网络里。**api 容器访问 Neo4j 用 `bolt://neo4j:7687`，而不是 `bolt://localhost:7687`**——因为每个容器有自己独立的 localhost。这是容器化最常见的坑，我们在 api 的环境变量里已经写对了：

```yaml
NEO4J_URI: bolt://neo4j:7687
```

#### 3.2 neo4j 服务

```yaml
neo4j:
  image: neo4j:5-community
  environment:
    NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:?请在 .env 中设置 NEO4J_PASSWORD}
    NEO4J_PASSWORD: ${NEO4J_PASSWORD:?请在 .env 中设置 NEO4J_PASSWORD}
  ports:
    - "7474:7474"   # 管理页面
    - "7687:7687"   # 应用连接
  volumes:
    - neo4j_data:/data
  healthcheck:
    test: ["CMD-SHELL", "cypher-shell -u neo4j -p \"$$NEO4J_PASSWORD\" 'RETURN 1' >/dev/null 2>&1 || exit 1"]
```

知识点拆解：
- **`${NEO4J_PASSWORD:?错误提示}`**：环境变量插值。compose 会自动读取项目根目录的 `.env` 文件；如果变量没设置，启动直接报错并给出提示——**防止用户用默认密码裸奔**；
- **`NEO4J_AUTH: neo4j/密码`**：Neo4j 官方镜像的初始化变量。⚠️ **只有首次初始化数据卷时生效**，之后改密码要删卷（`docker compose down -v`）或用 `neo4j-admin` 改；
- **端口映射 `宿主机端口:容器端口`**：宿主机 7474/7687 转发到容器；
- **数据卷 `neo4j_data:/data`**：容器删了数据还在（持久化）。三个服务都有对应卷；
- **健康检查里的 `$$NEO4J_PASSWORD`**：`$$` 是 compose 的转义，表示"这里不是 compose 变量，是容器内的 shell 变量"，最终容器里执行 `cypher-shell -u neo4j -p "$NEO4J_PASSWORD" 'RETURN 1'`；
- **`start_period: 30s`**：Neo4j 首次启动较慢，给 30 秒宽限期再开始计失败次数。

#### 3.3 qdrant / redis 服务

```yaml
qdrant:
  image: qdrant/qdrant:latest   # 开发用 latest；生产应固定版本号
  ports:
    - "6333:6333"   # REST API
    - "6334:6334"   # gRPC（可选）
  volumes:
    - qdrant_data:/qdrant/storage
```
Qdrant 是 D3 起要用的向量数据库。镜像里没内置 curl/python，**暂不设 healthcheck**，api 对它的依赖用 `service_started`（只保证启动、不保证就绪），D3 写摄取管道时会加"连接重试"兜底。

```yaml
redis:
  image: redis:7-alpine
  command: ["redis-server", "--appendonly", "yes"]   # 开启 AOF 持久化
  volumes:
    - redis_data:/data
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
```
Redis 只给容器内部用，**不映射宿主端口**（更安全，外部无法直连）。`redis-cli ping` 是 Redis 官方的健康检查标准姿势。

#### 3.4 api 服务（我们的应用）

```yaml
api:
  build: .            # 用当前目录的 Dockerfile 构建镜像
  environment:
    APP_HOST: 0.0.0.0 # 关键！
    ...
  ports:
    - "8000:8000"
  depends_on:
    neo4j:
      condition: service_healthy
    redis:
      condition: service_healthy
    qdrant:
      condition: service_started
  restart: unless-stopped
```

三个重点：
- **`APP_HOST: 0.0.0.0`**：我们 `config.py` 的默认值是 `127.0.0.1`，在容器里只监听 127.0.0.1 的话宿主机永远访问不到——必须监听 `0.0.0.0`（所有网卡）。这是"本地能跑、容器里 404"的经典原因；
- **`depends_on` + `condition: service_healthy`**：等 Neo4j/Redis 健康检查通过才启动 api，避免"api 先起、连不上数据库"的启动竞态；
- **`restart: unless-stopped`**：容器意外退出自动重启，除非你手动 stop。

#### 3.5 配置注入方式（为什么不 COPY .env）

api 的配置全部通过 `environment` 从宿主机 `.env` 插值注入（`${DASHSCOPE_API_KEY:-}` 表示"有则用，没有则空"）。**配置和代码分离**：镜像里不含任何密钥，密钥只在运行容器时注入——这是 12-Factor 应用的原则，也是 `.dockerignore` 里排除 `.env` 的原因。

### 4. .dockerignore

```dockerignore
.env
qa/.env
.venv/
__pycache__/
tests/
LEARNING_LOG.md
UPGRADE_PLAN.md
```

作用：`docker build` 会把"构建上下文"（整个目录）发给 Docker 守护进程。**如果不排除，`.env`（含密码/API Key）会被打进镜像**，任何人拿到镜像就能看到你的密钥。排除还能加快构建、减小镜像。

### 5. 怎么跑（在你本机）

```bash
# 1. 安装 Docker Desktop（Windows/Mac）并启动
# 2. 项目根目录准备 .env（复制 .env.example 并改 NEO4J_PASSWORD）
cp .env.example .env

# 3. 构建并后台启动全部服务（首次构建较慢，要下载镜像）
docker compose up -d --build

# 4. 查看状态（等 neo4j/redis 变成 healthy）
docker compose ps

# 5. 访问
#    API 文档    http://127.0.0.1:8000/docs
#    Neo4j 管理  http://127.0.0.1:7474   （用户名 neo4j / 密码见 .env）
#    Qdrant 面板 http://127.0.0.1:6333/dashboard

# 6. 看日志 / 停止 / 彻底清除
docker compose logs -f api
docker compose down          # 保留数据
docker compose down -v       # 连数据卷一起删（改 Neo4j 密码时需要）
```

### 6. 关键收获（面试/简历可用）

1. **层缓存**：能讲清"为什么先复制依赖清单再复制代码"（构建加速）；
2. **服务编排**：`depends_on` 的两种语义（started vs healthy），启动竞态的解法；
3. **配置注入**：密钥不进镜像（.dockerignore + 环境变量注入）；
4. **持久化**：命名卷让数据库数据跨容器生命周期存活；
5. **容器网络**：服务名即主机名、`0.0.0.0` 监听、端口映射三个概念能串起来讲。

### 7. 明日预告（D3）

文档摄取管道：`qa/ingestion/`（parser / chunker / embedder），把 PDF/Markdown 解析、切分、向量化并写入 Qdrant——RAG 的"知识生产"环节正式开工。

---

## D3：文档摄取管道（RAG 的"知识生产"）

### 0. 今天做了什么（一句话总结）

建成了 RAG 的第一条腿——**摄取管道**：任意 Markdown/TXT/PDF 文档，经过"解析 → 语义切分 → 向量化 → 写入 Qdrant"四步，变成可被向量检索的"知识库"。

新增文件：

| 文件 | 作用 |
|---|---|
| `qa/ingestion/__init__.py` | 包导出 |
| `qa/ingestion/parser.py` | 文档解析：统一成"标题块 + 段落块" |
| `qa/ingestion/chunker.py` | 语义切分：按标题层级组织 + 贪心合并 + overlap |
| `qa/ingestion/embedder.py` | 向量化：DashScope 真实实现 + Debug 离线实现 |
| `qa/ingestion/qdrant_store.py` | Qdrant 封装：建集/幂等写入/向量检索 |
| `qa/ingestion/run.py` | 命令行入口 `python -m qa.ingestion.run` |
| `docs/sample_new_energy.md` | 示例测试文档（可当真实知识导入） |
| `tests/test_ingestion.py` | 28 个新测试 |

测试结果：**89 passed**（61 + 28 新增）。端到端离线演示也跑通：示例文档 → 18 块 → 8 个 chunk → 入库 → 检索"全钒液流电池适合什么场景？"命中"储能技术 / 液流电池储能"章节（相似度 0.817）。

### 1. 摄取管道全景（先建立心智模型）

```
文档(PDF/MD/TXT)
   │ ① parser：解析成块（标题/段落）
   ▼
Block 列表
   │ ② chunker：按标题组织 + 贪心合并，切出带上下文的 chunk
   ▼
Chunk 列表（text + metadata: doc_id/source/section/序号）
   │ ③ embedder：每段文本 → 一串浮点数（向量）
   ▼
向量列表
   │ ④ qdrant_store：写入 Qdrant（向量 + 原文 + 元数据）
   ▼
向量库（供后续检索）
```

**为什么 RAG 必须有这一步？** LLM 只"记得"训练时见过的东西，你要让助手回答"你私有文档/新增知识"的问题，就必须先把这些知识**切块、向量化、存进检索库**——回答时先检索再生成。摄取管道质量直接决定 RAG 上限。

### 2. parser.py —— 解析成统一中间表示

核心数据结构（`dataclass`，Python 标准库的"轻量类"）：

```python
@dataclass
class Block:
    type: str    # "heading" 标题 或 "paragraph" 段落
    text: str
    level: int   # 标题层级 1-6；段落恒为 0
```

解析规则：
- **Markdown**：`^#{1,6}` 开头的行是标题（记录层级）；**空行分隔段落**；段落内的连续行（列表、表格行）用换行拼成一个段落块；
- **纯文本**：只按空行分段落，不猜标题（避免把正文误判成标题）；
- **PDF**：pypdf 逐页抽文本后同样按空行分段落（PDF 无可靠标题结构，先不强行识别）。

```python
HEADING_RE = re.compile(r"^(\#{1,6})\s+(.+?)\s*#*\s*$")
```
正则拆解：`^`行首 → `(\#{1,6})`捕获 1~6 个井号（第 1 组=层级）→ `\s+`至少一个空格 → `(.+?)`非贪婪捕获标题文字 → 允许行尾 `#`（Markdown 的闭合井号）→ `$`行尾。

**设计原则：解析只出"块"，不负责"切块"**——职责单一，每层都能独立测试。这就是工程里常说的"单一职责"。

### 3. chunker.py —— 语义切分（本日重点，面试必问）

#### 3.1 为什么切分质量很重要

检索是"**拿用户的问题去和库里的每一段比相似度**"。如果一段塞进 2000 字，用户问其中一个小点，整段的平均相似度会被稀释；如果一段只有 30 字，又缺少上下文（"它"指代什么不知道）。**切块 = 在"信息完整"和"语义聚焦"之间找平衡**。

#### 3.2 我们的策略（两层）

**第一层：按标题组织（heading-aware）**——比"无脑按字数切"高级的地方：

```python
def _update_heading_stack(stack, level, title):
    while stack and stack[-1][0] >= level:
        stack.pop()          # 遇到同级/上级标题，弹出栈顶
    stack.append((level, title))
```

维护一个"标题栈"模拟文档层级：
- 遇到 `# 光伏` → 栈 `[(1, 光伏)]`
- 遇到 `## 晶硅电池`（level 2 > 1）→ 压栈 `[(1, 光伏), (2, 晶硅电池)]`
- 再遇到 `## 薄膜电池`（level 2 ≤ 栈顶 2）→ 先弹出 `(2, 晶硅电池)` 再压入 → `[(1, 光伏), (2, 薄膜电池)]`

每个段落归入"最近的标题"名下，最终每个 chunk 的 metadata 记录完整标题路径（`光伏 / 晶硅电池`），**回答时可以拿它当引用出处**。

**第二层：贪心合并 + overlap**——一个标题下内容太长时：

```python
def _greedy_chunk_paragraphs(paragraphs, chunk_size, overlap):
    ...
    if len(buffer) + 1 + len(para) <= chunk_size:
        buffer = buffer + "\n" + para   # 装得下就拼
    else:
        tail = _tail(buffer, overlap)   # 装不下：取旧块尾部做"记忆"
        chunks.append(buffer)
        buffer = (tail + "\n" + para) if tail else para
```

- 段落能拼进目标长度（600 字）就拼，让一个 chunk 尽量是一个完整话题；
- 切出的相邻块**首尾重叠 80 字**：被切在两块边界的话题，在下一块开头仍有"上一块的尾巴"兜底，检索时不至于丢掉半个句子；
- 单个超长段落（如一大段表格文字）用固定窗口二次切分，窗口步长 = 块长 − overlap。

### 4. embedder.py —— 文本向量化

**向量（embedding）**：把一段文字变成一串浮点数，使"语义相近的文本数值相近"。切好的 chunk 要转成向量才能做相似度检索。

两个实现 + 工厂：

| 实现 | 何时用 | 原理 |
|---|---|---|
| `DashScopeEmbedder` | 配置了 API Key（真实场景） | 调阿里云 text-embedding-v3，1024 维 |
| `DebugHashEmbedder` | 无 Key / 测试 / CI | 本地"n-gram 特征哈希 + L2 归一化"，确定性伪向量 |

```python
def create_embedder():
    if config.DASHSCOPE_API_KEY:
        return DashScopeEmbedder(model=config.EMBEDDING_MODEL)
    logger.warning("未配置 DASHSCOPE_API_KEY，使用 DebugHashEmbedder ...")
    return DebugHashEmbedder(dimension=config.EMBEDDING_DIMENSION)
```

**为什么需要 Debug 实现？** 单元测试和 CI 不能依赖外部付费 API；有了它，整条"摄取→入库→检索"链路在没 Key 时也能验证（质量低但流程对）。这是"可测试性设计"的体现。

Debug 向量原理（可讲的细节）：
- 提取文本的**字符 1~2 gram 特征**（如"磷酸铁锂"拆出"磷酸""酸铁""铁锂"…）；
- 每个 gram 用 **MD5 哈希**映射到 1024 个桶之一并累加权重 → 两段文本共享的 n-gram 越多，向量越接近；
- **L2 归一化**（向量长度=1）：让"余弦相似度"和"点积"等价，统一检索口径。

DashScopeEmbedder 两个工程细节：
- **延迟导入**：`from dashscope import TextEmbedding` 放在方法内部——没装 SDK 时创建对象不报错，便于分层测试；
- **批量调用**：`embed()` 按 `EMBED_BATCH_SIZE=10` 分批，避免一次请求塞过多文本。

### 5. qdrant_store.py —— 向量库读写

**Qdrant 基本概念**：
- **collection（集合）**：一张"向量表"，创建时声明向量维度与距离度量（我们用**余弦距离**）；
- **point（点）**：集合里的一条记录 = 向量 + payload（原文与元数据）；
- **检索**：拿查询向量和所有点算相似度，返回 Top-K。

```python
def ensure_collection(self, dimension, recreate=False):
    # 集合不存在才创建（或 recreate=True 时删了重建）
```

**幂等写入（本日工程亮点）**：

```python
@staticmethod
def _point_id(doc_id, chunk_index):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{chunk_index}"))
```

- `doc_id` 是**文件内容的 SHA-256**（`run.py::compute_doc_id`），内容变了 id 才变；
- point id = `uuid5(doc_id + chunk_index)`：**同一文档同一序号永远映射到同一个 point id**；
- 因此"重新导入同一文档"是**覆盖写而非堆积**——测试 `test_upsert_idempotent` 验证了重复导入后 `count()` 不变。

**新旧版本兼容**：新版 qdrant-client（1.19）移除了 `.search()`，我们探测 `hasattr(self.client, "query_points")` 决定走哪条 API——让你的代码在不同版本上都能跑的小技巧。

### 6. run.py —— 命令行入口

```bash
# 在本地（需先启动 Qdrant：docker compose up -d qdrant）
python -m qa.ingestion.run --input docs/sample_new_energy.md
python -m qa.ingestion.run --input docs/                 # 整个目录
python -m qa.ingestion.run --input docs/ --recreate      # 清空重建集合
python -m qa.ingestion.run --input docs/ --limit 2       # 试跑前 2 个
```

`ingest_path()` 是**可测试的核心函数**（测试注入内存 Qdrant + Debug embedder 就能跑全链路），CLI 只是它的薄壳——再次体现"逻辑与 IO 分离"。

### 7. 配置新增（qa/config.py）

```python
EMBEDDING_MODEL     # text-embedding-v3（DashScope）
EMBEDDING_DIMENSION # 1024
EMBED_BATCH_SIZE    # 10
QDRANT_URL          # 默认 http://localhost:6333（compose 里 QDRANT_HOST=qdrant 覆盖）
QDRANT_COLLECTION   # newenergy_docs
CHUNK_SIZE / CHUNK_OVERLAP  # 600 / 80
```
全部可从环境变量覆盖，`.env.example` 已同步。注意 `QDRANT_URL` 的推导逻辑：`QDRANT_HOST` 默认 `localhost`，在 docker-compose 中 api 服务设置了 `QDRANT_HOST=qdrant`，同一份代码本地/容器都能跑。

### 8. 关键收获（面试/简历可用）

1. **RAG 全链路**：能讲清"摄取为什么是 RAG 的前提"以及四步管道；
2. **切分策略**：heading-aware + 贪心合并 + overlap，能回答"为什么不是固定长度切"；
3. **可测试设计**：Debug Embedder + 内存 Qdrant + 纯函数核心，无外部依赖也能全链路验证；
4. **幂等设计**：内容哈希 doc_id + uuid5 point id，重复导入不产生脏数据；
5. **数据溯源**：每个 chunk 带 source/section/chunk_index，为后续"回答引用出处"埋好伏笔（第三周前端引用卡片直接可用）。

### 9. 明日预告（D4 → D5）

按计划 D4 是评估与消融，但更自然的顺序是先把 **D5 的向量检索器**（`qa/retrieval/`：把图谱检索、向量检索、BM25 统一成 Retriever 接口）做出来——否则向量库里有了数据却还没有"问答侧"去用。实际推进时我会把计划微调为：下一步做检索器 + 混合检索，再回头做评估闭环，保证每一步都可演示。

---

## D4：统一检索层（多路召回 + RRF 混合）

### 0. 今天做了什么（一句话总结）

把"取知识"这一步做成了**统一检索层**：图谱（KG）、语义（向量）、关键词（BM25）三路检索收敛到同一个接口，再用 **RRF（倒数排名融合）** 混合成一路结果——这是 RAG 工程"多路召回"的标准姿势。

新增文件：

| 文件 | 作用 |
|---|---|
| `qa/retrieval/base.py` | 统一接口 `Retriever` + 统一结果 `RetrievalResult` |
| `qa/retrieval/bm25.py` | 纯 Python BM25 全文索引与检索器 |
| `qa/retrieval/vector_retriever.py` | 把 QdrantStore + Embedder 包装成检索器 |
| `qa/retrieval/kg_retriever.py` | 把 Neo4j 图谱查询包装成检索器 |
| `qa/retrieval/hybrid.py` | RRF 融合 + HybridRetriever 编排 |
| `qa/retrieval/factory.py` | 按环境可用性自动组装默认管线 |
| `qa/retrieval/cli.py` | 演示入口 `python -m qa.retrieval.cli` |
| `tests/test_retrieval.py` | 19 个新测试 |

配套修改：`QdrantStore` 新增 `fetch_all()`（分页拉取全部 chunk，供 BM25 建索引）。

测试结果：**108 passed**。演示效果：同一份知识被 vector + bm25 同时命中时合并为一条（标记 `[vector+bm25]`），RRF 分 0.0328，明显高于只被单路命中的 0.0164。

### 1. 为什么需要"统一接口"

D3 结束时，向量库能查了、图谱本来就能查，但它们是**两套完全不同的 API**：一个返回 Qdrant 的 ScoredPoint，一个返回结构化 dict。问答引擎要同时用它们，就得写一堆 if/else。

统一接口的价值：

```python
class Retriever(ABC):
    def retrieve(self, query: str, top_k: int = 10) -> List[RetrievalResult]

@dataclass
class RetrievalResult:
    text: str       # 可读文本（展示 / 注入 Prompt）
    score: float    # 该来源的相关性分数
    source: str     # kg | vector | bm25
    doc_id: str     # 文档 ID（溯源）
    section: str    # 章节路径（溯源）
    extra: dict     # 来源特有信息
```

上层只认识一种类型，至于"哪一路检索器、底层是 Cypher 还是向量库"，对上层透明。这为第三周把检索包装成 Agent 工具打好了地基。

### 2. 各路检索器

#### 2.1 VectorRetriever（语义检索）

```python
def retrieve(self, query, top_k=10):
    query_vector = self.embedder.embed_one(query)   # 问题 → 向量
    hits = self.store.search(query_vector, top_k=top_k)  # 库内找最相似的 chunk
```
用户问"电池龙头是谁"，即使文档里没出现"龙头"二字，只要语义相近就能召回。调试中发现并修掉了一个真 bug：`QdrantStore.search` 返回 dict 列表（`{"id","score","payload"}`），而最初写的 VectorRetriever 按对象属性 `hit.payload` 访问——**这就是"接口契约不一致"的典型 bug**，单元测试立刻抓到了它。

#### 2.2 BM25Retriever（关键词检索）

向量检索的盲区是**精确词**："PERC"、"2024"、"全钒液流电池"这种专有名词，语义向量可能把它和别的技术混在一起；BM25 是经典的关键词加权算法，精确命中时毫不含糊。

BM25 打分公式（面试常考，必须能默写）：
```
score(d, q) = Σ_{qi} IDF(qi) × f(qi,d)×(k1+1) / (f(qi,d) + k1×(1-b + b×|d|/avgdl))
IDF(qi) = ln(1 + (N - n(qi) + 0.5) / (n(qi) + 0.5))
```
- **词频 f(qi,d)**：词在文档里出现次数；
- **词频饱和 (k1)**：出现 5 次和 20 次的差距被压缩，防止长文档刷词作弊；
- **文档长度惩罚 (b)**：同样含关键词，短文比长文更"专一"，得分更高；
- **IDF 逆文档频率**：满篇都是的"技术/公司"权重低，罕见的"钒液流"权重高。

实现里有两个工程点：分词用 jieba 的**搜索引擎模式** `cut_for_search`（能切出更细的 token 提高召回）；英文转小写（`PERC` → `perc`，保证 "PERC" 和 "perc" 是同一个词）。

**BM25 的数据从哪来？** 新加的 `QdrantStore.fetch_all()` 把集合里所有 chunk 的原文分页拉进内存建索引。首次检索稍慢，换来的是与向量检索**同一份语料**——多路召回必须作用在同一批知识上才有意义。

#### 2.3 KGRetriever（图谱检索）

把 D1 之前 AnswerEngine 里的图谱查询逻辑搬进统一接口：实体识别（复用 IntentClassifier）→ 按意图分发到 `get_tech_overview` / `get_related` / `compare_techs` → 各自格式化成可读文本的 `RetrievalResult`。

一个真实教训（写测试时踩到）：测试用的 Fake handler 用 `"produced_by" in query` 判断关系查询，但 **overview 的 Cypher 里同样含有 `[:produced_by]->`**，导致概览查询被错误拦截。修复：改用各查询独有的**返回字段**做判别（`AS items` / `AS desc` / `AS desc1`）。这也提醒我们：写 Fake 时要模拟真实查询的"指纹"，不能想当然。

### 3. RRF —— 混合检索的核心算法

#### 3.1 为什么不能用"分数相加"

三路检索的分数**量纲不同**：余弦相似度在 [-1,1]，BM25 分数可能上千，KG 命中是自定义分——直接加权相加没有意义。

#### 3.2 RRF 的做法（公式很简单）

```
RRF_score(d) = Σ_{第r路} w_r / (k + rank_r(d))
```
`rank_r(d)` 是文档 d 在第 r 路的**排名**（第 1 名、第 2 名…），k=60 是平滑常数。

核心思想：**放弃分数，只看排名**。"排名"是相对量，天然可比——不管哪一路，第 1 名就是该路最相关的。某份知识被多路同时排进前列，RRF 分就会叠加升高。

以本项目演示数据为例：一份"液流电池"chunk 同时被向量路排第 1、BM25 路排第 1 → `1/61 + 1/61 = 0.0328`；只被单路命中的 chunk → `1/61 = 0.0164`。**多路共识 = 更高置信度**，这正是混合检索优于单路的地方。

#### 3.3 跨源去重（本日工程优化点）

第一版实现里，同一 chunk 被 vector 和 bm25 同时命中会出现**两条重复结果**。修复：`result_key` 对文档类结果用 `(doc_id, chunk_index)` 作身份键（**与 source 无关**），RRF 融合时同键合并、并记录 `extra["sources"]` 溯源列表：

```
[vector+bm25] rrf=0.0328 | 液流电池储能 | 液流电池通过电解液中活性物质…
```

### 4. factory.py —— 能装什么装什么

```python
def build_default_pipeline() -> HybridRetriever:
    # Neo4j 连上了 → 加 KG 检索器
    # Qdrant 集合有数据 → 加 向量 + BM25 检索器
    # 什么都没有 → 返回空管线并给提示
```
**优雅降级**：用户只配了 Neo4j、没跑 Qdrant，系统照常工作（只少一路）；什么都不配也有明确报错提示。不因为某个依赖挂了就让整个系统崩溃——这是工程里重要的容错思维。

### 5. 怎么跑（在你本机）

```bash
# 前提：Neo4j 已连 或 已用 D3 摄取过文档（docker compose up -d 后执行摄取）

python -m qa.ingestion.run --input docs/          # 先把示例文档入库（如还没做）
python -m qa.retrieval.cli --query "全钒液流电池适合什么场景？" --top-k 5
```

### 6. 关键收获（面试/简历可用）

1. **多路召回**：能讲清"为什么三路检索互补"（语义 vs 关键词 vs 结构化事实）；
2. **RRF 原理**：能默写公式、解释"为什么用排名而非分数"、k 的作用；
3. **接口设计**：统一 Retriever/RetrievalResult 让上层与来源解耦；
4. **去重与溯源**：跨源合并同一知识、记录 sources，回答时可标注"依据来源 A+B"；
5. **降级容错**：检索管线按可用性自动组装。

### 7. 下一步

检索层就绪后，下一步把它**接回问答引擎**：回答问题 = 混合检索出的知识 + LLM 生成（引用来源），并引入查询改写与重排；然后做评估闭环（RAGAS + 消融实验）验证"混合 > 单路"。

---

## D5：问答引擎接入混合检索 + 引用溯源

### 0. 今天做了什么（一句话总结）

把 D4 的检索层**真正接进 AnswerEngine**：现在回答一个问题会经历"图谱结构化检索 + 文档混合检索（向量+BM25）→ 把参考资料编号注入 Prompt → LLM 生成可溯源回答"，响应里带 `references` 字段，为前端"引用卡片"铺路。

改动文件：

| 文件 | 改动 |
|---|---|
| `qa/answer_engine.py` | `__init__` 新增 `enable_documents`/`document_retriever` 参数；新增 `_retrieve_documents()`；`answer()` 注入参考资料；`get_status()` 报告检索状态 |
| `qa/prompt_builder.py` | `build_user_prompt` 支持 `references` 参数（自动编号 [1][2]…） |
| `qa/retrieval/factory.py` | 新增 `try_build_document_retriever()`（仅向量+BM25，不含 KG） |
| `qa/config.py` + `.env.example` | 新增 `RETRIEVER_TOP_K`（默认 5） |
| `qa/main.py` | `QAResponse` 模型新增 `references` 字段 |
| `tests/` | 新增 FakeLLM 技术、桩检索器测试，共 **112 passed** |

### 1. 一个关键设计决策：引擎为什么只接"文档"检索？

有人会问：D4 不是有 KG 检索器吗，为什么混合管线不一起注入？

因为引擎**内部本来就有**结构化的图谱检索路径（`kg_context`，直接以 JSON 形式喂给 LLM，事实密度更高）。如果再把 KG 检索器的文本结果注入一遍，同一批图谱事实会在 Prompt 里出现两次——浪费 token、还可能让 LLM 困惑。

所以分工是：
- **图谱事实** → 走引擎原有结构化路径（`知识图谱数据：{...json...}`）；
- **文档知识** → 走新的混合检索路径（`参考资料：[1] 来源：xxx.md …`）。

这也是工厂里新增 `try_build_document_retriever()`（只装向量+BM25）而不是复用 `build_default_pipeline()`（含 KG）的原因。**做工程时要时刻想着"同一份知识会不会被重复注入"**——这是面试官喜欢追问的点。

### 2. AnswerEngine 的三层变更

#### 2.1 构造：可注入、可降级

```python
def __init__(self, enable_kg=True, enable_documents=True, document_retriever=None):
    ...
    self.document_retriever = document_retriever
    if self.document_retriever is None and enable_documents:
        try:
            from qa.retrieval.factory import try_build_document_retriever
            self.document_retriever = try_build_document_retriever()
        except Exception as e:
            self.document_retriever = None   # Qdrant 没起 → 优雅降级
```
- **测试注入**：测试传一个假检索器，不碰真实 Qdrant；
- **自动降级**：Qdrant 没启动时 `try_build_*` 内部捕获异常返回 None，引擎照常工作（只是少一路知识）——**任何可选依赖都不能让主流程崩溃**。

#### 2.2 检索参考资料

```python
def _retrieve_documents(self, question):
    results = self.document_retriever.retrieve(question, top_k=config.RETRIEVER_TOP_K)
    for r in results:
        sources = "+".join(r.extra.get("sources") or [r.source])  # "vector+bm25"
        references.append({text/source/section/doc_id/score})
```
注意把 D4 的跨源合并信息（`extra["sources"]`）还原成展示字符串 `vector+bm25`——溯源信息一路保留到最终响应。

#### 2.3 响应协议扩展

```python
return {
    ...原有字段...,
    "references": references,   # 每条含 text/source/section/doc_id/score
}
```
`qa/main.py` 的 Pydantic 模型同步加 `references: list = []`，前端可直接渲染引用卡片。**后端每加一个字段，API 契约（Pydantic 模型）必须同步**——这是 FastAPI 项目的纪律。

### 3. PromptBuilder：编号引用

```python
if references:
    lines.append("参考资料（若引用其中内容，请在对应句子后标注编号，如[1]）：")
    for i, ref in enumerate(references, start=1):
        head = f"[{i}] 来源：{ref['source']}（章节：{ref['section']}）"
        ...
```
用**自然语言指令 + 编号**约束 LLM："引用内容时在句末标注 [编号]"。模型不一定每次都听话，但大多数时候可行；且无论是否标注，`references` 字段都随响应返回，前端照样能展示"依据哪些资料"。

### 4. 测试技术：FakeLLM

之前测试只能覆盖"无 LLM 的兜底路径"；这次要让**LLM 路径**也进测试，方法是用一个"假 LLM"替换工厂函数：

```python
class FakeLLM:
    def __init__(self): self.calls = []
    def generate(self, system_prompt, user_prompt):
        self.calls.append(user_prompt)          # 捕获 Prompt！
        return {"content": "...", "model": "fake", ...}

monkeypatch.setattr("qa.answer_engine.create_llm_client", lambda: FakeLLM())
```

于是可以断言**"参考资料确实被写进了发给 LLM 的 Prompt"**——不是只测"函数没崩"，而是测行为是否符合预期。捕获输入/输出的假对象（Fake、Spy）是测试里的高级技巧：**Fake 让被测系统动起来，Spy 记下它做了什么**。

测试还暴露了两个真实问题并当场修复：
1. 注入的普通检索器没有 `retrievers` 属性 → `getattr` 兼容；
2. 简单 Retriever 的 status 来源显示其 `name`（hybrid）而非内部结果来源。

### 5. 离线演示结果

用真实引擎 + 内存 Qdrant（示例文档）+ FakeLLM 跑通：

```
问题：全钒液流电池适合什么场景？
回答：全钒液流电池循环寿命长，适合长时储能 [1]。
参考资料数：5
  [1] vector+bm25 | 液流电池储能 | 液流电池通过电解液中活性物质的价态变化…
  [2] vector+bm25 | 光伏…      …
```

### 6. 关键收获（面试/简历可用）

1. **引用溯源闭环**：检索结果 → 编号注入 → LLM 按编号引用 → 响应带完整出处，是产品级 RAG 的标志；
2. **防重复注入**：能讲清"为什么图谱知识走结构化、文档知识走文本、两者不混"；
3. **可测的 LLM 路径**：FakeLLM 捕获 Prompt 验证上下文注入，不花 API 费用；
4. **API 契约同步**：引擎返回字段 ↔ Pydantic 模型 ↔ 前端展示三者一致；
5. **优雅降级**：可选依赖（Qdrant）不可用不影响主流程。

### 7. 下一步

问答链路已经通了，但"混合检索到底比单路强多少？"还没有数据支撑——下一步做**评估闭环**：把 golden set 扩到 100 题、接入 RAGAS 指标（faithfulness / answer relevancy / context precision），并跑"纯图谱 / 纯向量 / 混合"消融实验，用数字证明升级价值（这也是简历上最有说服力的部分）。

---

## D6：检索评估与消融实验（LLM-free）

### 0. 今天做了什么（一句话总结）

建成了**检索质量评估闭环**：用现有 60 题 golden set + 可自动生成的章节评测集，对"纯向量 / 纯BM25 / 混合RRF"三套配置跑消融实验，输出 Recall@K / MRR / Precision@K 三指标对比报告——第一次用**数字**回答"混合检索值不值"。

新增文件：

| 文件 | 作用 |
|---|---|
| `qa/eval/__init__.py` | 包导出 |
| `qa/eval/dataset.py` | golden set 加载、chunk 自动生成评测条目、相关性判定 |
| `qa/eval/metrics.py` | Recall@K / MRR / Precision@K 指标实现 |
| `qa/eval/ablation.py` | 消融实验编排 + Markdown 报告 |
| `qa/eval/run.py` | CLI：`python -m qa.eval.run` |
| `qa/eval/ablation_demo_report.md` | 离线演示样例报告（标记了 Debug 伪向量） |
| `tests/test_eval.py` | 新增 11 个测试 |

测试结果：**123 passed**（112 + 11 新增）。

### 1. 为什么先做"LLM-free"的检索评估？

RAG 评估分两层：

| 层 | 回答的问题 | 需要 LLM 吗 |
|---|---|---|
| **检索质量**（本日） | 该捞的资料捞回来了吗？排得靠前吗？噪音多吗？ | ❌ 不需要（有 golden 标注即可） |
| **生成质量** | 回答忠实于资料吗（faithfulness）？答到点上了吗？ | ✅ 需要（RAGAS 那套，后接） |

检索层是 RAG 的地基：**资料都捞不回来，生成再强也是无米之炊**。而且检索质量可以用期望实体/章节做文本匹配离线判定——**不花 API 钱、可复现、跑得快**，是最适合先建立评估闭环的一层。

### 2. 三个指标（务必能讲清楚）

对每个查询，先让检索器返回 Top-K，再把每条结果与 golden 比对得到"是否相关"的布尔序列，然后：

```
Recall@K    : 前 K 名里有没有相关结果？(0/1) → 对全部查询取平均
MRR         : 第一个相关结果排第几？→ 1/rank，取平均
Precision@K : 前 K 名里有几条相关？→ 占比，取平均
```

一句话记忆：**"Recall 看漏没漏，MRR 看排得靠不靠前，Precision 看噪音多不多。"** 三个指标一起看才能描述全貌（比如 Recall 高但 Precision 低 = 都捞回来了但前面混了一堆无关的）。

### 3. 相关性判定（relevance judgment）——评估的地基

```python
def judge_result(result, entry):
    # 1) 条目声明了期望实体 → 结果文本/章节命中任一实体即相关
    # 2) 否则有期望章节 → 章节命中即相关
```

评测条目两个来源：
- **现有 60 题 golden set**（`qa/eval_dataset.json`，自带 `expected_entities`）——直接复用，没有重复造数据；
- **chunk 自动生成**：每个 chunk 生成"请介绍{章节}相关内容"并期望命中该章节——语料变大时评测集自动跟着长。

**必须诚实面对的局限**（面试官会问）：关键词判定的相关性是**近似**。比如"电池龙头是谁"的期望实体是"宁德时代"，如果某段资料没写全名只写了"CATL"，就会漏判。所以报告里明确标注：严格语义相关性需要 LLM-as-judge。**知道评估方法的边界，比只会跑分更显专业。**

### 4. 消融实验（ablation study）

**消融**：其他条件不变，只切换一个组件，看指标怎么变——用来归因"到底是哪个组件在起作用"。

```python
rows = run_ablation(
    retrievers={"向量检索": v, "BM25检索": b, "混合(RRF)": h},
    dataset=dataset, judge=judge_result, top_k=3,
)
# → 每套配置一行：Recall@3 / MRR / Precision@3
```

本日离线演示（示例文档 8 chunks，Debug 伪向量）结果：

| 配置 | Recall@3 | MRR | Precision@3 |
|---|---|---|---|
| 向量检索 | 0.88 | 0.708 | 0.46 |
| BM25 | 1.00 | 1.000 | 0.67 |
| 混合(RRF) | 1.00 | 1.000 | 0.62 |

**演示数据里 BM25 反而最强**——为什么？因为自动生成的题目（"请介绍液流电池储能…"）直接包含章节里的词，是**关键词友好型查询**，正中 BM25 下怀。这恰恰是很好的反面教材：
1. 评测集必须**贴近真实用户提问**（口语化、换说法），否则会高估关键词检索；
2. 混合检索的价值体现在"语义换说法"的查询上——向量路补召回、BM25 路补精度，各取所长。

真实的消融要等你用**真实的 60 题 QA 集 + 真实文档库 + 真实 Embedding** 跑 `python -m qa.eval.run` 才有说服力（D2 周计划里我们还会把评测集扩到 100 题）。

### 5. CLI 用法（你本机 docker 起来后）

```bash
# 1) 启动服务并摄取文档（如还没做）
docker compose up -d
python -m qa.ingestion.run --input docs/

# 2) 跑消融（默认用 60 题 QA 集，K=5，输出到 qa/eval/retrieval_ablation_report.md）
python -m qa.eval.run
python -m qa.eval.run --top-k 3 --limit 30     # 小样本试跑
```

### 6. 关键收获（面试/简历可用）

1. **评估分层**：能讲清"检索质量评估先于生成质量评估"的原因与成本考量；
2. **指标口径**：Recall@K / MRR / Precision@K 的定义、区别、何时用哪个；
3. **消融方法论**：单变量归因，"混合 > 单路"要用数据说话；
4. **评估的诚实性**：能主动说出关键词判定的局限 → LLM-as-judge（这是加分回答）；
5. **复用存量**：60 题 golden set 直接复用，不重复造轮子。

### 7. 下一步

检索评估闭环已就位。下一阶段（进入计划的第二周主线）：**查询改写 + LLM 意图路由**，以及把评估接上**生成质量指标（RAGAS）**——届时需要你提供 DashScope API Key 才能跑通完整判分，我会把代码先写好、离线可测，Key 一配即用。

---

## D7：重排（rerank）—— Week1 收官

### 0. Week1 完成清单

| 计划 Week1 任务 | 状态 | 学习日志 |
|---|---|---|
| D1 pyproject + pytest + loguru | ✅ | D1 |
| D2 Docker Compose 骨架 | ✅ | D2 |
| D3-4 文档摄取管道 | ✅ | D3 |
| D5 向量检索 + BM25 + RRF | ✅ | D4 |
| **D6 重排（今天补上）** | ✅ | **D7（本日）** |
| D7 统一接口 + KG 工具化 + 端到端 | ✅ | D4（统一接口/KG 检索器）+ D5（引擎接入） |
| 额外：引擎接入 + 引用溯源 | ✅ | D5 |
| 额外：检索评估与消融（第二周内容提前） | ✅ | D6 |

**Week1 目标"图谱 + 向量 + 全文 + 重排"四件套正式达成**，检索管线完整形态：
多路召回 → RRF 粗排 → **交叉编码精排** → 进 Prompt。

### 1. 重排是什么？为什么召回之后还要重排？

检索家族有两个流派：

| | 双塔 (bi-encoder) | 交叉编码 (cross-encoder) |
|---|---|---|
| 做法 | 问题/段落各自编码成向量再比对 | 把"问题+段落"拼一起送进模型打分 |
| 是否互相看过对方 | ❌ 各编各的 | ✅ 精细交互 |
| 速度 | 快（可预先索引百万级） | 慢（每条候选都要单独过模型） |
| 精度 | 一般 | 高 |

**召回阶段必须用双塔**：库里有上万 chunk，不可能每条都做交叉编码；向量/BM25 先用便宜的方式粗筛出 Top-50。**重排阶段再用交叉编码器**对 Top-50 逐条精排，只留 Top-10 进 LLM——这就是 QAnything 等项目的"两阶段检索"标准姿势。

### 2. 本模块设计（qa/retrieval/reranker.py）

照搬 D3 embedder 的"双实现 + 工厂"模式：

| 实现 | 何时生效 | 行为 |
|---|---|---|
| `DashScopeReranker` | 配置了 API Key | 调 DashScope `gte-rerank-v2` 交叉编码精排 |
| `NoopReranker` | 无 Key / 离线 | 保持召回顺序直接截断（等价于"没重排"） |

```python
def create_reranker():
    if config.DASHSCOPE_API_KEY:
        return DashScopeReranker(model=config.RERANK_MODEL)
    return NoopReranker()
```

**关键收益：管线代码不感知后端差异。** 你把 Key 一配，同一套 HybridRetriever 自动从"召回即结果"升级为"召回+精排"——不需要改任何调用方。这是"可降级/可插拔"设计的又一次实践。

DashScopeReranker 的实现要点：
- **分数回写**：把 API 的 `relevance_score` 写回 `result.score`，覆盖 RRF 的排名分——让上层拿到的分数口径统一为"精排相关度"；
- **缺失候选兜底**：API 偶尔漏返回某些 index 时，把缺失的按原序补在末尾，保证结果不丢失；
- **延迟导入 dashscope**：与 embedder 一致，创建对象不报错。

### 3. HybridRetriever 的两阶段改造

```python
HybridRetriever(retrievers=[...], reranker=create_reranker(), rerank_candidates=50)
```

`retrieve()` 的流程变为：
```
每路召回 top_k*3 → RRF 融合（跨源去重）→ 取前 rerank_candidates(50) 条候选
→ reranker.rerank(query, 候选, top_k) → 返回精排后的 top_k
```

没有 reranker 时行为与原来完全一致（测试保证），**新能力零破坏地叠加**。

### 4. 怎么测试一个"调用外部 API"的模块？—— 注入假 SDK

重排要调 DashScope，测试不能真调。方法：**把假的 dashscope 模块塞进 `sys.modules`**：

```python
monkeypatch.setitem(sys.modules, "dashscope", FakeDashScopeModule())

class FakeTextReRank:
    items = [...]   # 测试指定的返回结果
    @classmethod
    def call(cls, **kwargs):
        return FakeDashScopeResult(cls.items)
```

这样能精确验证：**API 返回的顺序会不会被正确采纳、分数是否回写、top_k 是否截断、参数是否透传（model/query/documents/top_n）、错误码是否抛异常**。Python 里 `import` 一个包时先查 `sys.modules`，所以塞进去的假模块会被 `from dashscope import TextReRank` 拿到——这是测试外部 SDK 的标准技巧。

### 5. 测试与演示

新增 `tests/test_reranker.py` 11 个测试 → 全量 **134 passed**。

离线演示确认挂点生效：同一个 HybridRetriever，未挂 reranker 时按 RRF 序返回；挂上（演示用倒序）reranker 后顺序被改写——真实 gte-rerank 只需在工厂里替换即可。

### 6. 关键收获（面试/简历可用）

1. **两阶段检索**：bi-encoder 召回 + cross-encoder 精排，能解释"为什么不在召回阶段用交叉编码"；
2. **成本-质量权衡**：Top-50→Top-10 的工程理由（每多一条候选多一次模型调用）；
3. **可插拔设计**：Noop 实现保证离线可跑、Key 到位即升级，调用方零改动；
4. **外部 SDK 测试法**：`sys.modules` 注入假模块验证调用行为；
5. **分数口径统一**：重排后把精排分回写，上层拿到的分数语义一致。

### 7. 下一步（进入 Week2）

Week1 检索底座全部完成。Week2 主线：**查询改写 + LLM 意图路由**（让"它呢？"这类指代问题能正确检索）→ 评测集扩到 100 题 → 接 RAGAS 生成质量指标（需 Key）。

---

## Week1 总结（里程碑回顾）

> 对应上方 `UPGRADE_PLAN.md` 第一周计划，全部完成 ✅（含两处顺序调整：D5 检索层提前、D6 评估提前，均已说明）。

### 1. 一周目标回顾

**原始目标**："把单一图谱检索升级为**图谱 + 向量 + 全文 + 重排**四件套，并补上文档摄取能力。"

**一周后的事实**：不只是四件套，还额外完成了问答引擎接入（引用溯源）和检索评估闭环——比计划多推进了约 1.5 天的 Week2 内容。

### 2. 完成清单（按学习日志章节）

| 日志 | 交付物 | 验证 |
|---|---|---|
| D1 | pyproject.toml、loguru 日志、pytest 骨架 + Fake Neo4j | 首个 61 测试全绿 |
| D2 | Dockerfile + docker-compose.yml（4 服务）+ .dockerignore | YAML/构建路径校验 |
| D3 | qa/ingestion：解析/切分/向量化/Qdrant 幂等入库 | 28 新测试 + 端到端命中 |
| D4 | qa/retrieval：KG/向量/BM25 统一接口 + RRF + 跨源去重 | 19 新测试，多路命中加分 |
| D5 | AnswerEngine 注入参考资料 + 编号引用 + FakeLLM 测试 | 回答带 [1] 引用 |
| D6 | qa/eval：Recall@K/MRR/Precision@K + 消融实验 | 样例报告生成 |
| D7 | qa/retrieval/reranker.py：gte-rerank + Noop 降级；两阶段检索 | 139 测试全绿（截至总结时） |

**工程资产新增**：
- 3 个新包共 20 个模块：`qa/ingestion`（5）、`qa/retrieval`（9）、`qa/eval`（5）＋ `qa/logging_setup.py`
- 根级文件：`pyproject.toml`、`Dockerfile`、`docker-compose.yml`、`.dockerignore`、`docs/sample_new_energy.md`、`UPGRADE_PLAN.md`、`LEARNING_LOG.md`
- 测试：0 → **139 个**（10 个测试文件，全部离线可跑、无外部依赖）
- 学习文档：约 1300 行、70+ 术语词条

### 3. 架构演进（Week1 前 → 后）

```
Week1 前：问题 → 规则意图 → 图谱查询 → LLM（KG+RAG 雏形，单通道）
                                        ↓
Week1 后：问题 → 意图分类
                    ├─ 图谱通道：overview / relation / compare（结构化事实）
                    ├─ 文档通道：向量 + BM25 → RRF 融合 → 跨源去重
                    │            → rerank 精排（召回池 50 → Top-K）
                    └─ 两路合并注入 Prompt（编号引用）→ LLM 生成带出处回答
        另配套：文档摄取管道（增量去重）+ 检索评估闭环（消融实验）
```

### 4. 关键技术决策（面试时能讲的"为什么"）

1. **为什么切分按标题 + overlap**：一个 chunk 尽量是一个完整话题，切块边界话题靠重叠尾巴兜底；
2. **为什么多路召回**：语义（向量）和精确词（BM25）互补，各管一段；
3. **为什么 RRF 用排名不用分数**：各路分数量纲不可比（余弦 [-1,1] vs BM25 上千），排名是天然可比量；
4. **为什么召回池要 ≥ 重排池**（实战教训）：重排器只能在召回给它的池子里工作——召回太浅，跨语言/低相似度的正确 chunk 在精排前就被截掉；
5. **为什么图谱知识走结构化、文档知识走文本**：防同一事实被注入两次；
6. **为什么 Embedder/Reranker 都做"双实现 + Noop/Debug 降级"**：无 Key 也能全链路跑通测试，Key 到位零改动升级。

### 5. 真实环境问题复盘（比代码更有价值的实战经验）

这一周在你机器上实际跑通时暴露的问题，全部已修复并沉淀为代码/文档：

| 问题 | 根因 | 修复 |
|---|---|---|
| `neo4j` 容器 exited(1) | Neo4j 镜像把 `NEO4J_PASSWORD` 环境变量当作配置项解析（NEO4J_ 前缀即配置） | compose 删除该变量，健康检查改从 `NEO4J_AUTH` 剥离密码 |
| 摄取报 `'dict' object has no attribute 'embeddings'` | 新版 dashscope SDK 的 `output` 是 dict 而非对象 | Embedder/Reranker 兼容两种返回形态 |
| 摄取时 Qdrant 连接被拒 | qdrant 容器停了（compose 未配 restart 策略） | `docker compose up -d` 拉起 |
| 重复摄取全量重跑 | 无增量逻辑，重复解析 + 重复花 embedding 钱 | 内容哈希去重，默认只处理新文档（`--force`/`--recreate` 逃生口） |
| 中文问英文文档答不出 | 召回池太浅（15 条），跨语言正确 chunk 没进池，rerank 救不回来 | 有 reranker 时每路召回加深到 50 |

### 6. 效果验证记录（你亲手跑的）

- ✅ 文档摄取：IEA Global EV Outlook 2025（173 页英文）与《中国光伏产业发展路线图》成功入库并可检索；
- ✅ 英文问英文文档：高质量回答；
- ✅ 中文问英文文档：修复召回池后解决（跨语言语义检索走通）；
- ✅ 增量摄取：重复运行自动跳过已入库文档；
- ✅ 网页问答：图谱 / 文档 / 混合三类问题均可用，回答带引用来源。

### 7. 遗留边界（诚实清单，面试可主动提）

1. Debug 伪向量 / Noop 重排只用于离线测试，真实效果依赖 DashScope Key；
2. 增量摄取对"内容修改的文件"会新增 doc_id 而非清理旧版（需 `--recreate` 定期整理）；
3. PDF 只支持文字版，扫描件/表格复杂版面抽取质量有限；
4. 关键词相关性的评估判定是近似（严格语义需 LLM-as-judge）；
5. 意图分类仍是规则式（Week2 升级为 LLM 路由）；
6. 会话记忆仍是内存级（Week2/3 换 Redis）。

### 8. Week2 预告

查询改写 + LLM 意图路由 → 评测集扩到 100 题 → RAGAS 生成质量指标 → 记忆持久化 → LangGraph Agent 化编排（Week3）。

---

## D8：LLM 查询改写 + LLM 意图路由（Week2 启动 · D1）

### 0. 今天做了什么（一句话总结）

把"用户问题 → 检索"之间补上了一层**查询理解**：LLM 先把多轮对话里的
指代问句（"那它的能量密度呢？"）改写成**自包含问句**，再由 LLM 做**意图路由**
（8 类标签），原来的规则识别降级为兜底——离线/无 Key/LLM 挂了时整条链路依然可用。

> 对应执行计划（UPGRADE_PLAN.md §5.2 D1）：
> "LLM 查询改写（多轮指代消解）+ LLM 意图路由，规则识别降级为兜底"。
> 新增 `qa/llm_router.py`，改造 `qa/intent_classifier.py`、`qa/prompt_builder.py`、
> `qa/config.py`、`qa/answer_engine.py`、`qa/main.py`，测试从 **139 → 154 全绿**。

### 1. 为什么 Week2 的第一天做"查询理解"？

Week1 结束时我们已经有了：文档摄取、向量+BM25+图谱多路检索、重排、引用溯源、
检索评估闭环。看起来"检索层"很完整了，但有一个环节 Week1 一直没动——

**问题在进检索器之前，长什么样？**

| 输入 | 实际会遇到的形态 | 直接检索会怎样 |
|---|---|---|
| 第一轮 | "磷酸铁锂电池的循环寿命长吗？" | 很好，检索器能处理 |
| 第二轮 | "那它的能量密度呢？" | 拿去向量化/分词的是"它"，语义空泛、无实体 → 召回质量断崖 |
| 第三轮 | "这俩哪个更适合储能？" | "这俩"指谁？图谱检索根本没有实体可查 |
| 闲聊 | "谢谢" | 不该花 token 去检索知识库 |

Week1 的架构是**单轮问答视角**：每句话当独立问题处理。
但真实用户是**多轮对话**的——前一轮的实体是后一轮的"上下文"。
检索质量的天花板不只在检索器本身，还在**query 的表达质量**：

> 检索器再好，也检索不了"它"；先把"它"翻译成"磷酸铁锂电池"，检索才有意义。

另外规则意图分类（关键词表）有两个先天短板，Week2 一并解决：

1. **泛化差**：问"磷酸铁锂和三元锂谁更安全？"没有"比较/区别"关键词，规则会判错；
2. **不可解释的维护成本**：每新增一种问法都要手动加关键词。

所以 Week2 D1 的内容实质是：**把"理解用户"这件事从规则升级到 LLM**，
同时**保留规则当降落伞**——这是所有"升级"动作的安全姿势：新路为主、老路兜底。

### 2. 心智模型：检索之前多一道"把问题变标准"的工序

类比工厂流水线：产品进质检前要先"整形"（去除毛刺、统一规格）。
我们的 query 也一样，先经过一层 **Query Understanding（查询理解）** 再进检索：

```
用户原话 "那它的能量密度呢？"
        │
        ▼
┌─ ① 查询改写（LLM，结合会话历史）─────────┐
│    输出：脱离上下文也能懂的完整问句        │
│    "磷酸铁锂电池的能量密度如何？"          │
└───────────────┬─────────────────────────┘
                ▼
┌─ ② 意图路由（LLM，输出 8 类标签之一）──────┐
│    property / relation / compare / ...   │
│    （输出不合法/失败 → 规则分类兜底）       │
└───────────────┬─────────────────────────┘
                ▼
┌─ ③ 实体抽取（规则，实体表=知识库名单）──────┐
│    "磷酸铁锂电池" ← 与图谱节点精确同名      │
└───────────────┬─────────────────────────┘
                ▼
         进入 Week1 的多路检索 + 重排 + LLM 生成
```

三个步骤为什么**分工不同**，这是今天最值得在面试里讲的点：

- **改写**：需要"理解语义 + 利用上文"，规则做不好 → 交给 LLM（语言能力强）；
- **意图**：本质是"8 选 1 的分类"，LLM 泛化好，但规则也能做个大概 → LLM 为主、规则兜底；
- **实体抽取**：**必须命中知识库里的精确名称**（Neo4j 节点叫"磷酸铁锂电池"，
  用户说"锂电池"需要映射），而实体名单是**有限的、已知的** → 规则（词典+模糊匹配）
  反而更稳、更快、零成本。LLM 自由发挥容易把实体名说偏，导致 Cypher 查不到。

一句话总结分工哲学：**LLM 干"开放理解"，规则干"封闭匹配"**。

### 3. 逐块拆解今天的代码

#### 3.1 查询改写（`LLMRouter._rewrite`）

**改写的目标**：输出一条"自包含问句"——不依赖任何上文，单独拿出来也能检索。

**触发条件（三层闸门，缺一不可）**：

```
闸门 1  改写开关没关（QUERY_REWRITE_MODE != off）
闸门 2  有可用 LLM（auto 模式下）
闸门 3  有会话历史 + 当前不是闲聊 + 疑似需要改写：
        ├─ 含指代词："它/这个/两者/该技术/上述"...
        └─ 或是超短问句（≤10 字，"为什么？""那循环寿命呢？"）
```

为什么有这些闸门？因为**改写不是免费的**：每次改写都是一次 LLM 调用
（多约 0.3~1 秒 + 少量 token）。对已经完整的问句再改写一次是纯浪费，
甚至可能被模型"画蛇添足"改歪。所以只在"不改写一定检索不好"时才改写。

**改写的 Prompt 设计**（`QUERY_REWRITER_SYSTEM_PROMPT`），逐句看我们约束了什么：

```
你是多轮对话中的「查询改写器」。
任务：把用户最新问题改写为一条不依赖上下文也能独立理解的完整问句。
规则：
1. 只做指代消解与必要的信息补全……不改动用户本意；   ← 防止模型自由发挥
2. 不回答该问题，不添加原文没有的新信息，不评价；     ← 防止模型把改写变成"抢答"
3. 用户用什么语言提问，就输出什么语言；               ← 防止中英混杂
4. 只输出改写后的问句本身：单行、不加引号、不加前缀。 ← 输出格式约束，方便解析
```

User Prompt 则把**历史对话逐条列出 + 最新问题**放进去
（`build_rewrite_prompt`）。注意这里只喂最近的几轮（会话记忆本来就限了 5 轮），
不会把整场对话全塞进去烧 token。

**LLM 输出不听话怎么办？—— `clean_rewritten` 的容错设计**。真实模型经常输出：

- ```` ```json\n磷酸铁锂电池的优点是什么？\n``` ````（加了代码块围栏）
- `改写后的问题：磷酸铁锂电池的优点是什么？`（加了前缀）
- `好的，根据上文，应该是：磷酸铁锂电池的优点是什么？`（先解释再给结果）

我们的清理策略（一句话版）：**剥围栏 → 去引号 → 去常见前缀 → 取最后一个非空行**。
"取最后一行"是有讲究的——模型长输出时通常把最终答案放最后，前文都是"思考过程"。

#### 3.2 LLM 意图路由（`_route_intent_llm`）

**协议**：让 LLM 只输出一个 JSON 对象：

```
{"intent": "property"}
```

System Prompt 里把 8 个标签 + 一句话定义**动态拼进去**
（`build_intent_router_system_prompt`，数据源 `INTENT_LABEL_DESCRIPTIONS`），
这样"标签清单"只维护一处（`qa/intent_classifier.py` 的 `INTENT_LABELS`），
规则兜底和 LLM 路由永远说同一套标签——切换无感。

**解析容错（`parse_intent_json`）**，今天学到一个很实用的技巧：

```python
start = text.find("{")
obj, _ = json.JSONDecoder().raw_decode(text[start:])
```

- `json.loads` 要求**整个字符串**都是合法 JSON，模型多说一个字就抛异常；
- `raw_decode` 只解析**从某个位置开始的一个 JSON 值**，后面多出来什么（解释、标点）
  无所谓。先 `find("{")` 跳到 JSON 起点，就能容忍"前面有废话"。
- 再配合"剥代码块围栏"的兜底。

**别名归一化（`normalize_intent`）**：模型不一定会老实输出 `property`，
可能写 `属性`、`comparison`、`无法判断`。维护一张
`INTENT_ALIASES`（中文/英文/口语 → 标准标签）字典做归一化，归不上的返回 None → 降级。

**降级逻辑（`_route_intent`）**，三个分支一句话：

```
intent_mode = rules → 直接规则分类，0 次 LLM 调用（离线评测/消融对比用）
LLM 可用        → 调 LLM；输出能解析且标签合法 → 用 LLM 结果 (source="llm")
其余一切情况    → 规则分类兜底 (source="rules")：
                  无 Key / LLM 抛异常 / 输出不是 JSON / 标签不合法
```

这就是"**能降级就降级，绝不让理解失败中断问答**"的工程姿势。

#### 3.3 统一入口 `LLMRouter.analyze`

返回一个 dict（而不是散装多个方法），AnswerEngine 拿一个 dict 就够用：

```python
{
    "raw_question":  "那它的能量密度呢？",        # 用户原话（保留给记忆/回显）
    "question":      "磷酸铁锂电池的能量密度如何？", # 改写后（检索/生成都用它）
    "intent":        "property",
    "entities":      [{"name": "磷酸铁锂电池", ...}],
    "intent_source": "llm",       # 或 "rules"
    "rewrite_applied": True,      # 本轮是否改写过（供响应/前端展示）
    "rewrite_used_llm": True,
}
```

### 4. AnswerEngine 怎么接入的（改动最小化）

`AnswerEngine.answer()` 里只换了一行"理解"的来源：

```python
# 之前：规则一条龙
analysis = self.classifier.analyze(question)

# 现在：LLM 优先、规则兜底的路由器（同一份实体表）
analysis = self.router.analyze(question, history)
```

随之而来的三个"用哪份问题"的决策，是今天最容易踩的坑，写清楚：

| 环节 | 用哪份 | 为什么 |
|---|---|---|
| 图谱检索 / 文档检索 | `analysis["question"]`（改写后） | 检索需要实体明确的问句，"它"没法查 |
| 注入 LLM 的 Prompt | `analysis["question"]`（改写后） | 让生成模型基于完整问句作答，避免它再猜指代 |
| 存进会话记忆 | `analysis["raw_question"]`（原话） | 记忆应忠实还原用户说了什么；改写只是"为检索服务的一次性加工" |
| 响应 `question` 字段 | `raw_question` | API 兼容（前端回显的是用户输入框内容） |
| 响应 `resolved_question` 字段 | 改写后问句 | 新增字段，前端/调试能看到"系统到底检索了什么" |

这样设计后有一个很优雅的效果：**指代消解的结果进了检索与生成，但没污染记忆**。
下一轮用户说"那成本呢？"，改写器看到的仍是用户真实的历史，而不是被加工过的问句。

**成本诚实说明**（写代码时就要心里有数）：

| 场景 | 每轮 LLM 调用次数（之前 → 现在） |
|---|---|
| 无历史、问题完整 | 1 → 2（多了 1 次意图路由） |
| 有历史、有指代 | 1 → 3（改写 + 意图路由 + 主问答） |
| 无 Key（离线） | 0 → 0（全规则，一次都不多花） |

这也是为什么给了两个开关（`INTENT_ROUTING_MODE=rules` / `QUERY_REWRITE_MODE=off`）：
离线测试、对比实验、或预算敏感场景可以一键关掉 LLM 理解层，行为退回 Week1。

### 5. 今天产出/改动的文件

| 文件 | 做了什么 |
|---|---|
| `qa/llm_router.py`（新增） | `LLMRouter`：改写 + 意图路由 + 容错解析 + 规则兜底，统一 `analyze()` 入口 |
| `qa/intent_classifier.py` | 新增 `INTENT_LABELS` 常量（单一标签源）；文档注明规则版现为兜底角色 |
| `qa/prompt_builder.py` | 新增改写器 System/User Prompt、意图路由 System/User Prompt 构建方法 |
| `qa/config.py` | 新增 `INTENT_ROUTING_MODE`、`QUERY_REWRITE_MODE`（auto/rules/llm、auto/on/off） |
| `qa/answer_engine.py` | 注入 `LLMRouter`；改写后问句走检索/Prompt；原话存记忆；响应新增 4 个字段 |
| `qa/main.py` | `QAResponse` 扩展 `resolved_question` / `query_rewritten` / `rewrite_used_llm` / `intent_source` |
| `.env.example` | 补两个开关的注释说明 |
| `tests/test_llm_router.py`（新增） | 15 个测试：降级、改写、意图容错、引擎集成 |

### 6. 测试怎么做"不花钱"的 LLM 测试（重点技巧）

今天的模块核心是"调用 LLM"，但我们不可能每次跑测试都真调 DashScope。
测试技术叫**脚本化 LLM（Scripted Fake）**：

```python
class ScriptedLLM:
    def generate(self, system_prompt, user_prompt):
        # 按 System Prompt 内容决定"扮演"谁
        if "意图分类器" in system_prompt:
            return {"content": '{"intent": "property"}'}   # 扮演意图路由
        if "查询改写器" in system_prompt:
            return {"content": "磷酸铁锂电池的能量密度如何？"}  # 扮演改写器
        return {"content": "这是主问答的回答。"}                 # 扮演主 LLM
```

同时这个 Fake 会**记录每一次调用**（`self.calls.append(...)`），测试里就能断言：

- 改写模式下 LLM 被调了 2 次（改写 1 + 意图 1），没历史时只有 1 次（只意图）；
- 主问答的 Prompt 里出现的是**改写后**的问题，而不是"那它的"；
- `QUERY_REWRITE_MODE=off` 时一次改写调用都不发。

这种"按输入分发的假对象 + 记录调用的断言"是测试 LLM 应用的**通用套路**
（比 mock 库更直白：你完全控制它说什么，也完全看得到它收到什么）。

今天还专门测了**故障注入**三种情况，证明链路韧性：

| 注入的故障 | 期望行为 | 测试 |
|---|---|---|
| Fake LLM 直接抛 `LLMError` | 改写用原问题、意图用规则，问答不中断 | `test_llm_failure_degrades_to_rules` |
| 意图输出 `"抱歉，我不确定怎么归类……"`（非 JSON） | 规则兜底，source=rules | `test_malformed_intent_output_falls_back_to_rules` |
| 意图输出 `{"intent": "我要飞"}`（非法标签） | 归一化失败 → 规则兜底 | `test_invalid_intent_label_falls_back_to_rules` |

另外被动的两处旧测试修改（这是**架构升级的正常代价**，写进日志）：

1. `test_answer_engine.py` 的 FakeLLM 原来对任何调用都返回一句固定回答；
   现在意图路由也会走同一个 LLM，固定回答解析不成 JSON → 日志会打降级警告，
   测试断言 `calls == 1` 也会失败。修正：让 FakeLLM 对"意图分类器"System Prompt
   返回合法 JSON，其余返回固定回答；断言改为 `calls == 2`（意图 + 主问答）。
2. 我的集成测试最初断言"本轮原话出现在本轮 Prompt 的历史里"——这是错的：
   记忆是**答完才写入**的，本轮原话要到下一轮才进历史。修正为断言上一轮原话在历史里。
   这个 bug 本身也是个好教训：**写测试前先确认数据的时序**。

### 7. 怎么验证（在你机器上可复现）

```bash
# 1) 全量测试（154 个，全部通过）
python -m pytest -q

# 2) 有 DashScope Key 时，真实对话验证多轮指代（本地起服务后）
#    第一轮：磷酸铁锂电池的循环寿命长吗？
#    第二轮：那它的能量密度呢？
#    → 看响应 JSON 里的字段：
#      "resolved_question": "磷酸铁锂电池的能量密度如何？"   ← 被改写
#      "query_rewritten": true
#      "intent_source": "llm"
#    网页版 http://127.0.0.1:8000/chat 可连问两轮直接体验

# 3) 想退回纯规则（离线对比/省 token）
#    在 qa/.env 加：
#    INTENT_ROUTING_MODE=rules
#    QUERY_REWRITE_MODE=off
```

（注：docker 里跑的 api 服务改了代码后需要 `docker compose restart api` 才生效；
本地 `python -m qa.main` 则即时生效。）

### 8. 关键收获（面试/简历可用）

1. **RAG 不止"检索 + 生成"**：Query Understanding（改写/意图）是独立且重要的一层；
   能把"为什么在检索前做改写"讲清楚 = 不是只会调框架的人；
2. **LLM 应用的正确姿势是"LLM 为主 + 规则兜底 + 双开关"**：
   主路径先进、老路保底、能一键切换——面试官问"LLM 挂了怎么办"直接有答案；
3. **开放任务给 LLM、封闭匹配留给规则**：实体抽取保留词典式，是为了保证
   与知识库命名精确一致（实体名不一致 → Cypher 0 命中，这是 RAG+KG 的常见坑）；
4. **输出协议要窄、解析要宽容**：强制 JSON + raw_decode + 别名归一化 + 非法降级，
   这是所有"让 LLM 输出结构化结果"场景的通用四件套；
5. **工程叙事升级**：响应里带 `resolved_question` / `intent_source` /
   `query_rewritten` —— 系统"每一步做了什么"都可观测，是 Agent 化的预演。

### 9. 遗留 / 待办（诚实清单）

1. 真实 DashScope 下的改写效果（指代消解质量、是否偶尔改歪）还没跑过真实验证，
   需要你在本机带 Key 连问两轮确认；
2. 每轮多 1~2 次 LLM 调用的**延迟与成本**：当前用 qwen-turbo 可接受；
   若后续嫌慢可做"规则置信度高就不调 LLM"的混合模式（Week2 调参阶段可选）；
3. 跨语言改写（中文问题 → 英文文档检索词）不在今天范围，Week2 做评测集时评估是否需要；
4. LLM 改写结果目前**没有二次校验**（万一模型把实体改错，检索会跟着错），
   后续可加"改写后必须命中至少一个知识库实体/否则退回原问题"的护栏（记入待办）。

### 10. 明日预告（Week2 D2-3）

把评测集从 60 题扩到 100 题，并接入 **RAGAS**（faithfulness / answer relevancy /
context precision / context recall）——生成质量指标需要真实 DashScope Key，
但代码与离线骨架会先写好，Key 一配即跑。查询理解层今天已就位，
正好可以用"多轮/指代"类题目补进评测集，量化改写带来的提升。

---

---

## D9：评测集扩到 100 题 + RAGAS 风格生成质量四指标（Week2 D2-3）

### 0. 今天做了什么（一句话总结）

两件事：① 把 golden set 从 **60 题扩到 100 题**（补齐 list/aggregate/path 等意图覆盖，
并为 40 道新题标注了 `reference_answer` 标准答案）；② 自研并接入 **RAGAS 风格四指标**
（faithfulness / answer_relevancy / context_precision / context_recall），
把 Week1 的"检索质量评估"升级为"生成质量评估"，测试从 **154 → 171 全绿**。

> 对应执行计划（UPGRADE_PLAN.md §5.2 D2-3）：
> "golden set 扩到 100 题；接入 RAGAS（faithfulness、answer relevancy、
> context precision/recall）"。
> 注意：四指标全部需要真实 LLM 裁判（DashScope Key），本次交付"代码 + 离线测试 +
> 示例样本 + 一键 CLI"，Key 一配即跑；无 Key 时报告会如实标注"无法判分"而不是编 0 分。

### 1. 为什么要"先扩评测集、再上生成指标"？

Week1 的评估只回答一个问题：**"检索器有没有把对的资料捞回来？"**
（Recall@K / MRR / Precision@K，LLM-free）。

但 RAG 系统交付给用户的是"生成的一句话"。检索再好，也可能在生成环节出问题：
- **幻觉**：资料没写，模型自己编（faithfulness 低）；
- **答非所问**：资料是捞到了，但模型答偏了（answer_relevancy 低）；
- **资料不全**：即使模型会答，参考资料里根本没那段事实（context_recall 低）。

所以 Week2 要补上"生成质量"这半边评估。而上生成指标之前必须先做一件事——
**把评测集做厚**：原来的 60 题意图分布是 property/relation/compare/list，
几乎没有 aggregate（统计）、path（产业链），这类"资料型问题"恰恰最能区分
"单路关键词检索 vs 语义/混合检索"。评测集不扩，四指标跑在 60 题上说服力不足。

### 2. D2：评测集 60 → 100 的结构变化

#### 2.1 分布变化

| 意图 | Week1（60 题） | 现在（100 题） | 说明 |
|---|---:|---:|---|
| property（属性） | 20 | 29 | 补 BC电池/半片/飞轮/重力储能/氢储能等 |
| relation（关系） | 22 | 32 | 补托卡马克装置、熔盐堆燃料、储氢载体等 |
| compare（对比） | 12 | 16 | 补 BC vs TOPCon、钠冷快堆 vs 熔盐堆等 |
| list（列举） | 6 | 12 | 补物理储能/薄膜电池/四代核电/液流电池等 |
| aggregate（统计） | 0 | 5 | 新覆盖："一共有几类……" |
| path（产业链） | 0 | 6 | 新覆盖："……产业链包括哪些环节" |

#### 2.2 条目新增了什么字段

原有字段：`question` / `expected_intent` / `expected_entities` /
`expected_answer_keywords` / `expected_relation_entities`。

新扩展字段（加载时原样透传，缺失就不出现）：
- `reference_answer`：**标准答案**（一句可被检索上下文证实/证伪的话）。
  没有它，context_recall 无从评估——这一步是今天最重的标注工作：
  40 道新题每道都写了一条可核实的标准答案；
- `history`：多轮对话历史（为后续"查询改写评测"预留；检索消融会自动过滤这类题，
  因为改写前它们无法被独立检索）。

#### 2.3 工程决策：为什么评测集拆成两个文件？

- `qa/eval_dataset.json`：原始 60 题不动（历史数据零风险）；
- `qa/eval_dataset_extra.json`：新增 40 题；
- `load_qa_golden()` 默认把两份**合并**，加载后 100 题对外无差别。

好处：新增题目与旧数据解耦，以后还可以再加 `_extra2`；坏处是"评测集在哪"变成
两处——所以 dataset.py 里集中维护两个常量 + 一个合并函数，注释写明规则
（传自定义路径时只加载单文件，便于小样本调试）。

配套新增 `load_retrieval_dataset()`：过滤掉 `history` 题，保证检索消融
不会拿"它呢？"这种无法独立检索的问题去误伤指标。

### 3. D3：四个生成质量指标（重点，逐字拆）

#### 3.1 先建一张"指标卡片"（面试先背这张表）

| 指标 | 一句话 | 判定对象 | 需要的输入 | 满分含义 |
|---|---|---|---|---|
| faithfulness 忠实度 | 回答有没有**编** | 回答的陈述 vs 上下文 | answer + contexts | 1 = 句句有据 |
| answer_relevancy 答案相关性 | 回答**切不切题** | 回答 vs 问题（经向量） | question + answer | 1 = 完全切题 |
| context_precision 上下文精确率 | 检索片段**有没有用、排得靠不靠前** | 片段 vs 问题 | question + contexts | 1 = 相关都靠前 |
| context_recall 上下文召回率 | 资料**够不够全**（能否支撑理想回答） | 标准答案陈述 vs 上下文 | ground_truth + contexts | 1 = 全覆盖 |

注意后面三个都依赖第一个链条：**一切生成质量评估的地基是把文本拆成"可证伪的陈述"**
（RAGAS 术语叫 statement decomposition）。没有陈述粒度，faithfulness 和 recall
只能整段模糊打分。

#### 3.2 faithfulness（忠实度）：两步拆解

**直觉**：把模型回答拆成一句句"事实断言"，再逐句问裁判"资料里支持吗？"

```
Step 1 陈述拆分（LLM）
    回答："全钒液流电池循环寿命超两万次，适合长时储能。"
  → claims: ["全钒液流电池循环寿命超两万次", "全钒液流电池适合长时储能"]
        prompt 要求只输出 JSON: {"claims": [...]}

Step 2 支持度检查（LLM）
    喂 claims（编号） + contexts（编号）
  → {"supported": [true, false]}        ← 第二句资料没提 → 不支持

score = supported / total = 1/2 = 0.5
```

一个工程细节值得记住：两个步骤各一次 LLM 调用，**但可以在 prompt 里批量完成**
（所有 claims 一次问完），而不是一条一条问 —— 少烧 token、少等延迟。

另一个细节：**上下文为空时直接判 0**（短路径，不再调一次支持度检查）：
资料一条都没有，回答里的陈述自然全部无据可依。这属于"可证明的退化分支"，短路合法。

#### 3.3 answer_relevancy（答案相关性）：RAGAS 的官方口径

不看 RAGAS 文档的人会直觉地让 LLM 给答案打个"相关分（0-5）"。
但 RAGAS 官方用的是**向量方法**，思路非常巧妙：

```
Step 1 让 LLM 从"回答"反推出 3 个问题
   回答："磷酸铁锂电池安全性高、循环寿命长、成本低。"
  → ["磷酸铁锂电池有什么优点？", "哪种电池便宜又安全？", ...]

Step 2 把反推问题与"用户真实问题"分别 embedding
Step 3 平均余弦相似度
```

为什么能测"切题"？**如果回答真的回答了用户的问题，那么"由这个回答能反推出
的问题"应该和用户问的差不多**；驴唇不对马嘴的回答，反推出来的问题会跟
用户问题离得很远。这是把"语义相关性"量化成可计算指标的标准做法，
面试被问"answer_relevancy 怎么算的"答出这三点就是加分项。

我们的实现里 embedding 走 Week1 的 Embedder 抽象：有 DashScope Key 用真向量，
离线测试用 `DebugHashEmbedder`（只验证流程与余弦计算，语义不真实——报告里要说明）。

#### 3.4 context_precision（上下文精确率）：把公式亲手推一遍

RAGAS 的定义不是简单的"相关片段数 / 总片段数"，而是**只看"相关位置"上的
precision@k 再取平均**。手工推例子：

```
contexts（按检索分数从高到低）: [无关A, 相关B, 相关C]
裁判判定 relevant = [false, true, true]

只看相关的位置：
  k=2（第 2 个片段相关）→ precision@2 = 前2个里相关的比例 = 1/2 = 0.5
  k=3（第 3 个片段相关）→ precision@3 = 2/3 ≈ 0.6667

context_precision = (0.5 + 0.6667) / 2 ≈ 0.5833
```

为什么这样定义？它同时惩罚两件事：
- 相关片段太少（分母大分子小）；
- 相关片段排太后（第一个相关位置越靠后，前面被统计的 k 越大、precision@k 越低）。

"相关片段是不是都在前面"是排序质量的核心，这正是 Week1 MRR 想表达、
但换到"上下文粒度"后的延伸。

#### 3.5 context_recall（上下文召回率）：和 faithfulness 的区别

最容易混淆的一对：

| | faithfulness | context_recall |
|---|---|---|
| 陈述来自 | 模型**回答** | **标准答案**（ground_truth） |
| 问的是 | 模型有没有编 | 资料够不够支撑"理想回答" |
| 需要的标注 | 无 | reference_answer |

本质同一个"支持度检查"函数（`_check_support`），但被检查的对象不同——
一个查回答、一个查标准答案。设计上我们让两个指标共用一个裁判 prompt，
只是传入的 claims 来源不同，代码零重复。

#### 3.6 可用性契约：None 和 0 必须分清（今天的核心工程原则）

很多评估代码会把失败当 0 分，这是**最坑的隐性 bug**：0 会被写进报告当成
"系统很差"，实际上是"没判成"。我们的契约：

| 情况 | 返回值 | 理由 |
|---|---|---|
| 判定成功但确实全不相关 | 0.0 | 真实的最差表现 |
| 需要 ground_truth 但没提供 | None | 不是差，是没得判 |
| LLM 裁判调用失败 / 输出解析失败 | None | 同上 |
| 上下文列表为空（faithfulness/recall） | 0.0 | 数学上可证：无资料必不支持 |

汇总时只统计成功判分的样本，并在报告里注明 `judged/skipped` 数字。
这样任何人看报告第一眼就知道：**这个 0.82 是 18 题的平均，还是 2 题的平均？**

### 4. 一个值得在简历/面试讲的决策：为什么"自研 RAGAS"而不是直接 `pip install ragas`？

1. **讲得清原理**：四指标各自拆成哪些 LLM 调用、公式怎么推——上面 3.2~3.5 就是
   面试答案；只会 `ragas.metrics()` 的人说不出这些；
2. **离线可测**：真 ragas 库强依赖 langchain 生态 + 在线 API，CI 里没法测；
   我们的实现用"脚本化裁判 LLM"就能在 CI 里把每条公式手工验证（171 个测试）；
3. **可控成本与失败**：真实的 ragas 新版 API 变化频繁，黑盒出错难排查；
   自研版每个失败点都有日志、有降级（None 契约）；
4. **诚实**：简历上写"自研实现 RAGAS 四指标评估体系（LLM-as-judge + 向量余弦）"
   比写"接入了 ragas 库"更可信——面试官深挖公式时你不会露馅。

（当然，理解原理后再去用真 ragas 做交叉验证是加分项，属于后续可选项。）

### 5. 工程产出与用法

| 文件 | 作用 |
|---|---|
| `qa/eval_dataset_extra.json`（新增） | 40 道新题 + reference_answer |
| `qa/eval/dataset.py`（改造） | 双文件合并加载、字段透传、`load_retrieval_dataset` |
| `qa/eval/gen_metrics.py`（新增） | 四指标 + `score_sample` / `summarize_scores`（纯函数） |
| `qa/eval/gen_run.py`（新增） | CLI：样本评分 / 现场收集 / Markdown 报告 |
| `qa/eval/demo_ragas_samples.json`（新增） | 5 个示例样本（含一个"回答超出上下文"的负面样例） |
| `tests/test_gen_metrics.py`（新增） | 17 个测试：手工核算 + 降级契约 + 汇总 + 报告 |
| `tests/test_eval.py`（改造） | 断言评测集 ≥100、新意图覆盖、40 条 reference_answer |

真实用法（需要 DashScope Key）：

```bash
# 1) 用示例样本试跑（Key 配好后即可看到分数）
python -m qa.eval.gen_run

# 2) 现场收集：用真实问答引擎把 40 道带标准答案的题跑一遍
#    （要求 Neo4j / Qdrant / Key 就绪），回答与检索引用自动落盘
python -m qa.eval.gen_run --collect-live --samples-output qa/eval/collected_samples.json

# 3) 对落盘样本评分（可反复评，不重复烧问答的钱）
python -m qa.eval.gen_run --samples qa/eval/collected_samples.json \
    --output qa/eval/ragas_live_report.md
```

报告包含：汇总表（四指标得分 + judged/skipped）+ 逐样本明细
（问题 / 回答来源 / 四指标单值）+ 说明（指标定义与判分条件）。

### 6. 测试：ScriptedJudge 剧本化裁判

延续 D8 的"脚本化 LLM"套路，测试里造一个**按 System Prompt 分发响应**的裁判：

```python
class ScriptedJudge:
    def generate(self, system_prompt, user_prompt):
        if "最小事实陈述" in system_prompt:  # 陈述拆分
            return {"content": '{"claims": ["陈述1", "陈述2"]}'}
        if "证据裁判" in system_prompt:       # 支持度检查
            return {"content": '{"supported": [true, false]}'}
        if "检索裁判" in system_prompt:       # 片段相关性
            return {"content": '{"relevant": [false, true, true]}'}
        if "反推出" in system_prompt:          # 问题反推
            return {"content": '{"questions": ["磷酸铁锂电池的优点是什么？", "今天天气怎么样？"]}'}
```

配合 DebugHashEmbedder，就能把四个指标的公式**用确定性的输入逐值算出来**
（比如 context_precision 手工推得 ≈0.5833，测试里就断言 ≈0.5833，abs=0.001），
而不是只测"在 0 到 1 之间"这种弱断言。

今天测试还抓出三个真 bug（复盘比功能本身更值钱）：

1. **`or` 吃掉空列表**：`obj.get("claims") or obj.get("statements")`，
   当 claims 合法地返回 `[]`（回答是"你好"没有陈述）时，被 or 当成 falsy
   去取 statements → None → 指标误判失败。修法：先 `get("claims")`，
   只有它是 None 才取 `statements`。教训：**`or` 不能用来给"空容器也是合法值"
   的字段做默认值**；
2. **对所有指标函数一刀切传参**：`score_sample` 原来把 question/answer/contexts/
   ground_truth 全塞给每个指标函数，而 `faithfulness()` 根本没定义 `question` 参数
   → TypeError。修法：为每个指标显式声明 `_METRIC_KWARGS` 参数白名单。
   教训：**不同函数签名不同时，调用方必须按签名分发参数，不能偷懒**；
3. **陈述拆分的空结果与失败没区分**：返回 `None` 还是 `[]` 语义完全不同
   （无陈述=该给满分，解析失败=该给 None），最初用 `return cleaned or None`
   把两者混为一谈。修法与 bug1 同源。

### 7. 离线验证记录（ScriptedJudge 剧本下的人工核算）

| 指标 | 输入剧本 | 期望 | 实测 |
|---|---|---|---|
| faithfulness | 2 claims，supported=[T,F] | 0.5 | ✅ |
| answer_relevancy | 反推 1 条近似 + 1 条无关 | (0,1] | ✅ |
| context_precision | [无关,相关,相关] | ≈0.5833 | ✅ |
| context_recall | 2 claims，covered=[T,F] | 0.5 | ✅ |
| 全部分支 | LLM 抛异常/非 JSON/缺 ground_truth | None 不误报 0 | ✅ |
| CLI（无 Key） | demo 样本 | 报告生成、全部标跳过、exit 0 | ✅ |

### 8. 关键收获（面试深挖点）

1. **评估金字塔**：检索指标（Week1）→ 生成指标（本周）是两层；
   "检索好≠答得好"，能主动说出二者区别 = 对 RAG 有整体认识；
2. **指标的可证伪设计**：faithfulness/recall 用"陈述拆分 + 支持度检查"两步走，
   比直接让 LLM 打总分更可解释、可定位（哪句话编的都能指出来）；
3. **None ≠ 0 的工程契约**：评估系统里"没判成"和"判了很差"必须分开，
   否则报告自欺欺人——这是数据科学工程化的分水岭；
4. **脚本化 LLM 让评估代码可离线回归**：手工核算 + 故障注入，
   LLM 应用不敢这么测就谈不上可靠；
5. **标注成本意识**：context_recall 需要标准答案，这是最贵的标注；
   我们的 40 条 reference_answer 是"从知识库事实改写的一句可核实陈述"——
   诚实讲，这是半自动标注，严格做法需人工复核（列入遗留清单）。

### 9. 遗留 / 待办（诚实清单）

1. 四指标的真实分数**还没跑**（需要 DashScope Key + 服务就绪），
   你本机配好 Key 后跑 `python -m qa.eval.gen_run --collect-live` 才能拿到
   简历可引用的数字；
2. `demo_ragas_samples.json` 是**手工构造**的示例（含一个故意"超出上下文"的
   负面样例），只能用来验证管线，不能当正式结果；
3. reference_answer 为半自动标注，正式报告前建议人工抽检 10~20 条；
4. answer_relevancy 离线测试用 Debug 向量，只验证公式与流程，
   真实分数必须用 text-embedding-v3；
5. 上下文字符数可能超裁判窗口（每条 chunk ~600 字 × 5 条），
   大批量评估前需要加"截断/抽样"策略（列入 D4-5 调参项）；
6. LLM-as-judge 本身有偏差，严格实验应做人工抽检一致性（inter-annotator），
   课程项目可用"抽 10 题人工核对"替代。

### 10. 明日预告（Week2 D4-5）

用扩好的 **100 题**评测集 + 真实文档库跑**检索消融实验**
（纯KG / 纯向量 / 纯BM25 / 混合 / 混合+重排），拿到四套真实对比数字；
同时把 `--collect-live` 收集的真实问答样本用四指标评分，
得到"检索质量 + 生成质量"双闭环的完整证据链。

---

## D10：检索消融升级为五组对比 + 可复现报告（Week2 D4-5 · 代码就绪）

### 0. 今天做了什么（一句话总结）

把 Week1 的检索消融 CLI 升级成 **五组对比**（纯KG图谱 / 纯向量 / 纯BM25 /
混合RRF / 混合RRF+重排），并把"对比组装配"抽成**纯函数 + 离线测试**；
报告增加实验配置块（meta），保证"这个 0.73 是在什么参数下跑出来的"可复现。
测试从 **171 → 175 全绿**。

> 对应执行计划（UPGRADE_PLAN.md §5.2 D4-5）：
> "检索消融实验：纯KG / 纯向量 / 混合 / 混合+重排 四组对比，产出报告"。
> 诚实说明：**真实跑分需要你的机器上 Neo4j/Qdrant/Key 就绪**（本会话环境无 Docker），
> 今天交付的是"跑分脚本 + 离线验证 + 复现规范"，数字等你本机执行后填入报告。

### 1. 为什么 Week2 还要升级消融？（先想清楚目的）

Week1 已有可用的 `qa.eval.run`（KG / 向量 / BM25 / 混合RRF），为什么还要改？

1. **缺了最关键的一组：混合+重排**。Week1 的混合组没有挂 reranker，
   等于没验证"两阶段检索"到底值不值那额外的 API 成本——
   这正是简历上最想引用的数字："混合+重排 比 单路 高多少"；
2. **报告不可复现**：旧报告只有指标表，没记录 RRF 的 k、评测集大小、
   是否重排——两周后自己都说不清这张表怎么来的；
3. **真实语料才能归因**：Week1 用 Debug 伪向量 + 3 条样例 chunk 跑过一版
   （ablation_demo_report.md），那是"流程验证"，不是"效果证据"。
   Week2 要在 100 题 + 真实文档上跑，才能谈"向量 vs BM25 谁强、强在哪类题"。

### 2. 升级设计：把"装配对比组"做成纯函数

旧代码在 `main()` 里用 if/else 现场拼 dict，没法测试、没法复用。
新版抽出顶层函数：

```python
def build_retriever_groups(kg, corpus, fusion_k=60, reranker=None, ...) -> dict:
    # 纯函数：给什么装什么，不碰网络/数据库
    groups = {}
    if kg is not None:
        groups["纯KG图谱"] = kg
    if corpus is not None:
        vector, bm25 = corpus
        groups["纯向量"] = vector
        groups["纯BM25"] = bm25
        groups["混合(RRF)"] = HybridRetriever([vector, bm25], fusion_k=fusion_k)
        if reranker is not None:
            groups["混合(RRF)+重排"] = HybridRetriever(
                [vector, bm25], reranker=reranker, rerank_candidates=50)
    return groups
```

为什么值得抽出来？

- **可离线测试**：装配逻辑不依赖真实 Neo4j/Qdrant，用测试里的内存语料
  （vector+bm25+NoopReranker）就能验证"该出的组都出了、不该出的没出"；
- **可复用的对比矩阵**：以后想加"向量+KG"组、想调权重，改一处函数即可；
- **main() 只剩编排**：取数据 → 装组 → 跑分 → 写报告，读起来像实验记录。

### 3. 三个关键工程决策（面试能讲）

#### 3.1 重排组只在"真实重排"时才加入

`create_reranker()` 在没有 DashScope Key 时返回 `NoopReranker`（顺序不变）。
如果此时还加"混合+重排"组，它和"混合(RRF)"的结果**逐题完全相同**，
纯属浪费时间还污染报告。所以 CLI 判断：

```python
rerank_group_enabled = reranker.name != "noop"
# Noop 时跳过，并在日志里说明原因
```

但这个判断**只在 CLI 里做**——`build_retriever_groups` 不替调用者决定，
测试里仍可显式传 `NoopReranker` 去验证装配和跑分链路。
这符合"库函数做能力、CLI 做策略"的分层。

#### 3.2 报告带 meta（可复现性）

`write_markdown_report` 新增 `meta` 参数，在标题下方输出实验配置：

```markdown
# 检索消融实验报告

> K = 5（Recall@5 / Precision@5）
> 评测集：qa/eval_dataset.json + qa/eval_dataset_extra.json（共 100 题参与…）
> RRF 平滑常数 k = 60
> 重排候选池 = 50；真实重排组：已启用（dashscope_rerank）
```

**规则：一张没有实验配置的指标表 = 不可复现的结果 = 不能写进简历。**
写报告时把"变量控制"也写进去，是数据侧工程素养的体现。

#### 3.3 fusion-k 参数化 + 评测集自动过滤多轮题

- `--fusion-k 60`：RRF 平滑常数可调，为 D6-7 的"k=30/60/100 调参实验"铺路；
- `load_retrieval_dataset()`：自动过滤带 history 的多轮指代题——
  这类题必须经 D1 的查询改写后才能检索，直接拿去跑消融会拖低所有组的指标，
  造成"系统性偏差"而非"某组真的差"。

### 4. 产出清单与用法

| 文件 | 变更 |
|---|---|
| `qa/eval/run.py` | 重写：`build_retriever_groups` 纯函数；五组装配；`--fusion-k` / `--rerank-candidates` 参数；报告 meta |
| `qa/eval/ablation.py` | `write_markdown_report` 新增 `meta` 参数（向后兼容） |
| `tests/test_eval.py` | +4 测试：组装配（含/不含重排）、KG-only、重排组跑分、meta 报告 |

真实跑分（在**你的机器**上，服务就绪后）：

```bash
# 1) 五组全量消融（100 题；有 Key 自动加"混合+重排"组）
docker compose up -d        # 确保 neo4j/qdrant healthy
python -m qa.eval.run --output qa/eval/retrieval_ablation_report.md

# 2) 调参对比（同一份评测集，只改 k，观察稳定性）
python -m qa.eval.run --fusion-k 30 --output qa/eval/ablation_fk30.md
python -m qa.eval.run --fusion-k 100 --output qa/eval/ablation_fk100.md

# 3) 生成质量闭环（真实问答收集 + RAGAS 四指标）
python -m qa.eval.gen_run --collect-live --samples-output qa/eval/collected_samples.json
python -m qa.eval.gen_run --samples qa/eval/collected_samples.json
```

预期观察（并写进最终报告）：跨语言/复杂表格类题目上 混合+重排 应优于单路；
KG 图谱类题目（数量/列举/对比）上 纯KG 组不可被文档路替代——**"各有所长"
本身就是消融实验最值钱的结论**（不必追求某组全胜）。

### 5. 测试（+4，为什么这么测）

| 测试 | 验证点 |
|---|---|
| `test_build_groups_with_corpus_and_rerank` | 有 reranker → 4 组；无 reranker → 3 组（重排组不出现在结果里） |
| `test_build_groups_kg_only` | 只有 KG → 恰好 1 组（不误建文档组） |
| `test_rerank_group_runs_ablation` | 重排组能完整跑完一轮消融（Noop 下应与混合组行为一致） |
| `test_write_markdown_report_with_meta` | meta 行（k=30 等）真实写入报告文件 |

前三个本质是"装配函数的契约测试"，第四个是"报告可复现性"的回归测试——
确保以后谁把 meta 参数弄丢，测试立刻报警。

### 6. 关键收获（面试弹药）

1. **消融实验是"变量控制实验"**：一次只切一个变量（加不加重排、k 取多少），
   否则无法归因。能说出"为什么重排组要跟混合组同 pool、同 k"就是专业；
2. **组件可装配性 = 可测试性**：把依赖真实服务的组装逻辑抽成纯函数，
   用内存语料离线验证——这套模式 Week1 的 FakeNeo4j 到 Week2 一直在复用；
3. **报告即实验记录**：meta（数据集、参数、跳过原因）比分数本身更重要；
4. **诚实的数据观**：跑分结果可能"不如预期"——降分也是有效结论
   （说明该配置在该语料上不划算），重点是能解释。

### 7. 遗留 / 待办（诚实清单）

1. **真实五组数字待你本机跑出**（本会话无 Docker/Key）；
2. 报告目前只到"配置级别"的汇总，D6-7 可加"按意图分组看指标"
   （如 compare 类问题上混合 vs 单路的差距），定位更细；
3. 单次全量消融对 100 题 × 5 组 × 每问多次检索，会消耗一定的
   embedding/rerank API 额度，建议先用 `--limit 20` 试跑估算耗时再全量；
4. 多轮指代题被消融过滤了——它们的检索质量评测需要"改写后"的版本，
   属于查询理解评测的一部分，Week2 收尾时补。

### 8. 明日预告（Week2 D6-7）

依据消融结果调参（RRF 的 k、Top-N、重排候选池、切分粒度），
并补"检索失败 → 改写重试"的用例：当检索结果为空或相关性过低时，
触发一次查询改写再检索（与 D1 的改写器衔接），这是 Week3 LangGraph
Agent 化"反思重试"节点的雏形。

---

## D11：Week2 收尾 —— 调参工具 + 检索失败改写重试（Week2 D6-7）

### 0. 今天做了什么（一句话总结）

补齐 Week2 最后两块：① 参数扫描工具 `qa/eval/tune.py`（fusion-k / 重排候选池 /
配合切分粒度实验的 `--collection`）；② 问答引擎的**检索失败 → 改写重试**链路
（零召回时让 LLM 换个问法再检索一轮）。README 评估章节更新为最新事实。
测试从 **175 → 182 全绿**。

> 对应执行计划（UPGRADE_PLAN.md §5.2 D6-7）：
> "依据消融结果调参（RRF 的 k、Top-N、切分粒度）；补检索失败→改写重试用例；
> 更新 README 评估章节"。
> 诚实说明：调参的**真实对比数字**仍需你在本机跑（依赖真实语料），
> 今天交付的是"调参工具 + 防死循环的重试机制 + 可复现跑分规范"。

### 1. 参数扫描工具：怎么设计"调参"这件事

#### 1.1 调参为什么不能靠手改代码

Week2 的调参目标是 RRF 平滑常数 k、重排候选池大小、切分粒度。如果每次
改一个值都手改配置再跑一遍，实验记录就会散落在终端历史里——报不出
"k=30 比 k=100 好多少"。

正确姿势是**扫描（sweep）**：同一份评测集、同一批语料，只把参数取一组值
分别跑，输出一张带参数标签的对比表。于是有了 `python -m qa.eval.tune`：

```bash
python -m qa.eval.tune --fusion-k-values 30,60,100 --limit 30
# 有 Key 时加扫重排池
python -m qa.eval.tune --rerank-candidates-values 30,50,100
```

#### 1.2 组名 = 实验记录

扫描结果要可读，关键设计是**把参数写进组名**：

```python
groups[f"混合(RRF) k={k}"] = HybridRetriever([v, bm], fusion_k=k)
groups[f"混合(RRF)+重排 k={k} cand={cand}"] = HybridRetriever(...)
```

于是报告行自带参数："混合(RRF) k=30 Recall@5=0.71" —— 不需要额外查配置。
"组名=实验变量"这个习惯在可视化/写论文/跟同事对实验时都极有用。

#### 1.3 切分粒度的正确工作流（`--collection` 的意义）

chunk 尺寸不能像 k 那样在同一个集合上扫——它决定**库里存了什么**，
必须重新摄取。为此本周打通一条链路：

```bash
# 1) 同一语料分别按 400 / 600 / 800 字符切分，存入三个集合
python -m qa.ingestion.run --input docs/ --collection corpus_c400 --chunk-size 400
python -m qa.ingestion.run --input docs/ --collection corpus_c600 --chunk-size 600

# 2) 对每个集合分别跑消融/扫描
python -m qa.eval.run --collection corpus_c400 --output qa/eval/ablation_c400.md
python -m qa.eval.run --collection corpus_c600 --output qa/eval/ablation_c600.md
```

所以 `try_build_corpus_retrievers(collection=...)` 等工厂函数都加了可选
collection 参数（默认仍读 config），评估工具暴露 `--collection`。
代价提示：不同 chunk 尺寸 = 不同的向量点集，**必须重建集合重新摄取**
（向量化 API 有成本），先小语料试跑。

### 2. 检索失败 → 改写重试（本周第二个重点）

#### 2.1 动机与触发条件

Week1 修过一个真问题：中文问英文文档零命中是因为"召回池太浅"。但还有另一类
零命中：**问法用词与库内表述差异过大**（用户问"磷酸铁锂安全性咋样"，
库内文本是"热失控风险低"）。深度调参救不了这种语义鸿沟，需要**换个问法重试**。

触发条件（全部满足才重试）：

```
图谱检索无结果（kg_context 为 None）
AND 文档检索无结果（references 为空）
AND 意图不是 chat / unknown（闲聊不需要检索证据）
AND 有可用 LLM（改写器本质是 LLM）
AND 已重试轮数 < config.RETRY_ROUNDS（默认 1，防失控）
```

#### 2.2 与 D1 查询改写的区别（容易混，务必分清）

| | D1 查询改写 `_rewrite` | D6 检索失败改写 `retry_rewrite` |
|---|---|---|
| 时机 | 检索**之前**（正常链路） | 检索**失败之后**（补救链路） |
| 目标 | 指代消解：把"它"补成实体 | 换说法/补全/必要时翻译，提高命中率 |
| 触发 | 有历史 + 疑似指代 | 零召回 + 非闲聊 + 有 LLM |
| 失败时 | 用原问题（降级） | 停止重试（防死循环） |

两个方法共用 `clean_rewritten` 清理逻辑和"单行输出"协议，
但 Prompt 目标不同（`QUERY_RETRY_SYSTEM_PROMPT` 明说"上一轮零命中，
请换一种更易命中的说法"）。

#### 2.3 防死循环的三重保险

LLM 不可控，重试必须可终止：

1. **轮数上限**：`RETRY_ROUNDS`（默认 1，环境变量可调）；
2. **无进展即停**：LLM 输出的改写与当前问句相同 → 视为无进展，返回 None → 停止；
3. **无可替代问法即停**：无 LLM / 输出为空 → None → 停止。

引擎每轮重试会：
- 重新对替代问句做**实体抽取**（问法变了实体可能变，图谱命中依赖实体名）；
- 重新做图谱检索 + 文档检索；
- 一旦任一路有证据就跳出循环，用最终问句构造 Prompt。

响应里新增 `retry_count` 和 `retry_questions`，前端/调试都能看到
"这轮回答其实经历了 1 次改写重试、用的是哪个问句"——可观测性延续 D8 的叙事。

> 与 Week3 的关系：LangGraph 里会有专门的 "reflect（反思）→ 重试" 节点，
> 本实现就是那个节点的**最小可替换版本**：逻辑已就位，Week3 只需把它搬进
> 图节点并在状态里记录重试历史，而不是从零写。

### 3. 测试（+7）

新增 `tests/test_retry_engine.py`（4 个）与 `tests/test_eval.py::TestTuneSweep`（3 个）：

| 测试 | 验证点 |
|---|---|
| 首次零命中 → 改写重试 → 第二次命中 | retry_count=1、引用注入、实体更新、LLM 调用序列正确 |
| 首次就命中 | 不触发重试（0 次额外 LLM 调用） |
| LLM 原样返回问题 | 无进展即停，不空转不重复检索 |
| 闲聊零命中 | 不重试 |
| 扫描组命名 | k/cand 参数进了组名 |
| 无真实重排时 | 不出 cand 变体 |
| 扫描组完整跑分 | 行数=组数、报告可读 |

回归修复两处旧测试（架构升级的正常代价）：

1. D1 的多轮改写集成测试原本无 KG/文档，现在会自动触发重试分支，脚本 LLM
   会被当成"重试改写器"误应答 → 在该测试里显式 `RETRY_ROUNDS=0`
   （重试特性本身在 test_retry_engine 单独覆盖，互不干扰）；
2. 扫描组断言的字符串包含写错成集合相等 → 改为 `any(sub in name for name in ...)`。

### 4. 产出清单

| 文件 | 变更 |
|---|---|
| `qa/eval/tune.py`（新增） | fusion-k / 重排候选池参数扫描 CLI；`build_sweep_groups` 纯函数 |
| `qa/eval/run.py` | 新增 `--collection`（切分粒度实验）；meta 输出集合名 |
| `qa/retrieval/factory.py` | 工厂函数支持 `collection` 参数 |
| `qa/config.py` | 新增 `RETRY_ROUNDS`（默认 1） |
| `qa/prompt_builder.py` | 新增 `QUERY_RETRY_SYSTEM_PROMPT` + `build_retry_prompt` |
| `qa/llm_router.py` | 新增 `retry_rewrite()`（检索失败改写器） |
| `qa/answer_engine.py` | 检索失败→改写重试循环；响应新增 retry 字段 |
| `qa/main.py` | QAResponse 扩展 retry 字段 |
| `tests/test_retry_engine.py`（新增） | 4 个重试场景测试 |
| `tests/test_eval.py` | +3 参数扫描测试 |
| `README.md` | 特性清单、架构树、评估命令、测试说明更新为 Week2 现状 |

### 5. 你现在可以跑 Week2 的完整闭环（本机）

```bash
# ① 全量自动化测试
python -m pytest -q

# ② 真实语料五组消融（100 题；配 Key 自动加重排组）
python -m qa.eval.run --output qa/eval/retrieval_ablation_report.md

# ③ 参数扫描（先小样本估算 API 消耗）
python -m qa.eval.tune --fusion-k-values 30,60,100 --limit 20

# ④ 真实问答样本收集 → RAGAS 四指标
python -m qa.eval.gen_run --collect-live --samples-output qa/eval/collected_samples.json
python -m qa.eval.gen_run --samples qa/eval/collected_samples.json

# ⑤ （可选）切分粒度实验：--collection + --chunk-size 摄取多个集合后对比
```

### 6. 关键收获（面试弹药）

1. **"调参"要有实验规范**：扫描工具 + 参数进组名 + meta 配置块，
   报告才能回答"为什么选 k=60"；
2. **LLM 功能的可靠性设计**：重试必须三保险（轮数上限/无进展即停/无替代即停）——
   面试被问"如果 LLM 循环重试怎么办"直接背这三条；
3. **一次只改一个变量的纪律**贯穿 Week2：消融切组件、扫描切参数、集合对比切切分；
4. **最小可替换版本（MVP-first）**：重试逻辑先在线性引擎里落地并测试，
   Week3 进 LangGraph 节点时就是搬运不是重写。

### 7. 遗留（Week2 收官盘点）

1. 真实跑分数字（消融五组 + 参数扫描 + RAGAS）待你本机执行后回填；
2. 会话记忆仍是内存级（deque）——Week3 D3 换 Redis；
3. 引擎主流程仍是线性编排——Week3 D1-2 搬进 LangGraph（改写/意图/检索/重试
   都已作为独立可复用单元就位）；
4. reference_answer 是半自动标注，正式使用前建议人工抽检。

---

## Week2 总结（里程碑回顾）

### 1. 计划对照表

| 计划（UPGRADE_PLAN §5.2） | 状态 | 落点 |
|---|---|---|
| D1 LLM 查询改写 + LLM 意图路由，规则兜底 | ✅ | `qa/llm_router.py`；改写/意图双 Prompt；15 测试 |
| D2-3 golden set 扩 100 题 + RAGAS 四指标 | ✅ | `eval_dataset_extra.json`；`qa/eval/gen_metrics.py` + `gen_run.py` |
| D4-5 检索消融：纯KG/纯向量/混合/混合+重排 | ✅（代码） | `qa/eval/run.py` 五组装配 + meta；真实数字待本机跑 |
| D6-7 调参 + 检索失败改写重试 + README | ✅（代码） | `qa/eval/tune.py`；引擎重试链路；README 更新 |

> 与 Week1 相同的纪律：**每阶段代码 + 离线测试 + 文档都交付**；
> 依赖真实服务/Key 的"跑分"保留给你本机执行（本会话环境无 Docker/Key）。

### 2. Week2 之后的系统形态

```
用户问题
  → 查询理解层（D1）    LLM 改写（指代）→ LLM 意图路由 → 规则实体抽取
  → 多路检索（Week1）    图谱 + 向量 + BM25 → RRF → 重排
  → 检索失败补救（D6）   零召回 → LLM 改写重试（≤ RETRY_ROUNDS）
  → 生成（引用溯源）      LLM 按编号参考资料作答
  → 记忆（原话入库）
```

### 3. 新增能力一览

- 意图/改写从规则升级为 LLM 为主、规则兜底（双开关可切回）；
- 评估体系三层：检索指标（Recall/MRR/P）→ 生成指标（RAGAS 四件）→
  参数扫描（k/cand/切分粒度）；
- 100 题评测集，40 题带标准答案，意图覆盖 6 类；
- 检索失败重试闭环 + 全程可观测字段（resolved_question/retry_count/…）。

### 4. 测试规模里程碑

| 时点 | 数量 |
|---|---:|
| Week1 结束 | 139 |
| D8（改写+意图路由） | 154 |
| D9（评测集+RAGAS） | 171 |
| D10（五组消融装配） | 175 |
| D11（扫描+重试） | 182 |

### 5. 遗留边界（诚实清单，Week3 开头逐条消）

1. 真实跑分数字待你本机回填（消融/扫描/RAGAS 三份报告）；
2. 记忆未持久化（Redis 化在 Week3 D3）；
3. 主流程未 Agent 化（LangGraph 在 Week3 D1-2，届时把改写/意图/检索/重试
   单元搬进图节点）；
4. 前端还未展示改写/重试过程（SSE 在 Week3 D4-5，字段已就位）；
5. PDF 无 OCR、扫描件弱、reference_answer 半自动标注——均为已知边界。

### 6. Week3 预告

LangGraph Agent 化（plan → retrieve → answer → reflect，检索失败重试 ≤2 轮）
→ Redis 会话记忆 → SSE 流式 + 前端检索过程展示 → Docker 一键 + CI + README
架构图/指标 → 3 分钟演示视频 + 简历 bullet 定稿。

---

## 附：术语速查表

| 术语 | 一句话解释 |
|---|---|
| pyproject.toml | Python 项目的元数据与工具配置标准文件（PEP 621） |
| 可编辑安装 `pip install -e .` | 安装后代码改动即时生效，不需要重装 |
| sys.path | Python 查找 import 模块的路径列表 |
| loguru | 开箱即用的日志库，全局 logger + 一行配置 |
| handler | 日志的"输出目的地"（控制台/文件），loguru 里用 `logger.add()` 添加 |
| rotation / retention | 日志文件按大小轮转 / 按时间保留 |
| pytest | Python 测试框架，自动发现 `test_*` 文件与函数 |
| fixture | 测试前置准备函数，pytest 自动注入 |
| tmp_path | pytest 内置 fixture：每个测试独立的临时目录 |
| monkeypatch | pytest 内置 fixture：临时修改属性/环境，测试后自动还原 |
| Mock / Fake | 模拟真实依赖的假对象，用于隔离被测代码 |
| assert | Python 断言，pytest 用它判断测试通过与否 |
| 镜像 (image) | 只读的应用模板：代码 + 环境 + 依赖，可反复创建容器 |
| 容器 (container) | 镜像运行起来的实例，互相隔离 |
| Dockerfile | 描述如何构建镜像的脚本 |
| 层缓存 (layer cache) | Docker 按行缓存构建结果，内容不变的分层不重复执行 |
| docker compose | 一条命令编排启动多个容器的工具 |
| healthcheck | 容器健康检查，compose 可等它通过再启动下游服务 |
| depends_on | 声明服务间启动顺序（started / healthy 两种语义） |
| 数据卷 (volume) | 容器外的持久化存储，容器删除数据不丢 |
| 端口映射 | 把"宿主机端口:容器端口"打通，外部才能访问容器服务 |
| .dockerignore | 构建时排除的文件清单，防止敏感文件进镜像 |
| embedding / 向量化 | 把文本转成一串浮点数，语义相近的文本数值相近 |
| chunk / 切块 | 把长文档切成适合检索与生成的小段（带元数据） |
| overlap | 相邻 chunk 的重叠内容，避免切块截断语义 |
| 余弦距离 (Cosine) | 衡量两个向量方向是否一致，RAG 检索常用度量 |
| n-gram | 连续 n 个字符/词组成的特征单元 |
| L2 归一化 | 把向量长度缩放为 1，让余弦相似度等价于点积 |
| collection / point | Qdrant 的"表" / "行"（向量 + 原文 payload） |
| payload | 伴随向量存储的业务数据（原文、来源、章节等） |
| doc_id / 内容哈希 | 用文件内容 SHA-256 生成的文档唯一 ID |
| uuid5 | 由"命名空间 + 名称"确定性生成 UUID，同输入同输出 |
| 幂等 (idempotent) | 同一操作重复执行结果不变（重复导入不堆积） |
| 内存模式 :memory: | QdrantClient 的本地模式，测试无需启动服务器 |
| 延迟导入 (lazy import) | 用到时才 import，避免未装依赖时创建对象就报错 |
| Retriever | 检索器统一接口：`retrieve(query, top_k) → List[RetrievalResult]` |
| RetrievalResult | 统一检索结果：text + score + source + 溯源字段 |
| 多路召回 | 多个检索器并行取结果再融合，互补单路盲区 |
| BM25 | 关键词加权排序算法（词频饱和 + 长度惩罚 + IDF） |
| IDF | 逆文档频率：越罕见的词权重越高 |
| 词频饱和 (k1) | 词出现很多次后边际收益递减，防止刷词 |
| RRF | 倒数排名融合：1/(k+rank) 按排名融合多路结果 |
| 跨源去重 | 同一份知识（doc_id+chunk_index）来自多路时合并为一条 |
| 余弦相似度 | 向量夹角的余弦值，衡量语义方向是否一致 |
| scroll / fetch_all | 分页拉取 Qdrant 全部数据（供 BM25 建索引） |
| 引用溯源 / 编号引用 | 检索结果编号注入 Prompt，LLM 按 [1][2] 标注回答出处 |
| references | 问答响应里的参考资料列表（含 text/source/section/doc_id/score） |
| 防重复注入 | 同一批事实只以最优形式注入一次（图谱结构化 vs 文档文本分工） |
| Fake / Spy | 假对象让系统动起来（Fake）；顺带记录调用输入（Spy）验证行为 |
| API 契约 | 后端返回字段 ↔ Pydantic 模型 ↔ 前端展示的约定，改动需同步 |
| 优雅降级 | 可选依赖不可用时系统自动降级而非崩溃 |
| golden set | 带标准答案/期望标注的评测问题集 |
| relevance judgment | 判定检索结果是否相关（本实现用实体/章节文本匹配近似） |
| Recall@K | 前 K 名中是否命中相关结果（"漏没漏"） |
| MRR | 第一个相关结果的倒数排名（"排得靠不靠前"） |
| Precision@K | 前 K 名中相关结果的占比（"噪音多不多"） |
| 消融实验 (ablation) | 只切换一个组件对比指标，归因组件贡献 |
| LLM-as-judge | 用 LLM 当裁判打分（faithfulness 等需要它） |
| 重排 (rerank) | 用交叉编码器对召回候选精排（两阶段检索第二步） |
| 双塔 (bi-encoder) | 文本各自编码后比对，快但精度有限（召回用） |
| 交叉编码 (cross-encoder) | 问题+段落拼接打分，准但慢（精排用） |
| 两阶段检索 | 双塔粗筛 Top-50 → 交叉编码精排 Top-10 |
| Noop / 空实现 | 无 Key 时的降级实现，保持原序保证管线可用 |
| sys.modules 注入 | 测试中塞入假模块，让被测代码 import 到替身 |
| 增量摄取 | 只处理内容未入库过的文档（doc_id=内容哈希去重） |
| 召回池 / 重排池 | 重排前召回的候选数量；池太浅则正确结果会在精排前被截掉 |
| 跨语言检索 | 中文问题检索英文文档等跨语言场景，依赖多语言 embedding + rerank |
| 配置项注入歧义 | 中间件镜像把"前缀环境变量"当配置解析（NEO4J_PASSWORD 教训） |
| 查询改写 (query rewrite) | 用 LLM 把多轮指代问句改写成脱离上下文也能独立检索的自包含问句 |
| 指代消解 (coreference resolution) | 弄清"它/这个/两者"在上下文中具体指代哪个实体 |
| 自包含问句 | 不依赖上文、单独拿出来也能被检索/理解的完整问句 |
| 意图路由 (intent routing) | 先用 LLM 判断问题属于哪类意图，规则分类降级为兜底 |
| 归一化 (normalize) | 把模型的各种口语输出（属性/comparison/无法判断）映射到标准标签 |
| JSONDecoder.raw_decode | 只解析字符串中从某位置开始的一个 JSON 值，容忍前后有废话 |
| 脚本化 LLM (Scripted Fake) | 按 System Prompt 分发固定响应的假 LLM，测试无需真实调用 API |
| LLM 输出协议 | 让 LLM 按约定格式（如 JSON）输出，收窄不确定性便于解析 |
| 故障注入测试 | 故意让依赖抛异常/返回脏数据，验证系统能正确降级 |
| 降级链 (degradation chain) | LLM 失败 → 规则兜底 → 本地兜底，任何一层故障都不中断问答 |
| 陈述拆分 (statement decomposition) | 把回答/标准答案拆成若干条"可被资料证实或证伪"的最小事实断言 |
| faithfulness（忠实度） | 回答中被上下文支持的陈述比例，衡量"有没有编造" |
| answer_relevancy（答案相关性） | 由回答反推问题与用户问题的向量余弦平均，衡量"切不切题" |
| context_precision（上下文精确率） | 按相关位置算 precision@k 再平均，衡量"片段有用且靠前" |
| context_recall（上下文召回率） | 标准答案陈述被上下文覆盖的比例，衡量"资料全不全" |
| ground_truth / reference_answer | 标准答案（reference_answer），context_recall 的必备输入 |
| judged / skipped | 报告里"成功判分的样本数 / 因缺输入或失败跳过的样本数" |
| 半自动标注 | 从结构化事实改写评测标注，需人工抽检保证质量 |
| 对比组装配 (build groups) | 把检索器按消融需要装成"组"的纯函数，可离线测试 |
| meta（实验配置） | 报告里的数据集/参数/跳过原因说明，保证结果可复现 |
| 变量控制实验 | 一次只切换一个组件/参数，其余不变，才能归因指标差异 |
| 过滤多轮指代题 | 消融前剔除需改写才能检索的 history 题，避免系统性偏差 |
| Noop 组跳过策略 | 重排器为空实现时跳过"重排组"，避免与混合组逐题重复 |
| 参数扫描 (sweep) | 同一评测集上让参数取一组值分别跑分，输出对比表找最优值 |
| 组名即实验变量 | 把参数（k=30、cand=50）写进对比组名，报告行自带实验条件 |
| 检索失败改写重试 | 零召回时让 LLM 换个问法再检索一轮，最多 RETRY_ROUNDS 轮 |
| 无进展即停 | LLM 输出与当前问句相同即视为无进展，停止重试防止空转 |
| collection 集合切换 | 切分粒度实验通过不同 Qdrant 集合隔离不同 chunk 版本的语料 |
| 最小可替换版本 (MVP-first) | 先在线性引擎实现某能力，之后搬进 LangGraph 节点时只做搬运 |
