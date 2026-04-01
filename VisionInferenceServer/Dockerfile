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

# Pre-download all Triple-Transformer arrays at build time strictly AS the non-root user.
# This obliterates the 2.5GB model network delay when Cloud Run instances cold start!
RUN python -c "from transformers import AutoProcessor, AutoModel, AutoImageProcessor, AutoModelForImageClassification; \
AutoProcessor.from_pretrained('google/siglip-base-patch16-224'); AutoModel.from_pretrained('google/siglip-base-patch16-224'); \
AutoImageProcessor.from_pretrained('umm-maybe/AI-image-detector'); AutoModelForImageClassification.from_pretrained('umm-maybe/AI-image-detector'); \
AutoImageProcessor.from_pretrained('dima806/deepfake_vs_real_image_detection'); AutoModelForImageClassification.from_pretrained('dima806/deepfake_vs_real_image_detection')"

WORKDIR $HOME/app
COPY --chown=user . $HOME/app

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--log-level", "info"]
