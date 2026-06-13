#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 客户端

支持两种调用方式：
1. 阿里云 DashScope（通义千问），默认方式，与 PDF 实验一致。
2. OpenAI 兼容接口，用于本地部署模型或其他厂商 API。

未配置 API Key 时，客户端会抛出异常，由上层启用规则兜底回答。
"""

import time
import traceback
from typing import List, Dict, Any, Optional

from qa import config


class LLMError(Exception):
    """LLM 调用过程中的通用异常"""
    pass


class LLMClient:
    """统一大语言模型调用入口"""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: int = config.LLM_TIMEOUT,
        max_tokens: int = config.LLM_MAX_TOKENS,
        temperature: float = config.LLM_TEMPERATURE,
    ):
        """
        初始化 LLM 客户端。

        Args:
            provider: "dashscope" 或 "openai"，默认自动推断。
            model: 模型名称，默认读取配置。
            api_key: API Key，默认读取配置。
            api_base: OpenAI 兼容接口的 base URL。
            timeout: 请求超时（秒）。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度。
        """
        self.provider = provider or config.get_llm_provider()
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

        if self.provider == "dashscope":
            self.model = model or config.DEFAULT_MODEL
            self.api_key = api_key or config.DASHSCOPE_API_KEY
            if not self.api_key:
                raise LLMError("使用 DashScope 需要提供 DASHSCOPE_API_KEY 环境变量")
            self._init_dashscope()
        elif self.provider == "openai":
            self.model = model or config.OPENAI_MODEL
            self.api_key = api_key or config.OPENAI_API_KEY
            self.api_base = api_base or config.OPENAI_API_BASE
            if not self.api_key or not self.api_base:
                raise LLMError("使用 OpenAI 兼容接口需要提供 OPENAI_API_KEY 和 OPENAI_API_BASE")
            self._init_openai()
        else:
            raise LLMError(
                "未配置任何可用的 LLM 提供商。请设置 DASHSCOPE_API_KEY "
                "或 OPENAI_API_BASE + OPENAI_API_KEY 环境变量。"
            )

    # ==================== DashScope 初始化与调用 ====================
    def _init_dashscope(self):
        try:
            import dashscope
            dashscope.api_key = self.api_key
            self._dashscope = dashscope
        except ImportError as e:
            raise LLMError("缺少 dashscope 库，请执行: pip install dashscope") from e

    def _call_dashscope(self, messages: List[Dict[str, str]]) -> str:
        from dashscope import Generation

        response = Generation.call(
            model=self.model,
            messages=messages,
            result_format="message",
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout=self.timeout,
        )

        if response.status_code != 200:
            raise LLMError(f"DashScope 请求失败: {response.status_code} - {response.message}")

        return response.output.choices[0].message.content

    # ==================== OpenAI 兼容接口初始化与调用 ====================
    def _init_openai(self):
        try:
            import openai
            self._openai = openai
        except ImportError as e:
            raise LLMError("缺少 openai 库，请执行: pip install openai") from e

        self.openai_client = self._openai.OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout,
        )

    def _call_openai(self, messages: List[Dict[str, str]]) -> str:
        response = self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return response.choices[0].message.content

    # ==================== 统一调用接口 ====================
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """
        调用 LLM 生成回答。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词（含问题与上下文）。

        Returns:
            {
                "content": str,      # 模型生成的文本
                "model": str,        # 实际使用的模型名
                "provider": str,     # 使用的提供商
                "elapsed_ms": int,   # 耗时（毫秒）
            }
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        start = time.time()
        try:
            if self.provider == "dashscope":
                content = self._call_dashscope(messages)
            elif self.provider == "openai":
                content = self._call_openai(messages)
            else:
                raise LLMError(f"不支持的 LLM 提供商: {self.provider}")
        except Exception as e:
            raise LLMError(f"LLM 生成失败: {e}") from e

        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "content": content.strip(),
            "model": self.model,
            "provider": self.provider,
            "elapsed_ms": elapsed_ms,
        }

    def is_available(self) -> bool:
        """检查当前客户端是否配置了可用的 LLM"""
        return self.provider in ("dashscope", "openai")


# 简单工厂函数
def create_llm_client() -> Optional[LLMClient]:
    """根据环境变量创建 LLMClient，未配置时返回 None"""
    try:
        return LLMClient()
    except LLMError:
        if config.DEBUG:
            traceback.print_exc()
        return None
