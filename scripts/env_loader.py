"""
Environment loader utility for both local and Render deployment
- Tries to load from local .env file if it exists
- Falls back to system environment variables (for Render/production)
"""
import os
from dotenv import load_dotenv

def load_env():
    """
    Load environment variables with fallback support.
    
    On local development: Loads from ai_engine/.env if it exists
    On Render: Uses system environment variables set in render.yaml dashboard
    
    CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars.
    It only loads from .env files. On Render, system env vars are already available
    via os.getenv() - they're set by render.yaml in the container environment.
    NEVER call load_dotenv() without a path argument on Render - it doesn't help.
    """
    env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
    
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✅ Loaded environment from {env_path}")
    else:
        # On Render: .env doesn't exist, but system env vars ARE available
        # They're set by render.yaml and accessible via os.getenv()
        print("ℹ️  No .env file found - using system environment variables (expected on Render)")
