# Use Python 3.10 slim image optimized for ML
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install OS dependencies required for Pillow and Transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Use Hugging Face cache volume for persistent model storage across Space restarts
ENV TRANSFORMERS_CACHE=/data/hf_cache
ENV HF_HOME=/data/hf_cache
VOLUME /data/hf_cache

COPY . .

# Hugging Face Spaces strictly routes to 7860 natively
EXPOSE 7860

# Run FastAPI via uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--log-level", "info"]
