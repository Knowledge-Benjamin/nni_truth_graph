"""
ai_engine/core/llm_router.py
────────────────────────────────────────────────────────────────────────────
Self-Hosted Gemma 2 (Ollama) Router.

Routes all LLM requests exclusively to the self-hosted Gemma 2 Ollama
backend running on Hugging Face Spaces. An Ollama shim translates
OpenAI-compat .chat.completions.create() calls into Ollama /api/chat
requests so the rest of the pipeline requires zero changes.

All third-party cloud providers (Google AI Studio, DeepInfra, OpenRouter,
Groq, GitHub) are weight-zeroed and disabled.
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

# Controls whether the engine routes to self-hosted local inference or to cloud API providers.
# Set EXECUTION_MODE=cloud to use cloud provider API keys, or EXECUTION_MODE=local to use local Ollama.
AIR_GAPPED_MODE = os.getenv("AIR_GAPPED_MODE", "false").lower() == "true"
LOCAL_INFERENCE_URL = os.getenv("LOCAL_INFERENCE_URL", "http://localhost:8000/v1")
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "cloud").strip().lower()

# Self-hosted Ollama backend (Hugging Face Spaces)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://bravadoben-gemma-proto-backend.hf.space")

from groq import Groq, RateLimitError as GroqRateLimitError
from openai import OpenAI, RateLimitError as OpenAIRateLimitError, NotFoundError, InternalServerError

# ── Configuration ────────────────────────────────────────────────────────────
COOLDOWN_MIN        = 60   # minimum seconds a 429 rate-limited key sits out
COOLDOWN_MAX        = 180  # maximum seconds a 429 rate-limited key sits out
JITTER_MIN          = 0.1  # min seconds to sleep before request
JITTER_MAX          = 0.5  # max seconds to sleep before request
MAX_FALLBACK_LOOPS  = 10   # maximum attempts across all keys before giving up

PROVIDERS = {
    # ── Self-Hosted Gemma 2 (Ollama on HF Spaces) ─────────────────────────────
    # Exclusive provider. All requests route here via the Ollama shim.
    # Uses a sentinel key "ollama" since no API key is required for the HF Space.
    "SELF_HOSTED_OLLAMA": {
        "base_url": OLLAMA_BASE_URL,  # Resolved at module load from OLLAMA_BASE_URL env var
        "weight": 100,
        "env_keys": ["OLLAMA_SENTINEL"],  # Sentinel — not a real key; shim bypasses auth
        "model_light":  "gemma2:9b",
        "model_heavy":  "gemma2:9b",
        "model_vision": "gemma2:9b"
    },
    # ── Disabled Cloud Providers ───────────────────────────────────────────────
    # All cloud providers are weight-zeroed. Routing is exclusively self-hosted.
    "GOOGLE_AI_STUDIO": {
        "base_url": None,
        "weight": 0,  # Disabled: self-hosted Ollama is exclusive
        "env_keys": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "model_light":  "gemma-4-26b-a4b-it",
        "model_heavy":  "gemma-4-26b-a4b-it",
        "model_vision": "gemma-4-26b-a4b-it"
    },
    "DEEPINFRA": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "weight": 0,  # Disabled: self-hosted Ollama is exclusive
        "env_keys": ["DEEPINFRA_API_KEY"],
        "model_light":  "google/gemma-4-26b-a4b-it",
        "model_heavy":  "google/gemma-4-31b-it",
        "model_vision": "google/gemma-4-multimodal-it"
    },
    "GROQ": {
        "base_url": None,
        "weight": 0,  # Disabled: Groq does not host Gemma 4 and self-hosted is exclusive
        "env_keys": ["GROQ_API_KEY"],
        "model_light":  "gemma-4-26b-a4b-it",
        "model_heavy":  "gemma-4-31b-it",
        "model_vision": "gemma-4-multimodal-it"
    },
    "OPENROUTER": {
        "base_url": "https://openrouter.ai/api/v1",
        "weight": 0,  # Disabled: self-hosted Ollama is exclusive
        "env_keys": ["OPENROUTER_API_KEY"],
        "model_light":  "google/gemma-4-26b-a4b-it:free",
        "model_heavy":  "google/gemma-4-31b-it:free",
        "model_vision": "google/gemma-4-multimodal-it:free"
    },
    "GITHUB": {
        "base_url": "https://models.inference.ai.azure.com",
        "weight": 0,  # Disabled: GITHUB_API_KEY returns 401 Unauthorized
        "env_keys": ["GITHUB_API_KEY"],
        "model_light":  "google-gemma-4-26b-a4b-it",
        "model_heavy":  "google-gemma-4-31b-it"
    },
    "HUGGINGFACE": {
        "base_url": "https://router.huggingface.co/hf-inference/v1",
        "weight": 0,  # Disabled: returns 400 Bad Request for models >10B parameters
        "env_keys": ["HF_TOKEN"],
        "model_light":  "google/gemma-4-26b-a4b-it",
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


# ── Google GenAI Native Shim ─────────────────────────────────────────────────
# The OpenAI-compat wrapper (/v1beta/openai/) returns 500 for Gemma models.
# These lightweight shims wrap genai.Client to expose the same interface that
# the rest of the pipeline expects (.chat.completions.create -> .choices[0].message.content),
# so zero changes are needed anywhere else in the codebase.

class _GenAIMessageShim:
    """Mimics openai ChatCompletionMessage."""
    def __init__(self, content: str):
        self.content = content
        self.role = "assistant"

class _GenAIChoiceShim:
    """Mimics openai Choice."""
    def __init__(self, content: str):
        self.message = _GenAIMessageShim(content)
        self.finish_reason = "stop"
        self.index = 0

class _GenAIResponseShim:
    """Mimics openai ChatCompletion so downstream code works unchanged."""
    def __init__(self, text: str):
        self.choices = [_GenAIChoiceShim(text)]

class _GenAICompletionsShim:
    """Translates OpenAI-style .create() kwargs into google-genai SDK calls."""
    def __init__(self, genai_client: Any):
        self._client = genai_client

    def create(self, *, model: str, messages: list, **kwargs) -> _GenAIResponseShim:
        from google.genai import types as _gt  # lazy import
        # Separate system prompt from conversation turns
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        conversation = [m for m in messages if m.get("role") != "system"]
        # Build contents list (multi-turn aware)
        contents = []
        for m in conversation:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
        # Build generation config — only pass params genai understands
        cfg: dict = {}
        if system_parts:
            cfg["system_instruction"] = " ".join(system_parts)
        if "temperature" in kwargs:
            cfg["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            cfg["max_output_tokens"] = kwargs["max_tokens"]
        config = _gt.GenerateContentConfig(**cfg) if cfg else None
        resp = self._client.models.generate_content(
            model=model, contents=contents, config=config
        )
        return _GenAIResponseShim(resp.text)

class _GenAIRawShim:
    """Top-level shim: exposes .chat.completions backed by google-genai."""
    class _Chat:
        def __init__(self, genai_client: Any):
            self.completions = _GenAICompletionsShim(genai_client)
    def __init__(self, genai_client: Any):
        self.chat = _GenAIRawShim._Chat(genai_client)
# ─────────────────────────────────────────────────────────────────────────────


# ── Ollama Native Shim ────────────────────────────────────────────────────────
# Translates OpenAI-compat .chat.completions.create() calls into Ollama
# /api/chat requests. Handles both streaming (NDJSON) and non-streaming JSON
# responses. No API key is required — the HF Space is open.

import json as _json
import requests as _requests

class _OllamaCompletionsShim:
    """Translates OpenAI-style .create() kwargs into Ollama /api/chat calls."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def create(self, *, model: str, messages: list, **kwargs) -> "_GenAIResponseShim":
        """
        Calls POST {base_url}/api/chat with the Ollama message format.
        Collects streamed response tokens and returns a unified _GenAIResponseShim
        so downstream code (choices[0].message.content) works unchanged.
        """
        payload: dict = {
            "model": model,
            "messages": messages,  # Ollama /api/chat accepts the same role/content format
            "stream": True,
        }
        # Map OpenAI generation kwargs to Ollama options where applicable
        options: dict = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if options:
            payload["options"] = options

        url = f"{self._base_url}/api/chat"
        try:
            resp = _requests.post(url, json=payload, stream=True, timeout=300)
            resp.raise_for_status()
        except _requests.exceptions.HTTPError as e:
            raise RuntimeError(f"[OllamaShim] HTTP {resp.status_code} from {url}: {e}") from e
        except _requests.exceptions.RequestException as e:
            raise RuntimeError(f"[OllamaShim] Connection error to {url}: {e}") from e

        # Collect streamed NDJSON tokens into a full response string
        full_text = []
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                chunk = _json.loads(raw_line)
            except _json.JSONDecodeError:
                continue
            # Ollama /api/chat streaming format: {"message": {"content": "..."}}
            content_piece = chunk.get("message", {}).get("content", "")
            if content_piece:
                full_text.append(content_piece)
            if chunk.get("done", False):
                break

        return _GenAIResponseShim("".join(full_text))


class _OllamaRawShim:
    """Top-level shim: exposes .chat.completions backed by the Ollama /api/chat endpoint."""
    class _Chat:
        def __init__(self, completions_shim: "_OllamaCompletionsShim"):
            self.completions = completions_shim

    def __init__(self, base_url: str):
        completions = _OllamaCompletionsShim(base_url)
        self.chat = _OllamaRawShim._Chat(completions)
# ─────────────────────────────────────────────────────────────────────────────


class RoutedClient:
    """Wrapper holding an instructor client and its routing metadata."""
    def __init__(self, provider_name: str, api_key: str, p_config: dict):
        self.provider = provider_name
        self.weight = p_config["weight"]
        self.p_config = p_config  # Stores model_light, model_heavy, model_vision

        self.key_preview = f"…{api_key[-6:]}"
        self.api_key_exact = api_key
        self.cooldown_until = 0.0
        self.disabled = False

        if provider_name == "SELF_HOSTED_OLLAMA":
            # Ollama shim — no SDK required, uses raw HTTP via requests
            ollama_shim = _OllamaRawShim(p_config["base_url"])
            self.raw_client = ollama_shim  # type: ignore
            # Instructor wraps the raw client via a compatibility path;
            # for JSON structured outputs we wrap a dummy OpenAI client pointed
            # at the same Ollama server which exposes /v1/chat/completions too.
            openai_compat = OpenAI(
                base_url=f"{p_config['base_url']}/v1",
                api_key="ollama",  # Ollama accepts any non-empty key string
                timeout=300.0,
                max_retries=0
            )
            self.client = instructor.from_openai(openai_compat, mode=instructor.Mode.JSON)  # type: ignore[assignment]
        elif provider_name == "GOOGLE_AI_STUDIO":
            from google import genai as _genai  # lazy import
            genai_client = _genai.Client(api_key=api_key)
            # The existing router expects .raw_client.chat.completions.create to exist
            # for non-structured completion paths, so wrap the native GenAI client
            # in the OpenAI-compatible shim regardless of mode.
            self.raw_client = _GenAIRawShim(genai_client)  # type: ignore
            self.client = instructor.from_genai(genai_client, mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS)  # type: ignore[assignment]
        elif provider_name == "GROQ":
            groq_client = Groq(api_key=api_key)
            self.raw_client = groq_client  # type: ignore
            self.client = instructor.from_groq(groq_client, mode=instructor.Mode.JSON)  # type: ignore[assignment]
        else:
            openai_client = OpenAI(base_url=p_config["base_url"], api_key=api_key)
            self.raw_client = openai_client  # type: ignore
            self.client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON)  # type: ignore[assignment]

    def is_cooling_down(self) -> bool:
        return time.time() < self.cooldown_until

    def disable(self, reason: str):
        self.disabled = True
        print(f"[Router · {self.provider}] {self.key_preview} disabled: {reason}")


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

        if AIR_GAPPED_MODE:
            print("[LLM Router] AIR_GAPPED_MODE ENABLED. Forcing all requests to local vLLM.")
            p_config = {
                "base_url": LOCAL_INFERENCE_URL,
                "weight": 100,
                "model_light": "gemma-local",
                "model_heavy": "gemma-local",
                "model_vision": "gemma-local"
            }
            self.clients_universal.append(RoutedClient("LOCAL_VLLM", "dummy-local-key", p_config))
            return

        if EXECUTION_MODE not in {"cloud", "local"}:
            print(f"[LLM Router] WARNING: Unknown EXECUTION_MODE={EXECUTION_MODE!r}. Falling back to cloud mode.")

        print(f"[LLM Router] EXECUTION_MODE={EXECUTION_MODE.upper()}")

        for p_name, p_config in PROVIDERS.items():
            effective_config = dict(p_config)

            if EXECUTION_MODE == "local":
                if p_name == "SELF_HOSTED_OLLAMA":
                    effective_config["weight"] = 100
                else:
                    effective_config["weight"] = 0
            elif EXECUTION_MODE == "cloud":
                if p_name == "SELF_HOSTED_OLLAMA":
                    effective_config["weight"] = 0
                elif effective_config.get("weight", 0) == 0:
                    effective_config["weight"] = 100

            for key in _load_all_keys_for_provider(effective_config["env_keys"]):
                if key not in seen_keys and effective_config["weight"] > 0:
                    seen_keys.add(key)
                    self.clients_universal.append(RoutedClient(p_name, key, effective_config))

        print(f"[LLM Router] Initialized Universal Pool with {len(self.clients_universal)} keys globally.")
        if not self.clients_universal:
            if EXECUTION_MODE == "local":
                message = (
                    "[LLM Router] ERROR: Local execution mode enabled but no local inference provider was found. "
                    "Set OLLAMA_SENTINEL or configure LOCAL_INFERENCE_URL."
                )
            else:
                message = (
                    "[LLM Router] ERROR: No cloud LLM API keys found in environment. "
                    "Set GOOGLE_API_KEY | GEMINI_API_KEY | OPENROUTER_API_KEY | GROQ_API_KEY | HF_TOKEN."
                )
            print(message)
            raise RuntimeError(message)

    def _select_client(self, tier: str) -> tuple[RoutedClient, str] | tuple[None, None]:
        """Weighted roulette selection of a healthy client that explicitly supports the requested tier."""
        with self._lock:
            # Filter the universal pool to contain only healthy clients that actively have a mapping for this tier
            valid_pool = []
            for c in self.clients_universal:
                if c.disabled or c.is_cooling_down():
                    continue

                mapped_string = None
                if tier == "VISION" and "model_vision" in c.p_config:
                    mapped_string = c.p_config["model_vision"]
                elif tier == "HEAVY" and "model_heavy" in c.p_config:
                    mapped_string = c.p_config["model_heavy"]
                elif tier == "LIGHT" and "model_light" in c.p_config:
                    mapped_string = c.p_config["model_light"]
                    
                if mapped_string and c.weight > 0:
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
                if not any(not c.disabled for c in self.clients_universal):
                    raise RuntimeError("[LLM Router] All configured LLM keys have been disabled. Halting pipeline.")
                # All remaining keys are cooling or otherwise unavailable. Wait briefly and retry.
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
                    # Instructor/genai path: some underlying genai implementations do not
                    # accept OpenAI-style kwargs such as `temperature` and will raise
                    # "Models.generate_content() got an unexpected keyword argument 'temperature'".
                    # Avoid passing `temperature` into the instructor-wrapped client to
                    # prevent that error. Keep `max_retries` behavior intact.
                    if "temperature" in call_kwargs and client_wrapper.provider == "GOOGLE_AI_STUDIO":
                        call_kwargs.pop("temperature")

                    if "max_retries" not in call_kwargs and client_wrapper.provider != "SELF_HOSTED_OLLAMA":
                        call_kwargs["max_retries"] = 3
                    response = client_wrapper.client.chat.completions.create(**call_kwargs)
                else:
                    response = client_wrapper.raw_client.chat.completions.create(**call_kwargs)

                # Debugging: inspect provider response structure
                choices = getattr(response, "choices", None)
                try:
                    print(f"[LLM Router] provider={client_wrapper.provider}, requested_tier={tier}, mapped_model={mapped_model}, choices_type={type(choices)}, choices_len={len(choices) if choices is not None else 'None'}")
                except Exception as inner_e:
                    print(f"[LLM Router] failed to inspect choices: {inner_e}")

                if "response_model" not in call_kwargs or call_kwargs.get("response_model") is None:
                    # For plain text completions we expect choices from provider
                    if not choices or len(choices) == 0:
                        raise ValueError(f"Empty or missing choices from provider {client_wrapper.provider} ({mapped_model}), response={response}")
                    if choices[0] is None:
                        raise ValueError(f"Empty first choice from provider {client_wrapper.provider} ({mapped_model}), response={response}")
                    has_message = hasattr(choices[0], 'message') and getattr(choices[0], 'message') is not None
                    has_text = hasattr(choices[0], 'text') and getattr(choices[0], 'text') is not None
                    if not has_message and not has_text:
                        raise ValueError(
                            f"Malformed choice[0] from provider {client_wrapper.provider} ({mapped_model}), no message/text field, response={response}"
                        )

                # For structured response_model paths, we accept any valid parsed object.
                return response
                
            except (GroqRateLimitError, OpenAIRateLimitError, NotFoundError, InternalServerError) as e:
                reason = f"provider failure: {type(e).__name__} {str(e)[:200]}"
                client_wrapper.disable(reason)
                exceptions.append(e)
            except Exception as e:
                err_str = str(e).lower()
                err_type = str(type(e)).lower()
                reason = f"error: {type(e).__name__} {str(e)[:200]}"

                if any(x in err_str for x in ["429", "too many requests", "timeout", "500", "404", "401", "400", "unauthorized", "bad request"]):
                    client_wrapper.disable(reason)
                elif "validation" in err_type or "json" in err_str or "parse" in err_str:
                    print(f"[LLM Router] Caught validation/parse error from {client_wrapper.provider}. Disabling key and retrying...")
                    client_wrapper.disable(reason)
                else:
                    print(f"[LLM Router] Unexpected provider error from {client_wrapper.provider}. Disabling key and retrying: {e}")
                    client_wrapper.disable(reason)
                exceptions.append(e)
                
        # If we exit the loop, we are totally exhausted
        raise Exception(f"[LLM Router] Failed after {MAX_FALLBACK_LOOPS} routed attempts. Last error: {exceptions[-1]}")

    def extract_text_from_response(self, response: Any) -> str | None:
        """Normalize provider response objects into a plain text string.

        Returns the textual content of the first choice when possible, or
        None when no usable text is found.
        """
        try:
            choices = getattr(response, "choices", None)
            if not choices:
                return None
            choice = choices[0]

            # OpenAI-like: choice.message.content
            if hasattr(choice, 'message') and getattr(choice.message, 'content', None):
                return getattr(choice.message, 'content')

            # Some providers use .text directly
            if hasattr(choice, 'text') and getattr(choice, 'text', None):
                return getattr(choice, 'text')

            # _GenAIChoiceShim exposes .message.content via shim; double-check
            if isinstance(choice, _GenAIChoiceShim):
                return getattr(choice.message, 'content', None)

            # As a last resort, try to stringify any simple attributes
            for attr in ('content', '_content', 'output_text'):
                if hasattr(choice, attr) and getattr(choice, attr, None):
                    return getattr(choice, attr)
        except Exception as e:
            print(f"[LLM Router] extract_text_from_response error: {e}")
        return None

# Export Singleton
llm_pool = MultiProviderRouter()
