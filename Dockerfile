FROM python:3.13-slim

WORKDIR /app

# System dependencies (libpq for psycopg2, gcc/g++ for hdbscan C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Remove build tools to keep image smaller
RUN apt-get purge -y --auto-remove gcc g++

# Copy source code and pre-built frontend assets
COPY src/ src/
COPY web/ web/
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Default: start the backend pipeline
CMD ["python", "-m", "opennews.main"]
