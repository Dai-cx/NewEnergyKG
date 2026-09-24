# ============================================================
# NewEnergyKG —— 问答 API 服务镜像
# 基础镜像：python:3.11-slim（Debian slim，体积小）
#
# 构建：docker compose build
#   或：docker build -t newenergykg-api .
# ============================================================
FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE：不生成 __pycache__，减小镜像体积
# PYTHONUNBUFFERED：stdout 不缓冲，docker logs 能实时看到日志
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 容器内工作目录
WORKDIR /app

# ---------- 第 1 层：安装依赖（利用 Docker 层缓存） ----------
# 先把依赖清单复制进来并安装；以后只改代码时，这一层不会重新执行，
# 已下载的依赖会被复用，构建速度大幅提升。
# （先放一个空的 qa/__init__.py 是为了让 pip install . 能发现 qa 包）
COPY pyproject.toml README.md ./
RUN mkdir -p qa && touch qa/__init__.py \
    && pip install --no-cache-dir .

# ---------- 第 2 层：复制应用代码 ----------
COPY qa ./qa

# ---------- 第 3 层：复制运行时数据与静态资源 ----------
COPY data ./data
COPY static ./static

# 容器内监听端口（文档性声明，真正映射在 docker-compose.yml 里）
EXPOSE 8000

# 健康检查：请求 /health 接口，供 compose 的 depends_on 判断服务是否就绪
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# 与本地开发一致的启动命令
CMD ["python", "-m", "qa.main"]
