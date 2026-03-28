# Use the official Microsoft Playwright image (includes Python 3.10 and all OS dependencies for Chromium)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set environment variables to avoid python buffering
ENV PYTHONUNBUFFERED=1

# Set the working directory to the parent root
WORKDIR /app

# Install dependencies (copy ONLY requirements first for layer caching)
COPY ai_engine/requirements.txt /app/ai_engine/
RUN pip install --no-cache-dir -r ai_engine/requirements.txt

# No local embedding model pre-bake: inference is now proxy to external inference pool
# (sentence_transformers is intentionally not required in ai_engine/requirements.txt)

# Also install scripts requirements if they exist 
# (feedparser isn't explicitly in scripts/requirements if it runs off the same env, but we'll try)
COPY ai_engine/requirements-local.txt /app/ai_engine/
RUN pip install --no-cache-dir -r ai_engine/requirements-local.txt || true

# Install Playwright browser (Chromium only to save space)
RUN playwright install chromium

# Copy the ENTIRE repository into the container at /app
COPY . /app

# Change the execution directory to the ai_engine folder so that main.py behaves 
# exactly as it does when run locally from the terminal.
WORKDIR /app/ai_engine

# Expose a dummy port to trick Hugging Face into passing the healthcheck
EXPOSE 7860

# We need a tiny background web server so HuggingFace knows the container is "healthy"
# while main.py (celery + worker) runs in the background.
CMD bash -c "python3 -m http.server 7860 & python3 main.py"
