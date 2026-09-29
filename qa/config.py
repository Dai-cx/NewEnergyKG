#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问答系统配置模块

所有敏感信息（API Key、密码）均建议通过环境变量注入，避免硬编码。
开发阶段可创建 qa/.env 文件，由 python-dotenv 自动加载。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（如果存在）
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)

# ==================== 项目路径 ====================
# qa/ 目录
QA_DIR = Path(__file__).resolve().parent
# 项目根目录
PROJECT_ROOT = QA_DIR.parent
# 新能源数据文件路径
DATA_PATH = PROJECT_ROOT / "data" / "new_energy.json"

# ==================== Neo4j 知识图谱配置 ====================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# ==================== LLM 配置 ====================
# 默认模型：通义千问 qwen-turbo（与 PDF 实验一致）
DEFAULT_MODEL = os.getenv("LLM_MODEL", "qwen-turbo")

# DashScope（通义千问）配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# OpenAI 兼容接口配置（可选，用于本地模型或其他厂商）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# LLM 请求参数
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# ==================== 服务配置 ====================
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

# ==================== 实体识别配置 ====================
# 模糊匹配阈值（0-100），低于此分数的 rapidfuzz 候选将被过滤。
# 阈值越高越严格，可减少误召回；阈值越低越宽松，可能引入更多近似实体。
ENTITY_FUZZY_THRESHOLD = int(os.getenv("ENTITY_FUZZY_THRESHOLD", "85"))

# ==================== 查询理解配置（Week2 D1）====================
# 意图路由模式：
#   auto  -> LLM 可用则用 LLM 意图路由，否则/失败时降级规则（默认，推荐）
#   rules -> 纯规则（离线测试、消融对比"规则 vs LLM"时用）
#   llm   -> 强制 LLM；无 LLM 时告警并降级规则（保证链路不断）
INTENT_ROUTING_MODE = os.getenv("INTENT_ROUTING_MODE", "auto").lower()

# 查询改写模式：
#   auto -> LLM 可用 + 会话有历史 + 疑似指代问句时改写（默认）
#   on   -> 只要 LLM 可用就尝试改写（忽略指代嫌疑判断）
#   off  -> 完全关闭查询改写
QUERY_REWRITE_MODE = os.getenv("QUERY_REWRITE_MODE", "auto").lower()

# ==================== 检索失败重试配置（Week2 D6）====================
# 图谱与文档都没检索到任何证据时，最多触发几轮"改写重试"（每轮一次 LLM 调用）。
# 默认 1：兜住"用词不同导致零召回"的常见场景；调高会增加延迟与成本。
RETRY_ROUNDS = int(os.getenv("RETRY_ROUNDS", "1"))

# ==================== 会话记忆配置 ====================
# 每个会话保留的最大问答轮数，超过后自动丢弃最旧的记录
MAX_HISTORY_ROUNDS = int(os.getenv("MAX_HISTORY_ROUNDS", "5"))

# ==================== 文档摄取与向量检索配置 ====================
# Embedding 模型与输出维度（DashScope text-embedding-v3 默认 1024 维）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
# 每次调用 Embedding API 的最大文本条数
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "10"))

# Qdrant 向量库地址
# 本地运行默认 http://localhost:6333；docker compose 中通过 QDRANT_HOST=qdrant 覆盖
QDRANT_URL = os.getenv("QDRANT_URL", "")
if not QDRANT_URL:
    QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")
    QDRANT_URL = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "newenergy_docs")

# 语义切分参数：目标块大小（字符数）与相邻块重叠长度
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "600"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))

# 问答时混合检索返回的最大参考资料条数（注入 Prompt 并展示给用户）
RETRIEVER_TOP_K = int(os.getenv("RETRIEVER_TOP_K", "5"))

# 重排（rerank）模型：DashScope gte-rerank-v2（如账号不可用可改 gte-rerank）
RERANK_MODEL = os.getenv("RERANK_MODEL", "gte-rerank-v2")

# 送入重排器的候选条数（两阶段检索的"粗排池子"）。
#
# 这是**成本敏感参数**：重排按 token 计费，而单次请求要把"问题 + 全部候选 chunk"
# 一起送进模型，故费用 ≈ 候选数 × chunk 长度。实测（见 qa/eval/retrieval_tuning_report.md）：
#   cand=30 → MRR 0.8665 / P@5 0.6320
#   cand=50 → MRR 0.8598 / P@5 0.6280
#   cand=100 → MRR 0.8585 / P@5 0.6240
# 小池子同时**更省钱且指标更好**，因此默认取 30（原实现硬编码 50）。
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "30"))

# ==================== 调试配置 ====================
DEBUG = os.getenv("QA_DEBUG", "false").lower() in ("true", "1", "yes")

# ==================== 日志配置 ====================
# 日志级别：DEBUG / INFO / WARNING / ERROR
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# 日志文件路径，留空则仅输出到控制台
LOG_FILE = os.getenv("LOG_FILE", "")

# 初始化全局日志（loguru）。之后任意模块直接 `from loguru import logger` 即可使用。
# 这里在模块末尾导入 setup_logging，避免与 logging_setup 产生循环依赖。
from qa.logging_setup import setup_logging  # noqa: E402

setup_logging(
    level=LOG_LEVEL,
    log_file=Path(LOG_FILE) if LOG_FILE else None,
)


def get_llm_provider():
    """
    根据环境变量判断使用哪个 LLM 提供商。
    优先级：
      1. 配置了 OPENAI_API_BASE + OPENAI_API_KEY → openai
      2. 配置了 DASHSCOPE_API_KEY → dashscope
      3. 未配置任何 key → 返回 None，将启用本地兜底回答
    """
    if OPENAI_API_BASE and OPENAI_API_KEY:
        return "openai"
    if DASHSCOPE_API_KEY:
        return "dashscope"
    return None


def check_data_path():
    """检查数据文件是否存在"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"数据文件不存在: {DATA_PATH}")
    return DATA_PATH
