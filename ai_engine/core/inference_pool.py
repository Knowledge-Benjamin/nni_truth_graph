from dotenv import load_dotenv
import os
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))
"""
ai_engine/core/inference_pool.py
────────────────────────────────────────────────────────────────────────────
Remote embedding pool — connects to the dedicated Inference Server.

Makes HTTP requests to the Hugging Face Spaces inference server for
sentence-transformers/all-mpnet-base-v2 embeddings. Includes retries,
rate limiting, and error handling.

Public API (drop-in replacement for hf_pool):
    from ai_engine.core.inference_pool import inference_pool

    embedding = inference_pool.embed(text)
    embedding = inference_pool.embed(text, url)  # url can override default

Returns: list[float] (768 dimensions)
"""

import os
import time
import requests
import threading
from typing import Optional

# Configuration
DEFAULT_URL = os.getenv("INFERENCE_SERVER_URL", "https://knowledgebenji-inferencesever.hf.space")
API_KEY = os.getenv("INFERENCE_API_KEY", "default-key-change-in-production")
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

class InferenceEmbedPool:
    """
    Thread-safe pool for remote embedding inference.
    Handles retries, cooldowns on 429/503, and connection pooling.
    """

    def __init__(self, base_url: str = DEFAULT_URL, api_key: str = API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()  # Connection pooling
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
        self._lock = threading.Lock()
        self.cooldown_until = 0.0

    def _is_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def _set_cooldown(self, seconds: int = 60):
        self.cooldown_until = time.time() + seconds

    def _make_request(self, texts: list[str]) -> Optional[list]:
        """Make a single request with retries."""
        url = f"{self.base_url}/embed"
        payload = {"texts": texts}

        for attempt in range(MAX_RETRIES):
            try:
                if self._is_cooldown():
                    time.sleep(RETRY_DELAY)
                    continue

                response = self.session.post(url, json=payload, timeout=TIMEOUT)
                response.raise_for_status()
                data = response.json()
                return data["embeddings"]

            except requests.exceptions.HTTPError as e:
                if response.status_code in (429, 503):
                    print(f"[InferencePool] Rate limited (429/503). Cooling for {RETRY_DELAY}s.")
                    self._set_cooldown(RETRY_DELAY)
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"[InferencePool] HTTP error {response.status_code}: {e}")
                    if attempt == MAX_RETRIES - 1:
                        raise
            except requests.exceptions.RequestException as e:
                print(f"[InferencePool] Request error: {e}")
                if attempt == MAX_RETRIES - 1:
                    raise
            except KeyError:
                print("[InferencePool] Invalid response format")
                raise

        return None

    def embed(self, text: str, url: Optional[str] = None, timeout: int = TIMEOUT) -> list:
        """
        Generate a 768-dim embedding for `text` via remote inference server.

        `url` can override the default server URL.
        `timeout` is accepted for compatibility but uses instance default.

        Returns: list[float] (768 dimensions), never None (raises on failure).
        """
        if url:
            # Temporary override for this call
            temp_pool = InferenceEmbedPool(url, self.api_key)
            return temp_pool.embed(text)

        with self._lock:
            result = self._make_request([text])
            if result and len(result) == 1:
                return result[0]
            else:
                raise RuntimeError("[InferencePool] Failed to get embedding")

    def embed_batch(self, texts: list[str], url: Optional[str] = None) -> list[list]:
        """
        Batch embed multiple texts. More efficient than calling embed() in a loop.
        """
        if url:
            temp_pool = InferenceEmbedPool(url, self.api_key)
            return temp_pool.embed_batch(texts)

        with self._lock:
            result = self._make_request(texts)
            if result:
                return result
            else:
                raise RuntimeError("[InferencePool] Failed to get embeddings")

    @property
    def token_count(self) -> int:
        """Always 1 — single server."""
        return 1

    @property
    def available_count(self) -> int:
        return 0 if self._is_cooldown() else 1

    def health_check(self) -> bool:
        """Call inference server health endpoint to keep it warm and verify availability."""
        url = f"{self.base_url}/health"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                print(f"[InferencePool] Health OK {url}")
                return True
            print(f"[InferencePool] Health check returned {response.status_code}: {response.text}")
            return False
        except requests.RequestException as exc:
            print(f"[InferencePool] Health check error: {exc}")
            return False

    def status(self) -> dict:
        return {
            "mode": "remote",
            "url": self.base_url,
            "cooldown": self._is_cooldown(),
            "cooldown_until": self.cooldown_until
        }

# ── Module-level singleton ────────────────────────────────────
inference_pool = InferenceEmbedPool()