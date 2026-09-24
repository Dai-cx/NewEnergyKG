#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志统一配置模块

基于 loguru 提供项目统一的日志输出格式与级别控制。

用法：
    # 在程序入口（例如 qa/config.py 被导入时）调用一次：
    from qa.logging_setup import setup_logging
    setup_logging(level="INFO")

    # 之后任意模块直接使用全局 logger，无需重复配置：
    from loguru import logger
    logger.info("xxx")
    logger.exception("出错了")  # 自动带异常堆栈
"""

import sys
from pathlib import Path
from typing import Optional

from loguru import logger

# 统一日志格式：
# 时间 | 级别 | 模块:函数:行号 - 消息
DEFAULT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """
    配置全局 loguru logger（幂等，可重复调用）。

    Args:
        level: 日志级别，DEBUG / INFO / WARNING / ERROR。
        log_file: 可选日志文件路径；提供后同时输出到文件，按大小自动轮转。
        rotation: 日志文件轮转大小，默认 10 MB。
        retention: 日志文件保留时长，默认 7 天。
    """
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = "INFO"

    # 先移除 loguru 自带的默认 stderr handler，避免重复输出
    logger.remove()

    # 输出到控制台（stderr）
    logger.add(
        sys.stderr,
        level=level,
        format=DEFAULT_FORMAT,
        colorize=True,
    )

    # 可选：同时输出到文件
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_file),
            level=level,
            format=DEFAULT_FORMAT,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
        )

    logger.debug(f"日志系统已初始化: level={level}, file={log_file}")
