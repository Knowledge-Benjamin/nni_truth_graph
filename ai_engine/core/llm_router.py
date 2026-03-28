"""
ai_engine/core/llm_router.py
────────────────────────────────────────────────────────────────────────────
Advanced Multi-Provider LLM Router with Weighted Roulette & Request Jitter.

Replaces the old Groq-only pool. Load-balances across generous free-tier 
providers (Groq, Cerebras, Together AI, OpenRouter) to eliminate cost, 
evade bot-detection, and maximize throughput.
"""

import os
import time
import random
import threading
from typing import Any
import instructor
from dotenv import load_dotenv

# Ensure .env is loaded at import time for any script that imports this module first.
load_dotenv()

from groq import Groq, RateLimitError as GroqRateLimitError
from openai import OpenAI, RateLimitError as OpenAIRateLimitError, NotFoundError, InternalServerError

# ── Configuration ────────────────────────────────────────────────────────────
COOLDOWN_MIN        = 60   # minimum seconds a 429 rate-limited key sits out
COOLDOWN_MAX        = 180  # maximum seconds a 429 rate-limited key sits out
JITTER_MIN          = 0.1  # min seconds to sleep before request
JITTER_MAX          = 0.5  # max seconds to sleep before request
MAX_FALLBACK_LOOPS  = 10   # maximum attempts across all keys before giving up

PROVIDERS = {
    "GROQ": {
        "base_url": None, # Uses native Groq client
        "weight": 50,
        "env_8b": "GROQ_API_KEY",
        "env_70b": "GROQ_API_KEY_70B",
        "model_8b": "llama-3.1-8b-instant",
        "model_70b": "llama-3.3-70b-versatile"
    },
    "OPENROUTER": {
        "base_url": "https://openrouter.ai/api/v1",
        "weight": 25,
        "env_8b": "OPENROUTER_API_KEY",
        "env_70b": "OPENROUTER_API_KEY_70B",
        "model_8b": "meta-llama/llama-3.1-8b-instruct:free",
        "model_70b": "meta-llama/llama-3.3-70b-instruct:free"
    },
    "GITHUB": {
        "base_url": "https://models.inference.ai.azure.com",
        "weight": 25,
        "env_8b": "GITHUB_API_KEY",
        "env_70b": "GITHUB_API_KEY_70B",
        "model_8b": "meta-llama-3.1-8b-instruct",
        "model_70b": "Llama-3.3-70B-Instruct"
    },
    "HUGGINGFACE": {
        "base_url": "https://router.huggingface.co/hf-inference/v1",
        "weight": 25,
        "env_8b": "HF_TOKEN",
        "env_70b": "HF_TOKEN_70B",
        "model_8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "model_70b": "meta-llama/Meta-Llama-3.1-70B-Instruct"
    },
    "OPENROUTER_VISION": {
        "base_url": "https://openrouter.ai/api/v1",
        "weight": 100,
        "env_vision": "OPENROUTER_API_KEY", # Falls back to standard key
        "model_vision": "qwen/qwen2.5-vl-32b-instruct:free"  # Explicitly routed to Qwen-2.5 32B Vision (Free Tier)
    }
}

def _load_keys_for_prefix(prefix: str) -> list[str]:
    """Finds all keys starting with prefix (e.g., TOGETHER_API_KEY, TOGETHER_API_KEY_2)."""
    keys = []
    base_val = os.getenv(prefix, "").strip()
    if base_val:
        keys.append(base_val)

    for env_key, val in sorted(os.environ.items()):
        if env_key.startswith(f"{prefix}_") and val.strip():
            # Exclude the 70B variant when we are scanning the 8B prefix
            if prefix.endswith("_70B"):
                keys.append(val.strip())
            else:
                if not env_key.startswith(f"{prefix}_70B"):
                    keys.append(val.strip())

    seen = set()
    return [x for x in keys if not (x in seen or seen.add(x))]

class RoutedClient:
    """Wrapper holding an instructor client and its routing metadata."""
    def __init__(self, provider_name: str, api_key: str, tier: str, p_config: dict):
        self.provider = provider_name
        self.tier = tier # '8B', '70B', or 'VISION'
        self.weight = p_config["weight"]
        if tier == "VISION":
            self.mapped_model = p_config["model_vision"]
        else:
            self.mapped_model = p_config["model_8b"] if tier == "8B" else p_config["model_70b"]
        self.key_preview = f"…{api_key[-6:]}"
        self.cooldown_until = 0.0

        # Keep both the raw client (for normal text completions) and an
        # instructor‑wrapped client (for structured Pydantic responses).
        # Newer versions of `instructor` require `response_model` for the
        # wrapped client, so we *only* route calls that include a
        # `response_model` through instructor to avoid
        # "Instructor.create() missing 1 required positional argument: 'response_model'"
        # for plain-text generations (e.g. stance detection, article copy).
        if provider_name == "GROQ":
            self.raw_client = Groq(api_key=api_key)
            self.client = instructor.from_groq(self.raw_client, mode=instructor.Mode.JSON)
        else:
            self.raw_client = OpenAI(base_url=p_config["base_url"], api_key=api_key)
            self.client = instructor.from_openai(self.raw_client, mode=instructor.Mode.JSON)
            
    def is_cooling_down(self) -> bool:
        return time.time() < self.cooldown_until
        
    def mark_rate_limited(self):
        penalty = random.uniform(COOLDOWN_MIN, COOLDOWN_MAX)
        self.cooldown_until = time.time() + penalty
        print(f"[Router · {self.tier}] {self.provider} ({self.key_preview}) hit 429. Cooling for {penalty:.0f}s.")

class MultiProviderRouter:
    def __init__(self):
        self.clients_8b: list[RoutedClient] = []
        self.clients_70b: list[RoutedClient] = []
        self.clients_vision: list[RoutedClient] = []
        self._lock = threading.Lock()
        
        self._bootstrap()

    def _bootstrap(self):
        """Populate the 8B and 70B pools based on available .env keys."""
        for p_name, p_config in PROVIDERS.items():
            # Load 8B Keys
            keys_8b = _load_keys_for_prefix(p_config["env_8b"])
            for k in keys_8b:
                self.clients_8b.append(RoutedClient(p_name, k, "8B", p_config))
                
            # Load 70B Keys
            if "env_70b" in p_config:
                keys_70b = _load_keys_for_prefix(p_config["env_70b"])
                for k in keys_70b:
                    self.clients_70b.append(RoutedClient(p_name, k, "70B", p_config))
                    
            # Load VISION Keys
            if "env_vision" in p_config:
                keys_vision = _load_keys_for_prefix(p_config["env_vision"])
                for k in keys_vision:
                    self.clients_vision.append(RoutedClient(p_name, k, "VISION", p_config))
                
        print(f"[LLM Router] Initialized. 8B: {len(self.clients_8b)} | 70B: {len(self.clients_70b)} | VISION: {len(self.clients_vision)}")
        if not self.clients_8b and not self.clients_70b and not self.clients_vision:
            message = (
                "[LLM Router] ERROR: No LLM API keys found in environment. "
                "Check .env for GROQ_API_KEY | OPENROUTER_API_KEY | GITHUB_API_KEY | HF_TOKEN, "
                "or export variables before running the worker."
            )
            print(message)
            raise RuntimeError(message)


    def _select_client(self, tier: str) -> RoutedClient | None:
        """Weighted roulette selection of a healthy client."""
        if tier == "VISION":
            pool = self.clients_vision
        elif tier == "70B":
            pool = self.clients_70b
        else:
            pool = self.clients_8b
        
        with self._lock:
            healthy = [c for c in pool if not c.is_cooling_down()]
            if not healthy:
                return None
            
            weights = [c.weight for c in healthy]
            selected = random.choices(healthy, weights=weights, k=1)[0]
            return selected

    def chat_completions_create(self, **kwargs) -> Any:
        """
        Drop-in replacement for the old groq_pool.chat_completions_create.
        Intercepts the requested model, determines the tier, picks a provider 
        via weighted roulette, maps the model string, applies jitter, and executes.
        If a 429 is hit, it falls back seamlessly until MAX_FALLBACK_LOOPS is reached.
        """
        req_model = kwargs.get("model", "")
        # Determine tier from the legacy model names used broadly across the codebase
        if "vision" in req_model.lower() or "llava" in req_model.lower() or "vl" in req_model.lower():
            tier = "VISION"
        else:
            tier = "70B" if "70b" in req_model.lower() else "8B"
        
        exceptions = []
        
        for attempt in range(1, MAX_FALLBACK_LOOPS + 1):
            client_wrapper = self._select_client(tier)
            
            if not client_wrapper:
                # All keys in this tier are chilling. Wait briefly and retry.
                time.sleep(2)
                continue
                
            # Anti-Bot Evasion: Inject Micro-Jitter
            time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            
            # Map the outgoing request to the precise model string the provider expects
            call_kwargs = kwargs.copy()
            call_kwargs["model"] = client_wrapper.mapped_model
            
            try:
                # If the caller requested a structured Pydantic response via
                # `response_model=MyModel`, route through the instructor‑wrapped
                # client. Otherwise, use the raw OpenAI/Groq client so plain
                # text generations (ArticleWorker, stance detection, etc.)
                # don't hit Instructor's strict `response_model` requirement.
                if "response_model" in call_kwargs and call_kwargs["response_model"] is not None:
                    response = client_wrapper.client.chat.completions.create(**call_kwargs)
                else:
                    response = client_wrapper.raw_client.chat.completions.create(**call_kwargs)

                # Debugging: inspect provider response structure
                try:
                    choices = getattr(response, "choices", None)
                    print(f"[LLM Router] provider={client_wrapper.provider}, model={client_wrapper.mapped_model}, choices_type={type(choices)}, choices_len={len(choices) if choices is not None else 'None'}")
                except Exception as inner_e:
                    print(f"[LLM Router] failed to inspect choices: {inner_e}")

                if "response_model" not in call_kwargs or call_kwargs.get("response_model") is None:
                    # For plain text completions we expect choices from provider
                    if not getattr(response, "choices", None):
                        raise ValueError(f"Empty or missing choices from provider {client_wrapper.provider} ({client_wrapper.mapped_model}), response={response}")

                # For structured response_model paths, we accept any valid parsed object.
                return response
                
            except (GroqRateLimitError, OpenAIRateLimitError, NotFoundError, InternalServerError) as e:
                # Catch actual rate limits, as well as hard model-blocks (Not Found - HF) 
                # and upstream crashes (Internal Server Error - OpenRouter Free)
                client_wrapper.mark_rate_limited()
                exceptions.append(e)
            except Exception as e:
                # Catch other API connection errors that act like rate limits / timeouts
                if "429" in str(e) or "Too Many Requests" in str(e) or "timeout" in str(e).lower() or "500" in str(e) or "404" in str(e):
                    client_wrapper.mark_rate_limited()
                else:
                    # If it's a validation error or something structural, raise it immediately
                    raise e
                exceptions.append(e)
                
        # If we exit the loop, we are totally exhausted
        raise Exception(f"[LLM Router] Failed after {MAX_FALLBACK_LOOPS} routed attempts. Last error: {exceptions[-1]}")

# Export Singleton
llm_pool = MultiProviderRouter()
