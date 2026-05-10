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
    "GOOGLE_AI_STUDIO": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "weight": 100,
        # All env vars whose values should be treated as Google AI Studio keys.
        # Any of these keys can serve LIGHT, HEAVY, or VISION requests.
        "env_keys": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "model_light":  "gemma-4-4b-it",
        "model_heavy":  "gemma-4-26b-moe-it",
        "model_vision": "gemma-4-multimodal-it"
    },
    "GROQ": {
        "base_url": None,  # Uses native Groq client
        "weight": 0,       # Disabled: Groq does not host Gemma 4
        "env_keys": ["GROQ_API_KEY"],
        "model_light":  "gemma-4-4b-it",
        "model_heavy":  "gemma-4-31b-it",
        "model_vision": "gemma-4-multimodal-it"
    },
    "OPENROUTER": {
        "base_url": "https://openrouter.ai/api/v1",
        "weight": 50,
        "env_keys": ["OPENROUTER_API_KEY"],
        "model_light":  "google/gemma-4-4b-it:free",
        "model_heavy":  "google/gemma-4-31b-it:free",
        "model_vision": "google/gemma-4-multimodal-it:free"
    },
    "GITHUB": {
        "base_url": "https://models.inference.ai.azure.com",
        "weight": 50,
        "env_keys": ["GITHUB_API_KEY"],
        "model_light":  "google-gemma-4-4b-it",
        "model_heavy":  "google-gemma-4-31b-it"
    },
    "HUGGINGFACE": {
        "base_url": "https://router.huggingface.co/hf-inference/v1",
        "weight": 25,
        "env_keys": ["HF_TOKEN"],
        "model_light":  "google/gemma-4-4b-it",
        "model_heavy":  "google/gemma-4-31b-it"
    }
}

def _load_all_keys_for_provider(env_key_list: list[str]) -> list[str]:
    """
    Scans all env vars that start with any prefix in env_key_list, including
    numbered suffixes (_2, _3, ...) and any suffix variant (_HEAVY, _VISION, etc.).
    Returns a deduplicated list of non-empty key strings.
    All keys land in the same flat pool — there is zero tier segmentation at the key level.
    """
    seen: set[str] = set()
    results: list[str] = []

    for prefix in env_key_list:
        # Collect exact match and any env var that starts with `prefix`
        for env_var, val in os.environ.items():
            if env_var == prefix or env_var.startswith(f"{prefix}_"):
                v = val.strip()
                if v and v not in seen:
                    seen.add(v)
                    results.append(v)

    return results


class RoutedClient:
    """Wrapper holding an instructor client and its routing metadata."""
    def __init__(self, provider_name: str, api_key: str, p_config: dict):
        self.provider = provider_name
        self.weight = p_config["weight"]
        self.p_config = p_config  # Stores model_light, model_heavy, model_vision

        self.key_preview = f"…{api_key[-6:]}"
        self.api_key_exact = api_key
        self.cooldown_until = 0.0

        if provider_name == "GROQ":
            groq_client = Groq(api_key=api_key)
            self.raw_client = groq_client  # type: ignore
            self.client = instructor.from_groq(groq_client, mode=instructor.Mode.JSON)
        else:
            openai_client = OpenAI(base_url=p_config["base_url"], api_key=api_key)
            self.raw_client = openai_client  # type: ignore
            self.client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON)

    def is_cooling_down(self) -> bool:
        return time.time() < self.cooldown_until

    def mark_rate_limited(self):
        penalty = random.uniform(COOLDOWN_MIN, COOLDOWN_MAX)
        self.cooldown_until = time.time() + penalty
        print(f"[Router · {self.provider}] {self.key_preview} hit 429. Cooling for {penalty:.0f}s.")


class MultiProviderRouter:
    def __init__(self):
        self.clients_universal: list[RoutedClient] = []
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self):
        """
        Populate the flat universal pool from all env keys for all providers.
        Every unique key string gets exactly one RoutedClient entry — no
        segmentation by tier. Any key can serve any tier request.
        """
        seen_keys: set[str] = set()

        for p_name, p_config in PROVIDERS.items():
            for key in _load_all_keys_for_provider(p_config["env_keys"]):
                if key not in seen_keys:
                    seen_keys.add(key)
                    self.clients_universal.append(RoutedClient(p_name, key, p_config))

        print(f"[LLM Router] Initialized Universal Pool with {len(self.clients_universal)} keys globally.")
        if not self.clients_universal:
            message = (
                "[LLM Router] ERROR: No LLM API keys found in environment. "
                "Check .env for GROQ_API_KEY | GOOGLE_API_KEY | OPENROUTER_API_KEY | "
                "GITHUB_API_KEY | HF_TOKEN."
            )
            print(message)
            raise RuntimeError(message)

    def _select_client(self, tier: str) -> tuple[RoutedClient, str] | tuple[None, None]:
        """Weighted roulette selection of a healthy client that explicitly supports the requested tier."""
        with self._lock:
            # Filter the universal pool to contain only healthy clients that actively have a mapping for this tier
            valid_pool = []
            for c in self.clients_universal:
                if not c.is_cooling_down():
                    mapped_string = None
                    if tier == "VISION" and "model_vision" in c.p_config:
                        mapped_string = c.p_config["model_vision"]
                    elif tier == "HEAVY" and "model_heavy" in c.p_config:
                        mapped_string = c.p_config["model_heavy"]
                    elif tier == "LIGHT" and "model_light" in c.p_config:
                        mapped_string = c.p_config["model_light"]
                    
                    if mapped_string:
                        valid_pool.append((c, mapped_string))
            
            if not valid_pool:
                return None, None
            
            weights = [c[0].weight for c in valid_pool]
            selected_tuple = random.choices(valid_pool, weights=weights, k=1)[0]
            return selected_tuple

    def chat_completions_create(self, **kwargs) -> Any:
        """
        Drop-in replacement for the old groq_pool.chat_completions_create.
        Intercepts the requested model, determines the tier, picks a provider 
        via weighted roulette, maps the model string, applies jitter, and executes.
        If a 429 is hit, it falls back seamlessly until MAX_FALLBACK_LOOPS is reached.
        """
        req_model = kwargs.get("model", "")
        # Determine tier from the requested model string
        if req_model == "TIER_VISION" or "vision" in req_model.lower() or "llava" in req_model.lower() or "vl" in req_model.lower():
            tier = "VISION"
        elif req_model == "TIER_HEAVY" or "70b" in req_model.lower() or "heavy" in req_model.lower():
            tier = "HEAVY"
        else:
            tier = "LIGHT"
        
        exceptions = []
        
        for attempt in range(1, MAX_FALLBACK_LOOPS + 1):
            client_wrapper, mapped_model = self._select_client(tier)
            
            if not client_wrapper:
                # All keys that support this tier are chilling. Wait briefly and retry.
                time.sleep(2)
                continue
                
            # Anti-Bot Evasion: Inject Micro-Jitter
            time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            
            # Map the outgoing request to the precise model string the provider expects dynamically!
            call_kwargs = kwargs.copy()
            call_kwargs["model"] = mapped_model
            
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
                    print(f"[LLM Router] provider={client_wrapper.provider}, requested_tier={tier}, mapped_model={mapped_model}, choices_type={type(choices)}, choices_len={len(choices) if choices is not None else 'None'}")
                except Exception as inner_e:
                    print(f"[LLM Router] failed to inspect choices: {inner_e}")

                if "response_model" not in call_kwargs or call_kwargs.get("response_model") is None:
                    # For plain text completions we expect choices from provider
                    if not getattr(response, "choices", None):
                        raise ValueError(f"Empty or missing choices from provider {client_wrapper.provider} ({mapped_model}), response={response}")

                # For structured response_model paths, we accept any valid parsed object.
                return response
                
            except (GroqRateLimitError, OpenAIRateLimitError, NotFoundError, InternalServerError) as e:
                # Catch actual rate limits, as well as hard model-blocks (Not Found - HF) 
                # and upstream crashes (Internal Server Error - OpenRouter Free)
                client_wrapper.mark_rate_limited()
                exceptions.append(e)
            except Exception as e:
                # Catch other API connection errors that act like rate limits / timeouts
                if any(err_str in str(e).lower() for err_str in ["429", "too many requests", "timeout", "500", "404", "401", "400", "unauthorized", "bad request"]):
                    client_wrapper.mark_rate_limited()
                else:
                    # If it's a validation error or something structural, raise it immediately
                    raise e
                exceptions.append(e)
                
        # If we exit the loop, we are totally exhausted
        raise Exception(f"[LLM Router] Failed after {MAX_FALLBACK_LOOPS} routed attempts. Last error: {exceptions[-1]}")

# Export Singleton
llm_pool = MultiProviderRouter()
