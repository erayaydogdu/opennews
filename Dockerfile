FROM python:3.13-slim

WORKDIR /app

# 系统依赖（psycopg2-binary 需要 libpq）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 直接复制本地已安装好的 venv，跳过 pip install
COPY .venv /app/.venv

# 复制源码与已构建的前端产物
COPY src/ src/
COPY web/ web/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# 默认启动后端流水线
CMD ["python", "-m", "opennews.main"]
