import os
from huggingface_hub import HfApi  # type: ignore

# Use the dedicated KnowledgeBenji write token from ENV
TOKEN = os.getenv("HF_TOKEN")
api = HfApi(token=TOKEN)
user = api.whoami()['name']
repo_id = f"{user}/cc-proxy"

print(f"Deploying proxy to Space: {repo_id}")

# Create space if not exists
api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
print("Space created / already exists.")

# Upload files
for fname in ['app.py', 'requirements.txt', 'Dockerfile']:
    path = os.path.join(os.path.dirname(__file__), fname)
    api.upload_file(path_or_fileobj=path, path_in_repo=fname, repo_id=repo_id, repo_type="space")
    print(f"  Uploaded {fname}")

proxy_url = f"https://{user.lower()}-cc-proxy.hf.space"
print(f"\nDone! Proxy URL: {proxy_url}")
print("Update 5_resolution.py CC_PROXY_BASE to:", proxy_url)
