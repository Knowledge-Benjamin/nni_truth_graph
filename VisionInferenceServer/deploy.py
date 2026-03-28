import os
from huggingface_hub import HfApi  # type: ignore
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

# Use the dedicated KnowledgeBenji write token from ENV
TOKEN = os.getenv("HF_TOKEN_70B")  # KnowledgeBenji account token
api = HfApi(token=TOKEN)
ORG = "KnowledgeBenji"
repo_id = f"{ORG}/VisionInferenceServer"

print(f"Deploying Vision Inference Server to Space: {repo_id}")

# Create space if not exists
api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
print("Space created / already exists.")

# Upload files
for fname in ['main.py', 'requirements.txt', 'Dockerfile', 'README.md']:
    path = os.path.join(os.path.dirname(__file__), fname)
    api.upload_file(path_or_fileobj=path, path_in_repo=fname, repo_id=repo_id, repo_type="space")
    print(f"  Uploaded {fname}")

proxy_url = f"https://knowledgebenji-visioninferenceserver.hf.space"
print(f"\nDone! Vision Inference Server URL: {proxy_url}")
print("Update ai_engine/.env to include VISION_INFERENCE_URL =", proxy_url)
