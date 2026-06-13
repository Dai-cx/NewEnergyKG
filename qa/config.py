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

# ==================== 调试配置 ====================
DEBUG = os.getenv("QA_DEBUG", "false").lower() in ("true", "1", "yes")


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
