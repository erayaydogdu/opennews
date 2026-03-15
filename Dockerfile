FROM python:3.10-slim

WORKDIR /app

# 系统依赖（psycopg2-binary 需要 libpq）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY web/ web/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# 默认启动后端流水线
CMD ["python", "-m", "opennews.main"]
