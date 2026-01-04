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
    On Render: Uses system environment variables set in dashboard
    """
    env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
    
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # On Render, .env doesn't exist - rely on environment variables
        load_dotenv()
