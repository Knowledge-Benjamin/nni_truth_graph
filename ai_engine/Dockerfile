# Docker build for Python AI Engine (Python 3.11 LTS)

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements from root and ai_engine
COPY requirements.txt /app/
COPY ai_engine/requirements.txt /app/ai_engine/

# Install Python dependencies with pip optimization
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r ai_engine/requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run the AI engine
CMD ["python", "ai_engine/main.py"]
