FROM node:22-alpine AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ .
RUN npx vite build

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

# Copy source code
COPY src/ src/
COPY web/ web/
# Copy built frontend assets from the frontend stage
COPY --from=frontend /build/dist web/dist
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Default: start the backend pipeline
CMD ["python", "-m", "opennews.main"]
