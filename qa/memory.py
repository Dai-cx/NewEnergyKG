#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话记忆管理模块（纯内存实现）

按 session_id 维护最近 N 轮问答历史，用于多轮对话中的指代消解与上下文理解。
服务重启后记忆会丢失，符合当前"方案 A"的轻量设计。
"""

from collections import deque
from threading import Lock
from typing import Dict, List, Optional

from qa import config


class ConversationMemory:
    """会话记忆管理器"""

    def __init__(self, max_rounds: Optional[int] = None):
        """
        Args:
            max_rounds: 每个会话保留的最大轮数，默认从 config.MAX_HISTORY_ROUNDS 读取
        """
        self.max_rounds = max_rounds if max_rounds is not None else config.MAX_HISTORY_ROUNDS
        # session_id -> deque of {"role": "user"|"assistant", "content": str}
        # deque 的 maxlen 设置为轮数 * 2，因为每轮包含一条用户消息和一条助手消息
        self._sessions: Dict[str, deque] = {}
        self._lock = Lock()

    def get_history(self, session_id: Optional[str]) -> List[Dict[str, str]]:
        """
        获取指定会话的历史记录。

        Args:
            session_id: 会话 ID，为空时返回空列表

        Returns:
            历史消息列表，每个消息包含 role 和 content
        """
        if not session_id:
            return []
        with self._lock:
            return list(self._sessions.get(session_id, deque()))

    def add_exchange(
        self,
        session_id: Optional[str],
        question: str,
        answer: str,
    ) -> None:
        """
        向指定会话添加一轮问答。

        Args:
            session_id: 会话 ID，为空时不保存
            question: 用户问题
            answer: 助手回答
        """
        if not session_id:
            return
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = deque(maxlen=self.max_rounds * 2)
            self._sessions[session_id].append({"role": "user", "content": question})
            self._sessions[session_id].append({"role": "assistant", "content": answer})

    def clear(self, session_id: Optional[str] = None) -> None:
        """
        清空会话记忆。

        Args:
            session_id: 指定会话 ID，为空时清空所有会话
        """
        with self._lock:
            if session_id:
                self._sessions.pop(session_id, None)
            else:
                self._sessions.clear()

    def count(self) -> int:
        """返回当前活跃会话数"""
        with self._lock:
            return len(self._sessions)
