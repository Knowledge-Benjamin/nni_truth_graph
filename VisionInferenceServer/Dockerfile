# Use Python 3.10 slim image optimized for ML
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

# Install OS dependencies required for Pillow and Transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create HF-compatible non-root user (UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    TRANSFORMERS_CACHE=/home/user/.cache/huggingface/hub \
    HF_HOME=/home/user/.cache/huggingface

# Instead of loading into RAM (which risks OOM on GitHub Actions), we securely download directly to disk cache.
RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('google/siglip-base-patch16-224'); \
    snapshot_download('umm-maybe/AI-image-detector'); \
    snapshot_download('dima806/deepfake_vs_real_image_detection')"

WORKDIR $HOME/app
COPY --chown=user . $HOME/app

EXPOSE 7860

CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860} --log-level info"
