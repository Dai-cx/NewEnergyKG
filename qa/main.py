#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新能源知识图谱问答系统 - FastAPI 服务入口

提供以下接口：
- GET  /           : 返回系统启动器页面（static/index.html）
- GET  /chat       : 返回智能问答助手页面（static/chat.html）
- POST /qa         : 问答接口
- GET  /health     : 健康检查
- GET  /status     : 引擎状态
- /static/*        : 静态资源（HTML/CSS/JS）

启动方式：
    cd d:\\NewEnergyKG-myself
    python -m qa.main

或使用 uvicorn：
    uvicorn qa.main:app --reload --host 127.0.0.1 --port 8000
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pathlib import Path

from qa import config
from qa.answer_engine import AnswerEngine

# ==================== FastAPI 应用实例 ====================
app = FastAPI(
    title="新能源知识图谱问答系统",
    description="KG+RAG 版问答系统：基于 Neo4j 知识图谱检索 + LLM 自然语言生成。",
    version="0.2.0",
)

# ==================== 挂载静态文件 ====================
# 将项目根目录 static/ 下的文件通过 /static 路径提供
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ==================== 全局问答引擎 ====================
engine = AnswerEngine()


# ==================== 请求/响应模型 ====================
class QARequest(BaseModel):
    question: str


class QAResponse(BaseModel):
    question: str
    answer: str
    intent: str
    entities: list
    llm_used: bool
    llm_model: str | None
    source: str
    response_time_ms: int


# ==================== 页面路由 ====================
@app.get("/", response_class=HTMLResponse)
def index():
    """返回系统启动器页面"""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="前端页面未找到")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/chat", response_class=HTMLResponse)
def chat():
    """返回智能问答助手页面"""
    chat_path = STATIC_DIR / "chat.html"
    if not chat_path.exists():
        raise HTTPException(status_code=500, detail="聊天页面未找到")
    with open(chat_path, "r", encoding="utf-8") as f:
        return f.read()


# ==================== API 路由 ====================
@app.post("/qa", response_model=QAResponse)
def qa_endpoint(req: QARequest):
    """
    问答接口

    请求示例：
        {
            "question": "磷酸铁锂电池有哪些优点？"
        }
    """
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    result = engine.answer(req.question.strip())
    return result


@app.get("/health")
def health_check():
    """健康检查"""
    return {"status": "ok"}


@app.get("/status")
def status():
    """引擎状态"""
    return engine.get_status()


@app.get("/kg/status")
def kg_status():
    """知识图谱连接状态与规模统计"""
    return engine.kg_client.get_status()


# ==================== 启动入口 ====================
if __name__ == "__main__":
    print("=" * 50)
    print("新能源知识图谱问答系统启动中...")
    print(f"访问地址: http://{config.APP_HOST}:{config.APP_PORT}")
    print(f"系统启动器: http://{config.APP_HOST}:{config.APP_PORT}/")
    print(f"问答助手: http://{config.APP_HOST}:{config.APP_PORT}/chat")
    print(f"接口文档: http://{config.APP_HOST}:{config.APP_PORT}/docs")
    print("=" * 50)
    uvicorn.run(app, host=config.APP_HOST, port=config.APP_PORT)
