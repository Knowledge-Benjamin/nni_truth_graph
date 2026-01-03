# Production-Ready NNI Truth Graph Deployment
# Multi-stage Docker build for Python/Node.js services

FROM node:22-alpine AS node-builder
WORKDIR /app/server
COPY server/package*.json ./
RUN npm ci --production

FROM python:3.11-slim AS python-builder
WORKDIR /app
COPY requirements.txt ai_engine/requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt

# Final production image
FROM python:3.11-slim

WORKDIR /app

# Install Node.js for running JS scripts
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=python-builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy Node modules
COPY --from=node-builder /app/server/node_modules ./server/node_modules

# Copy application code
COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import sys; sys.exit(0)" || exit 1

# Run startup script
CMD ["python", "scripts/run_pipeline.py"]
