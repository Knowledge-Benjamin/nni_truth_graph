"""
ai_engine/core/groq_pool.py
────────────────────────────────────────────────────────────────────────────
Backward-compatibility alias.

Points all legacy `groq_pool` imports directly to the new multi-provider
load-balancing router (llm_router.py).

Important: we use a *relative* import so this module works both when
imported as `ai_engine.core.groq_pool` (package style) and as
`core.groq_pool` when `ai_engine/` has been added to `sys.path` (as in
the worker scripts running on HF Spaces).
"""

from .llm_router import llm_pool as groq_pool
