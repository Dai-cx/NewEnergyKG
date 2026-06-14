#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图谱构建与评估模块的配置管理

所有敏感信息（Neo4j 密码）均建议通过环境变量注入，避免硬编码。
开发阶段可在 graph/ 目录下创建 .env 文件，由 python-dotenv 自动加载。
"""

import os
from pathlib import Path

# 尝试加载 .env 文件；若未安装 python-dotenv 则跳过（保持最小依赖）
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore

# ==================== 路径推导 ====================
# graph/ 目录
GRAPH_DIR = Path(__file__).resolve().parent
# 项目根目录
PROJECT_ROOT = GRAPH_DIR.parent
# 默认数据文件路径
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "new_energy.json"

# 依次尝试加载项目根目录和 graph/ 目录下的 .env 文件
# 后加载的不会覆盖已存在的环境变量（override=False）
if load_dotenv is not None:
    for env_dir in (PROJECT_ROOT, GRAPH_DIR):
        env_file = env_dir / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)

# ==================== Neo4j 知识图谱配置 ====================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# ==================== 数据文件路径 ====================
# 支持通过环境变量指定自定义数据文件；否则使用项目默认路径
DATA_PATH = Path(os.getenv("GRAPH_DATA_PATH", str(DEFAULT_DATA_PATH)))

# ==================== 调试开关 ====================
DEBUG = os.getenv("GRAPH_DEBUG", "false").lower() in ("true", "1", "yes")


def get_neo4j_auth():
    """返回 Neo4j 认证元组 (user, password)。若密码为空则抛出异常。"""
    if not NEO4J_PASSWORD:
        raise ValueError(
            "Neo4j 密码未配置。请设置环境变量 NEO4J_PASSWORD，"
            "或在 graph/.env 文件中填写 NEO4J_PASSWORD=your-password"
        )
    return NEO4J_USER, NEO4J_PASSWORD


def check_data_path():
    """检查数据文件是否存在，返回 Path 对象。"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"数据文件不存在: {DATA_PATH}")
    return DATA_PATH
