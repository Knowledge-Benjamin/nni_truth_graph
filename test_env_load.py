import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), 'ai_engine/.env')

# Test the resilient loading approach
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"✅ Loaded .env from {env_path}")
else:
    load_dotenv()
    print("ℹ️ No .env file found - using system environment variables")

# Check if GROQ_API_KEY is loaded
groq_key = os.getenv("GROQ_API_KEY")
if groq_key:
    print(f"✅ GROQ_API_KEY loaded: {groq_key[:20]}...")
else:
    print("❌ GROQ_API_KEY not loaded")

# Check all critical keys
keys = ["DATABASE_URL", "NEO4J_URI", "NEO4J_PASSWORD", "GROQ_API_KEY", "HF_TOKEN"]
print("\n📋 Environment variables status:")
for key in keys:
    value = os.getenv(key)
    status = "✅" if value else "❌"
    print(f"  {status} {key}: {'set' if value else 'missing'}")
