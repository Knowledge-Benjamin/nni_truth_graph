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

import json
import os
import re
import time
import random
import threading
from typing import Any
import requests
import instructor
from dotenv import load_dotenv
from google.auth import default as google_default
from google.auth.transport.requests import AuthorizedSession
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure the ai_engine .env is loaded at import time for any script that imports this module first.
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=env_path, override=False)

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
        "model_light":  "gemma-4-e4b",
        "model_heavy":  "gemma-4-e4b",
        "model_vision": "gemma-4-e4b"
    },
    # ── Disabled Cloud Providers ───────────────────────────────────────────────
    # All cloud providers are weight-zeroed. Routing is exclusively self-hosted.
    "GOOGLE_AI_STUDIO": {
        "base_url": None,
        "weight": 0,  # Disabled: self-hosted Ollama is exclusive
        "env_keys": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "model_light":  "gemini-2.5-flash",
        "model_heavy":  "gemini-2.5-pro",
        "model_vision": "gemini-2.5-flash"
    },
    "VERTEX_AI": {
        "base_url": None,
        "weight": 0,
        "env_keys": ["VERTEX_AI_API_KEY"],
        "model_light":  "gemini-2.5-flash",
        "model_heavy":  "gemini-2.5-pro",
        "model_vision": "gemini-2.5-flash"
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


def _is_placeholder_vertex_key(value: str | None) -> bool:
    """Return True when the Vertex API key is still a placeholder value."""
    if value is None:
        return True
    cleaned = value.strip().lower()
    if not cleaned:
        return True
    placeholders = {
        "replace_with_your_vertex_api_key",
        "your_vertex_api_key",
        "changeme",
        "placeholder",
        "replace_me",
        "todo",
    }
    return cleaned in placeholders or cleaned.startswith("replace_with_")


def _vertex_ai_is_enabled() -> bool:
    """Return True when Vertex AI mode is explicitly enabled."""
    enabled_value = os.getenv("VERTEX_AI_ENABLED", "false").strip().lower()
    return enabled_value in {"1", "true", "yes", "on"}


def _vertex_ai_is_configured() -> bool:
    """Return True when Vertex AI has enough config to initialize a client."""
    if not _vertex_ai_is_enabled():
        return False
    api_key = os.getenv("VERTEX_AI_API_KEY", "").strip()
    project = os.getenv("VERTEX_AI_PROJECT", "").strip()
    location = os.getenv("VERTEX_AI_LOCATION", "").strip()
    return bool(project and location and api_key and not _is_placeholder_vertex_key(api_key))


def discover_vertex_model_names() -> list[str]:
    """Query Vertex AI for available model IDs for the configured project and region."""
    project = os.getenv("VERTEX_AI_PROJECT", "").strip()
    location = os.getenv("VERTEX_AI_LOCATION", "").strip()
    if not project or not location:
        return []

    try:
        credentials, _ = google_default()
        if credentials is None:
            return []
        session = AuthorizedSession(credentials)
        url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models"
        response = session.get(url, timeout=30)
        if response.status_code >= 400:
            return []
    except Exception:
        return []

    try:
        payload = response.json()
    except Exception:
        return []

    models = payload.get("models", []) if isinstance(payload, dict) else []
    names: list[str] = []
    for model in models:
        if isinstance(model, dict):
            name = model.get("name") or model.get("model") or model.get("displayName")
            if isinstance(name, str) and name:
                names.append(name.split("/")[-1])
    return sorted(set(names))


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
        if kwargs.get("response_format", {}).get("type") == "json_object":
            cfg["response_mime_type"] = "application/json"
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import httpx

def _make_resilient_session() -> _requests.Session:
    """
    Build a requests Session with connection-error retries.
    We do NOT retry on read timeouts — a slow LLM response is not a transient
    connection error, and retrying would re-POST the full request unnecessarily.
    """
    session = _requests.Session()
    retry = Retry(
        total=3,
        connect=3,           # retry on connect failures only
        read=0,              # never auto-retry on read timeout
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

class _OllamaCompletionsShim:
    """
    Translates OpenAI-style .create() kwargs into either:
      - Ollama /api/chat  (when OLLAMA_NATIVE=true or base_url contains .hf.space)
      - vLLM/OpenAI /v1/chat/completions  (default — used for ngrok/Colab vLLM endpoints)
    """

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._session = _make_resilient_session()
        # Detect native Ollama vs vLLM/OpenAI-compat endpoint.
        # Native Ollama endpoints are identified by the OLLAMA_NATIVE env var or
        # by the legacy HF Space host. Everything else (ngrok, Cloud Run, etc.)
        # is treated as an OpenAI-compatible vLLM server.
        _native_flag = os.getenv("OLLAMA_NATIVE", "").strip().lower() == "true"
        _hf_host = ".hf.space" in self._base_url
        self._use_ollama_native = _native_flag or _hf_host

    def create(self, *, model: str, messages: list, **kwargs) -> "_GenAIResponseShim":
        if self._use_ollama_native:
            return self._create_ollama(model=model, messages=messages, **kwargs)
        else:
            return self._create_openai_compat(model=model, messages=messages, **kwargs)

    def _create_ollama(self, *, model: str, messages: list, **kwargs) -> "_GenAIResponseShim":
        """Ollama /api/chat — for the HF Space Gemma 2 backend."""
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        options: dict = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if options:
            payload["options"] = options

        url = f"{self._base_url}/api/chat"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._session.post(url, json=payload, stream=True, timeout=600, verify=False)
                resp.raise_for_status()
                break
            except _requests.exceptions.HTTPError as e:
                last_error = RuntimeError(f"[OllamaShim] HTTP {getattr(resp, 'status_code', '?')} from {url}: {e}")
                if attempt == 2:
                    raise last_error from e
            except (_requests.exceptions.Timeout, _requests.exceptions.RequestException) as e:
                last_error = RuntimeError(f"[OllamaShim] Connection error to {url}: {e}")
                if attempt == 2:
                    raise last_error from e
            time.sleep(2)

        full_text = []
        for raw_line in resp.iter_lines():  # type: ignore[union-attr]
            if not raw_line:
                continue
            try:
                chunk = _json.loads(raw_line)
            except _json.JSONDecodeError:
                continue
            content_piece = chunk.get("message", {}).get("content", "")
            if content_piece:
                full_text.append(content_piece)
            if chunk.get("done", False):
                break
        return _GenAIResponseShim("".join(full_text))

    def _create_openai_compat(self, *, model: str, messages: list, **kwargs) -> "_GenAIResponseShim":
        """
        vLLM / any OpenAI-compatible server — POST /v1/chat/completions.

        Non-streaming. SSL verification is disabled (verify=False) so that the
        ngrok tunnel's intermediate certificate chain does not cause SSLEOFErrors.
        Streaming was tried but ngrok buffers SSE chunks, causing ReadTimeoutErrors
        even when the model is actively generating. Non-streaming + long timeout is
        the correct approach for ngrok-proxied vLLM.
        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("response_format", {}).get("type") == "json_object":
            payload["response_format"] = {"type": "json_object"}

        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer notneeded"}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers,
                    timeout=720,   # 12 min — generous for long extractions
                    verify=False,  # bypass ngrok SSL cert chain
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return _GenAIResponseShim(content)
            except _requests.exceptions.Timeout as e:
                last_error = RuntimeError(f"[vLLMShim] Timeout from {url}: {e}")
            except _requests.exceptions.RequestException as e:
                last_error = RuntimeError(f"[vLLMShim] Connection error to {url}: {e}")
            except (KeyError, IndexError) as e:
                last_error = RuntimeError(f"[vLLMShim] Malformed response from {url}: {e} | body={getattr(resp, 'text', '')[:300]}")
            except _json.JSONDecodeError as e:
                last_error = RuntimeError(f"[vLLMShim] JSON decode error from {url}: {e} | body={getattr(resp, 'text', '')[:300]}")
            if attempt < 2:
                time.sleep(3)
        raise last_error  # type: ignore[misc]




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
            # Also pass a custom httpx client with SSL verification disabled so that
            # the OpenAI SDK (which uses httpx internally) doesn't choke on the ngrok
            # tunnel's SSL chain when used via instructor for structured outputs.
            openai_compat = OpenAI(
                base_url=f"{p_config['base_url']}/v1",
                api_key="ollama",  # Ollama accepts any non-empty key string
                timeout=300.0,
                max_retries=0,
                http_client=httpx.Client(verify=False),
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
        elif provider_name == "VERTEX_AI":
            from google import genai as _genai  # lazy import
            init_kwargs: dict[str, Any] = {
                "vertexai": True,
                "api_key": api_key,
            }
            genai_client = _genai.Client(**init_kwargs)
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

    def _extract_json_payload(self, text: str) -> Any | None:
        """Best-effort extraction of a JSON object from model output."""
        if not text:
            return None

        candidate = text.strip()
        if not candidate:
            return None

        if candidate.startswith("```"):
            match = re.match(r"```(?:json)?\s*(.*?)\s*```", candidate, re.S | re.I)
            if match:
                candidate = match.group(1).strip()

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for idx, ch in enumerate(candidate):
            if ch in "[{":
                try:
                    parsed, _ = decoder.raw_decode(candidate[idx:])
                    return parsed
                except json.JSONDecodeError:
                    continue
        return None

    def _build_structured_prompt(self, messages: list[dict], response_model: Any) -> list[dict]:
        """Append a JSON-only instruction so Gemini can return a parseable payload."""
        schema = {}
        if hasattr(response_model, "model_json_schema"):
            schema = response_model.model_json_schema()

        # Build a short, explicit example JSON for common response models
        example = None
        try:
            model_name = getattr(response_model, '__name__', '')
            keys = set(schema.get('properties', {}).keys() if isinstance(schema, dict) else [])
            if model_name == 'TriageResult' or {'goal_type', 'initial_queries'}.issubset(keys):
                example = {
                    "goal_type": "PROFILING",
                    "target_type": "PERSON",
                    "canonical_target": "elon musk",
                    "exhaust_predicate": None,
                    "initial_queries": [
                        "\"Elon Musk\" biography site:twitter.com",
                        "\"Elon Musk\" funding OR investors site:news",
                        "Elon Musk SpaceX regulatory filings"
                    ],
                    "seed_leads": [
                        {"entity_name": "Tesla", "lead_type": "ORGANISATION", "priority": 90}
                    ],
                    "rationale": "PROFILING: focus on public profiles, corporate links, and funding." 
                }
        except Exception:
            example = None

        schema_hint = (
            "\n\nReturn ONLY a single JSON OBJECT that matches the requested schema. "
            "Do not return the JSON Schema, property descriptions, or any metadata (no 'properties', 'type', 'required', 'title'). "
            "Do not wrap it in markdown code fences or add commentary.\n"
            f"Schema: {json.dumps(schema, indent=2)}"
        )
        if example is not None:
            try:
                schema_hint += "\nExample JSON:\n" + json.dumps(example, indent=2)
            except Exception:
                pass

        if not messages:
            return [{"role": "user", "content": schema_hint}]

        updated = [dict(m) for m in messages]
        if updated and updated[-1].get("role") == "user":
            updated[-1]["content"] = str(updated[-1].get("content", "")) + schema_hint
        else:
            updated.append({"role": "user", "content": schema_hint})
        return updated

    def _infer_safe_default_for_field(self, field_info: Any) -> Any:
        """Produce a safe default for a Pydantic field when provider output is malformed."""
        if field_info is None:
            return None

        default = getattr(field_info, "default", None)
        if default is not None:
            return default

        default_factory = getattr(field_info, "default_factory", None)
        if default_factory is not None:
            try:
                return default_factory()
            except Exception:
                pass

        annotation = getattr(field_info, "annotation", None)
        annotation_name = getattr(annotation, "__name__", "") if annotation is not None else ""
        annotation_str = str(annotation)

        if annotation_name == "list" or annotation_str.startswith("typing.List") or annotation_str.startswith("list["):
            return []
        if annotation_name in {"str", "String"} or annotation_str.startswith("typing.Optional") and "str" in annotation_str:
            return ""
        if annotation_name == "bool":
            return False
        if annotation_name in {"int", "float"}:
            return 0
        return None

    def _build_safe_model_instance(self, response_model: Any, parsed: Any | None = None) -> Any:
        """Return a valid model instance with safe defaults rather than a plain dict."""
        if hasattr(response_model, "model_validate"):
            try:
                if isinstance(parsed, dict):
                    return response_model.model_validate(parsed)
                if parsed is None:
                    return response_model.model_validate({})
            except Exception:
                pass

        if isinstance(response_model, type):
            fields = getattr(response_model, 'model_fields', None) or getattr(response_model, '__fields__', None) or {}
            defaults: dict[str, Any] = {}
            if isinstance(parsed, dict):
                for fname in fields:
                    if fname in parsed and parsed[fname] is not None:
                        defaults[fname] = parsed[fname]
                    else:
                        defaults[fname] = self._infer_safe_default_for_field(fields[fname])
            else:
                for fname, finfo in fields.items():
                    defaults[fname] = self._infer_safe_default_for_field(finfo)

            try:
                return response_model(**defaults)
            except Exception:
                try:
                    return response_model()
                except Exception:
                    return {}

        return parsed if parsed is not None else {}

    def _run_structured_fallback(self, client_wrapper: RoutedClient, call_kwargs: dict, response_model: Any) -> Any:
        """Fallback for providers that reject instructor/structured-output schemas."""
        fallback_kwargs = dict(call_kwargs)
        fallback_kwargs.pop("response_model", None)
        fallback_kwargs.pop("max_retries", None)
        fallback_kwargs["messages"] = self._build_structured_prompt(
            fallback_kwargs.get("messages", []),
            response_model,
        )

        try:
            raw_response = client_wrapper.raw_client.chat.completions.create(**fallback_kwargs)
        except Exception as exc:
            raise RuntimeError(f"Structured-output fallback failed: {exc}") from exc

        text = self.extract_text_from_response(raw_response)
        parsed = self._extract_json_payload(text or "")

        if parsed is None:
            # If we couldn't parse JSON, log and fall back to an empty mapping so
            # downstream validation can produce a safe default instance instead
            print(f"[LLM Router] Structured-output fallback could not parse JSON from provider response: {text}")
            parsed = {}

        # If the provider returned a bare list but the expected response
        # model is a Pydantic model (object), attempt to coerce the list
        # into a dict by placing it under a likely list-typed field name.
        # This handles cases where models return the list of items for a
        # single-list field (e.g. `initial_queries`) instead of a wrapper.
        try:
            if isinstance(parsed, list) and isinstance(response_model, type):
                # Prefer Pydantic v2 `model_fields`, fall back to v1 `__fields__`.
                model_fields = getattr(response_model, 'model_fields', None) or getattr(response_model, '__fields__', None) or {}
                # Look for an obvious list-typed field name first.
                candidates = ['initial_queries', 'items', 'results', 'entities', 'candidates']
                chosen = None
                for cname in candidates:
                    if cname in model_fields:
                        chosen = cname
                        break
                if chosen is None:
                    # Otherwise find first field whose annotation indicates a list
                    for fname, finfo in model_fields.items():
                        ann = getattr(finfo, 'annotation', None)
                        ann_name = getattr(ann, '__name__', '') if ann is not None else ''
                        if ann_name == 'list' or str(ann).startswith('typing.List'):
                            chosen = fname
                            break
                if chosen is not None:
                    parsed = {chosen: parsed}
        except Exception:
            # If coercion fails for any reason, keep parsed as-is and let
            # downstream validation handle it.
            pass

        # Normalization: if the parsed payload contains `initial_queries`
        # as a list of dicts, coerce each dict to a search string by
        # preferring `entity_name`, `query`, `text`, or `name` fields.
        try:
            if isinstance(parsed, dict):
                iq = parsed.get('initial_queries')
                if isinstance(iq, list) and iq:
                    need_norm = any(isinstance(x, dict) for x in iq)
                    if need_norm:
                        new_iq = []
                        for item in iq:
                            if isinstance(item, str):
                                new_iq.append(item)
                                continue
                            if isinstance(item, dict):
                                s = item.get('entity_name') or item.get('query') or item.get('text') or item.get('name')
                                if not s:
                                    try:
                                        s = ' '.join(str(v) for v in item.values() if v)
                                    except Exception:
                                        s = json.dumps(item)
                                new_iq.append(s)
                                continue
                            new_iq.append(str(item))
                        parsed['initial_queries'] = new_iq

                # Ensure required top-level fields exist with safe defaults
                if 'goal_type' not in parsed or not parsed.get('goal_type'):
                    parsed.setdefault('goal_type', 'UNKNOWN')
                if 'target_type' not in parsed or not parsed.get('target_type'):
                    parsed.setdefault('target_type', 'UNKNOWN')
                if 'canonical_target' not in parsed:
                    parsed.setdefault('canonical_target', '')
                if 'rationale' not in parsed:
                    parsed.setdefault('rationale', '')

                # Debugging aid: compact print of the normalized parsed JSON
                try:
                    print(f"[LLM Router] Parsed structured JSON (normalized): {json.dumps(parsed, separators=(',',':'))[:1000]}")
                except Exception:
                    pass
        except Exception:
            pass

        # If the model accidentally returned a JSON Schema object (it echoed
        # the schema instead of filling it), attempt to extract concrete
        # values from the `properties` mapping. This handles cases like the
        # provider returning {"properties": {"queries": [...]}, "title": ...}
        try:
            if isinstance(parsed, dict) and 'properties' in parsed and isinstance(parsed['properties'], dict):
                props = parsed['properties']
                instance: dict = {}
                for fname, fval in props.items():
                    # If the property value is already a concrete value, use it
                    if isinstance(fval, (str, int, float, bool, list)):
                        instance[fname] = fval
                        continue

                    # If the property appears to be a schema fragment, prefer
                    # explicit examples/defaults/constants if present.
                    if isinstance(fval, dict):
                        if 'default' in fval:
                            instance[fname] = fval['default']
                            continue
                        if 'example' in fval:
                            instance[fname] = fval['example']
                            continue
                        if 'const' in fval:
                            instance[fname] = fval['const']
                            continue

                        # Otherwise, scan nested values for any concrete list/string
                        found = None
                        for subv in fval.values():
                            if isinstance(subv, (list, str)):
                                found = subv
                                break
                        if found is not None:
                            instance[fname] = found
                        else:
                            # As a last resort, keep the raw fragment so validation
                            # can decide, but stringify to avoid nested schema objects.
                            try:
                                instance[fname] = json.dumps(fval)
                            except Exception:
                                instance[fname] = str(fval)
                    else:
                        try:
                            instance[fname] = str(fval)
                        except Exception:
                            instance[fname] = None

                # Merge top-level scalar values if present (goal_type/rationale etc.)
                for k in ('goal_type', 'target_type', 'canonical_target', 'rationale'):
                    if k in parsed and parsed[k]:
                        instance.setdefault(k, parsed[k])

                parsed = instance
                try:
                    print(f"[LLM Router] Converted JSON-Schema-like output into instance: {json.dumps(parsed, separators=(',',':'))[:1000]}")
                except Exception:
                    pass
        except Exception:
            pass

        # Try to coerce/validate into the requested Pydantic model, but be
        # defensive: if validation fails, return a default instance with safe
        # placeholder values rather than raising and disabling providers.
        try:
            if hasattr(response_model, "model_validate"):
                return response_model.model_validate(parsed)
            if isinstance(response_model, type):
                if isinstance(parsed, dict):
                    return response_model(**parsed)
                return response_model()
            return parsed
        except Exception as e:
            print(f"[LLM Router] Structured parsing to {getattr(response_model, '__name__', str(response_model))} failed: {e}")
            return self._build_safe_model_instance(response_model, parsed)

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
                elif _vertex_ai_is_configured():
                    if p_name == "VERTEX_AI":
                        effective_config["weight"] = 100
                    else:
                        effective_config["weight"] = 0
                elif p_name == "VERTEX_AI":
                    effective_config["weight"] = 0
                elif p_name == "GOOGLE_AI_STUDIO":
                    effective_config["weight"] = 100
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
                    response_model = call_kwargs["response_model"]
                    if client_wrapper.provider in {"GOOGLE_AI_STUDIO", "VERTEX_AI", "SELF_HOSTED_OLLAMA"}:
                        # The local Ollama path and Gemini fallback both work better with a
                        # plain-text JSON response that we parse locally instead of the
                        # instructor wrapper, which is prone to timeouts and schema issues.
                        response = self._run_structured_fallback(client_wrapper, call_kwargs, response_model)
                    else:
                        # Instructor/genai path: some underlying genai implementations do not
                        # accept OpenAI-style kwargs such as `temperature` and will raise
                        # "Models.generate_content() got an unexpected keyword argument 'temperature'".
                        # Avoid passing `temperature` into the instructor-wrapped client to
                        # prevent that error. Keep `max_retries` behavior intact.
                        if "temperature" in call_kwargs and client_wrapper.provider in {"GOOGLE_AI_STUDIO", "VERTEX_AI"}:
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
                if client_wrapper.provider == "SELF_HOSTED_OLLAMA" and "timeout" in str(e).lower():
                    print(f"[LLM Router] Transient local timeout from {client_wrapper.provider}; retrying without disabling provider: {e}")
                    client_wrapper.cooldown_until = time.time() + 5
                else:
                    client_wrapper.disable(reason)
                exceptions.append(e)
            except Exception as e:
                err_str = str(e).lower()
                err_type = str(type(e)).lower()
                reason = f"error: {type(e).__name__} {str(e)[:200]}"

                if client_wrapper.provider == "SELF_HOSTED_OLLAMA" and any(x in err_str for x in ["timeout", "timed out", "connection", "temporarily unavailable", "retry", "failed_attempts", "ssl", "eof"]):
                    print(f"[LLM Router] Local provider connection issue from {client_wrapper.provider}; cooling down and falling back to cloud providers: {str(e)[:120]}")
                    client_wrapper.cooldown_until = time.time() + 30
                    exceptions.append(e)
                    continue
                elif any(x in err_str for x in ["validation", "json", "parse"]):
                    print(f"[LLM Router] Local provider JSON/parse issue from {client_wrapper.provider}; returning safe fallback: {str(e)[:120]}")
                    client_wrapper.cooldown_until = time.time() + 5
                    if "response_model" in kwargs and kwargs.get("response_model") is not None:
                        try:
                            response_model = kwargs["response_model"]
                            return self._build_safe_model_instance(response_model, {})
                        except Exception:
                            try:
                                return response_model()
                            except Exception:
                                return {}
                    return {}
                elif any(x in err_str for x in ["429", "too many requests", "timeout", "500", "404", "401", "400", "unauthorized", "bad request"]):
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
