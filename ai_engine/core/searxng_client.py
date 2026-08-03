import os
import time
from typing import Any

import requests


def _should_retry_without_time_range(response: requests.Response, params: dict[str, Any] | None) -> bool:
    if response.status_code != 200:
        return False
    if not params:
        return False
    if params.get("time_range") is None:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    results = payload.get("results") or payload.get("data") or payload.get("hits") or []
    if isinstance(results, dict):
        results = results.get("results") or results.get("data") or []
    return isinstance(results, list) and len(results) == 0


DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SEARXNG_TIMEOUT_SECONDS", "10"))
DEFAULT_RETRIES = int(os.getenv("SEARXNG_RETRIES", "2"))


def request_searxng(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "get",
    timeout: int | None = None,
    retries: int | None = None,
    backoff_seconds: float = 0.5,
) -> requests.Response:
    """Send a SearXNG request with timeout-aware retry behavior."""
    timeout_value = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
    retry_count = retries if retries is not None else DEFAULT_RETRIES
    method_name = (method or "get").lower()
    last_error: Exception | None = None

    for attempt in range(retry_count + 1):
        try:
            if method_name == "post":
                response = requests.post(url, data=params, headers=headers, timeout=timeout_value)
            else:
                response = requests.get(url, params=params, headers=headers, timeout=timeout_value)
            response.raise_for_status()
            if _should_retry_without_time_range(response, params):
                fallback_params = dict(params or {})
                fallback_params.pop("time_range", None)
                if attempt < retry_count:
                    time.sleep(backoff_seconds * (attempt + 1))
                    if method_name == "post":
                        response = requests.post(url, data=fallback_params, headers=headers, timeout=timeout_value)
                    else:
                        response = requests.get(url, params=fallback_params, headers=headers, timeout=timeout_value)
                    response.raise_for_status()
                    return response
            return response
        except requests.Timeout as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
        except requests.ConnectionError as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {429, 500, 502, 503, 504} and attempt < retry_count:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise

    if last_error is not None:
        raise last_error
    raise requests.RequestException("SearXNG request failed")
