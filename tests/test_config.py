# -*- coding: utf-8 -*-
"""
qa/config.py 配置模块的单元测试

注意：config 中的常量在导入时就从环境变量读取，
所以测试"默认值"时不做环境变量修改；测试 get_llm_provider 时
用 monkeypatch 直接修改 config 模块属性（该函数调用时读取模块全局变量）。
"""

import pytest

from qa import config


class TestDefaults:
    """默认配置值（未设置任何环境变量时）"""

    def test_default_neo4j_settings(self):
        assert config.NEO4J_URI == "bolt://localhost:7687"
        assert config.NEO4J_USER == "neo4j"
        assert config.NEO4J_PASSWORD == ""

    def test_default_llm_settings(self):
        assert config.DEFAULT_MODEL == "qwen-turbo"
        assert config.LLM_TIMEOUT == 30
        assert config.LLM_MAX_TOKENS == 1024
        assert config.LLM_TEMPERATURE == 0.3

    def test_default_service_settings(self):
        assert config.APP_HOST == "127.0.0.1"
        assert config.APP_PORT == 8000

    def test_default_entity_and_memory_settings(self):
        assert config.ENTITY_FUZZY_THRESHOLD == 85
        assert config.MAX_HISTORY_ROUNDS == 5

    def test_default_logging_settings(self):
        assert config.LOG_LEVEL == "INFO"
        assert config.LOG_FILE == ""

    def test_data_path_points_to_project_data(self):
        # data/new_energy.json 必须在项目中真实存在（测试的前置条件）
        assert config.DATA_PATH.name == "new_energy.json"
        assert config.DATA_PATH.exists()


class TestGetLLMProvider:
    """LLM 提供商推断逻辑（优先级：openai > dashscope > None）"""

    def test_none_when_no_key(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        monkeypatch.setattr(config, "OPENAI_API_BASE", "")
        assert config.get_llm_provider() is None

    def test_dashscope_when_only_dashscope(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setattr(config, "OPENAI_API_KEY", "")
        monkeypatch.setattr(config, "OPENAI_API_BASE", "")
        assert config.get_llm_provider() == "dashscope"

    def test_openai_when_both(self, monkeypatch):
        monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-dash")
        monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-openai")
        monkeypatch.setattr(config, "OPENAI_API_BASE", "https://api.example.com/v1")
        assert config.get_llm_provider() == "openai"


class TestCheckDataPath:
    """数据文件路径校验"""

    def test_missing_file_raises(self, monkeypatch, tmp_path):
        missing = tmp_path / "no_such_file.json"
        monkeypatch.setattr(config, "DATA_PATH", missing)
        with pytest.raises(FileNotFoundError):
            config.check_data_path()

    def test_existing_file_returns_path(self, monkeypatch, sample_data_path):
        monkeypatch.setattr(config, "DATA_PATH", sample_data_path)
        assert config.check_data_path() == sample_data_path
