"""
ai_engine/core/hf_pool.py
────────────────────────────────────────────────────────────────────────────
Legacy local embedding pool adapter.

This module now proxies to ai_engine.core.inference_pool (remote HF Spaces
inference server) to keep embedding behavior consistent for production.
"""

from ai_engine.core.inference_pool import inference_pool as hf_pool

# `hf_pool` remains a stable API alias for backward compatibility.
# Local sentence-transformers model has been removed in favor of the shared
# remote inference server pool (inference_pool).
