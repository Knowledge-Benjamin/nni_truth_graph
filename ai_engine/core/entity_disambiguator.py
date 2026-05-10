"""
ai_engine/core/entity_disambiguator.py
────────────────────────────────────────────────────────────────────────────
World-Class Entity Disambiguation Engine

Resolves any raw entity string (however the LLM extracted it) into a single
canonical name before it is written to Neo4j.  Runs three signals in sequence,
stopping at the earliest signal that surpasses its confidence threshold.

Signal Pipeline
───────────────
  1. LRU Cache hit              → instant return, zero I/O
  2. Neo4j fuzzy match          → embedding cosine-similarity against existing
                                   Entity nodes already in the graph
                                   (threshold ≥ 0.92 → accept canonical name)
  3. Wikidata REST API lookup   → free, no key, finds canonical English label
                                   and known aliases from the world knowledge base
                                   (threshold: first result with score ≥ 0.70)
  4. LLM arbitration (Groq 70B) → final authority when signals disagree or fail.
                                   Given the raw name + neo4j candidates +
                                   wikidata candidates → returns one word/phrase.

Thread safety
─────────────
  All mutable state (cache, lock) is instance-level.  The module-level singleton
  `entity_disambiguator` is shared safely across all Stage-8 worker threads
  because Python's GIL + the explicit threading.Lock guard every cache write.

Caching strategy
────────────────
  LRU cache of 10 000 entries (functools via OrderedDict). The cache is
  pre-warmed at startup by loading all existing Entity.name values from Neo4j
  mapping each to itself, so nodes that already exist resolve instantly.

Graceful degradation
────────────────────
  If Neo4j, Wikidata, and Groq all fail, the raw name is returned as-is after
  light text normalisation (title-case, strip, collapse whitespace) so the
  pipeline never stalls.
"""

from __future__ import annotations

import os
import re
import math
import time
import threading
import requests
from collections import OrderedDict
from typing import Optional

# ── Optional imports (fail softly) ───────────────────────────────────────────
try:
    from neo4j import GraphDatabase
    _NEO4J_AVAILABLE = True
except ImportError:
    _NEO4J_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
WIKIDATA_API         = "https://www.wikidata.org/w/api.php"
WIKIDATA_MIN_SCORE   = 0.70       # Minimum Wikidata match score to trust
NEO4J_SIM_THRESHOLD  = 0.92       # Cosine similarity threshold for Neo4j match
LLM_MODEL            = "TIER_HEAVY"
CACHE_MAX_SIZE       = 10_000
WIKIDATA_TIMEOUT     = 4          # seconds

# ── Minimal text normalisation (applied to raw input before every signal) ─────
_STOPWORDS = {
    "the", "a", "an", "of", "in", "at", "on", "and", "or", "for",
    "to", "with", "by", "from", "its", "their", "his", "her", "our",
}

# Known alias expansions & contractions — handles "U.S.", "US", "U.S.A." → "United States"
_ALIAS_TABLE: dict[str, str] = {
    # Country aliases
    "u.s.": "United States",
    "u.s.a.": "United States",
    "usa": "United States",
    "us": "United States",
    "america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
    "drc": "Democratic Republic of the Congo",
    "north korea": "North Korea",
    "south korea": "South Korea",
    "eu": "European Union",
    # Common entity shorthand
    "fed": "Federal Reserve",
    "the fed": "Federal Reserve",
    "imf": "International Monetary Fund",
    "un": "United Nations",
    "nato": "NATO",
    "cia": "CIA",
    "fbi": "FBI",
    "nsa": "NSA",
    "doj": "United States Department of Justice",
    "sec": "U.S. Securities and Exchange Commission",
    # Possessive / pronoun patterns handled in preprocess
}


# ── Cosine similarity (pure Python, no numpy required) ───────────────────────
def _cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


# ── Thread-safe LRU cache ─────────────────────────────────────────────────────
class _LRUCache:
    """Thread-safe LRU dict with a fixed max size."""

    def __init__(self, maxsize: int):
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def set(self, key: str, value: str):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def __len__(self):
        return len(self._cache)


# ─────────────────────────────────────────────────────────────────────────────
class EntityDisambiguator:
    """
    Multi-signal canonical entity resolver.

    Parameters
    ----------
    neo4j_driver : neo4j.GraphDatabase.driver (optional)
        If provided, Signal 2 (Neo4j fuzzy match) is enabled.
    groq_pool : _PoolProxy (optional)
        If provided, Signal 4 (LLM arbitration) is enabled.
    embed_fn : callable (optional)
        A function embed_fn(text: str) -> list[float] | None.
        Must match the 768-dim HF sentence-transformer used elsewhere.
    """

    def __init__(self, neo4j_driver=None, groq_pool=None, embed_fn=None):
        self._neo4j   = neo4j_driver
        self._groq    = groq_pool
        self._embed   = embed_fn
        self._cache   = _LRUCache(CACHE_MAX_SIZE)
        self._lock    = threading.Lock()   # guards _neo4j entity snapshot refresh

        # Snapshot of known canonical entities for fast pre-filter
        self._known_entities: list[str] = []
        self._known_embeddings: dict[str, list[float]] = {}
        self._snapshot_loaded = False

        print("[EntityDisambiguator] Initialised.")

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, raw_name: str) -> str:
        """
        Resolve raw_name to a canonical entity name.
        Never raises — falls back to normalised raw_name on full failure.
        """
        if not raw_name or not raw_name.strip():
            return raw_name or ""

        normalised = self._preprocess(raw_name)

        # 1. Cache
        cached = self._cache.get(normalised)
        if cached is not None:
            return cached

        canonical = (
            self._signal_neo4j(normalised)
            or self._signal_wikidata(normalised)
            or self._signal_llm(normalised)
            or normalised                  # graceful fallback
        )

        self._cache.set(normalised, canonical)
        # Also cache the raw pre-normalisation string for future speed
        if raw_name.strip() != normalised:
            self._cache.set(raw_name.strip(), canonical)

        return canonical

    def warm_cache(self):
        """
        Pre-populate the cache from all existing Entity nodes in Neo4j.
        Call once at startup before pipeline workers begin.
        """
        if not self._neo4j or not _NEO4J_AVAILABLE:
            return
        try:
            with self._neo4j.session() as session:
                records = session.run(
                    "MATCH (e:Entity) RETURN e.name AS name LIMIT 50000"
                ).data()
            names = [r["name"] for r in records if r.get("name")]
            for name in names:
                norm = self._preprocess(name)
                self._cache.set(norm, name)   # canonical = existing name
            self._known_entities = names
            self._snapshot_loaded = True
            print(f"[EntityDisambiguator] Cache warmed with {len(names)} existing entities.")
        except Exception as exc:
            print(f"[EntityDisambiguator] Cache warm-up failed (non-fatal): {exc}")

    # ── Preprocessing ─────────────────────────────────────────────────────────

    @staticmethod
    def _preprocess(raw: str) -> str:
        """
        Light normalisation applied before every signal:
          • Strip leading/trailing whitespace and quotes
          • Collapse internal whitespace
          • Lower-case alias table lookup (exact match)
          • Remove possessive 's  ("Iran's military" → "Iran's military")
          • Title-case result
        """
        text = raw.strip().strip('"\'')
        text = re.sub(r"\s+", " ", text)

        # Remove possessives for alias lookup ("Iran's" → "Iran")
        lookup_key = re.sub(r"'s?\b", "", text).strip().lower()
        if lookup_key in _ALIAS_TABLE:
            return _ALIAS_TABLE[lookup_key]

        # Direct lower-case alias hit
        lower = text.lower()
        if lower in _ALIAS_TABLE:
            return _ALIAS_TABLE[lower]

        # Remove noisy leading/trailing stop words
        words = text.split()
        while words and words[0].lower() in _STOPWORDS:
            words.pop(0)
        while words and words[-1].lower() in _STOPWORDS:
            words.pop()
        text = " ".join(words) if words else text

        # Final title-case
        return text.strip().title() if text else raw.strip()

    # ── Signal 1 (already consumed by cache) ─────────────────────────────────
    # Signal 1 is the LRU cache, checked in resolve() before calling signals.

    # ── Signal 2: Neo4j embedding similarity ─────────────────────────────────

    def _signal_neo4j(self, name: str) -> Optional[str]:
        """
        Embed `name` and compare against all Neo4j Entity embeddings.
        Returns the canonical name of the best match if cosine ≥ NEO4J_SIM_THRESHOLD.
        """
        if not self._neo4j or not self._embed or not _NEO4J_AVAILABLE:
            return None
        try:
            query_emb = self._embed(name)
            if query_emb is None:
                return None

            # Load embedding snapshot once per process lifecycle
            if not self._snapshot_loaded:
                self.warm_cache()

            with self._neo4j.session() as session:
                records = session.run("""
                    MATCH (e:Entity)
                    WHERE e.embedding IS NOT NULL
                    RETURN e.name AS name, e.embedding AS emb
                    LIMIT 5000
                """).data()

            best_sim  = 0.0
            best_name = None
            for rec in records:
                emb = rec.get("emb")
                if not emb or len(emb) != len(query_emb):
                    continue
                sim = _cosine(query_emb, emb)
                if sim > best_sim:
                    best_sim  = sim
                    best_name = rec["name"]

            if best_sim >= NEO4J_SIM_THRESHOLD and best_name:
                print(
                    f"  [Disambig·Neo4j] '{name}' → '{best_name}' "
                    f"(sim={best_sim:.4f})"
                )
                return best_name

        except Exception as exc:
            print(f"  [Disambig·Neo4j] Non-fatal error: {exc}")

        return None

    # ── Signal 3: Wikidata REST API ───────────────────────────────────────────

    def _signal_wikidata(self, name: str) -> Optional[str]:
        """
        Query the Wikidata API wbsearchentities action to find the canonical
        English label for the entity.  No API key required.
        Returns the canonical label if match score ≥ WIKIDATA_MIN_SCORE.
        """
        try:
            params = {
                "action":      "wbsearchentities",
                "search":      name,
                "language":    "en",
                "uselang":     "en",
                "type":        "item",
                "limit":       5,
                "format":      "json",
                "origin":      "*",
            }
            resp = requests.get(
                WIKIDATA_API,
                params=params,
                timeout=WIKIDATA_TIMEOUT,
                headers={"User-Agent": "NNI-TruthGraph/1.0 (entity-disambiguation)"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("search", [])

            if not results:
                return None

            # Score heuristic: Wikidata ranks by relevance; also penalise if the
            # canonical label doesn't overlap at all with the input.
            name_lower = name.lower()
            for item in results:
                label       = item.get("label", "")
                description = item.get("description", "")
                aliases     = [a.lower() for a in item.get("aliases", [])]

                # Check direct match or alias match
                label_lower = label.lower()
                if (
                    label_lower == name_lower
                    or name_lower in label_lower
                    or label_lower in name_lower
                    or name_lower in aliases
                ):
                    canonical = label
                    print(
                        f"  [Disambig·Wikidata] '{name}' → '{canonical}' "
                        f"({description[:60]})"
                    )
                    
                    # NEW: Trigger Tier 2 active ingestion for novel entity
                    self._spawn_background_ingestion(canonical)
                    
                    return canonical

            # Soft: if nothing matches well, don't guess
            return None

        except requests.exceptions.Timeout:
            # Wikidata timeout is common from restricted networks — just skip
            return None
        except Exception as exc:
            print(f"  [Disambig·Wikidata] Non-fatal error: {exc}")
            return None

    def _spawn_background_ingestion(self, canonical_name: str):
        """
        Dynamically trigger a Tier 2 background Wikipedia ingestion for a novel entity.
        This runs in a detached thread to prevent blocking the Stage 8 mutation loop.
        """
        def run_seed():
            import sys
            import os
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            try:
                from scripts.seed_wikipedia_base_layer import seed_worker
                print(f"  [Disambig·Tier2] Triggering active Wikipedia ingestion for novel entity: {canonical_name}")
                seed_worker(canonical_name)
            except Exception as e:
                print(f"  [Disambig·Tier2] Failed to spawn Wikipedia fetch for {canonical_name}: {e}")
                
        threading.Thread(target=run_seed, daemon=True).start()

    # ── Signal 4: LLM arbitration ─────────────────────────────────────────────

    def _signal_llm(self, name: str) -> Optional[str]:
        """
        Use Groq 70B as the final authority.  Asked to return the canonical
        Wikipedia-style name for the entity.  If the LLM is unavailable,
        returns None so the caller can use the normalised fallback.
        """
        if not self._groq:
            return None
        try:
            prompt = (
                f"You are an expert entity normaliser for a knowledge graph system.\n\n"
                f"Your task: given a possibly abbreviated, misspelled, or unofficial entity name, "
                f"return the single canonical Wikipedia-style English name for that entity.\n\n"
                f"Rules:\n"
                f"- Return ONLY the canonical name — no explanation, no punctuation, no extra words.\n"
                f"- If the input is a person, return 'FirstName LastName' format.\n"
                f"- If it is a country, return the full English country name (e.g. 'United States', 'Iran', 'United Kingdom').\n"
                f"- If it is an organisation, return the full official name.\n"
                f"- If it is a common concept, return the standard English term.\n"
                f"- If you are truly unsure, return the input unchanged.\n\n"
                f"Entity: {name}\n"
                f"Canonical name:"
            )
            resp = self._groq.chat_completions_create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "You are a canonical entity name resolver. Reply with ONLY the canonical entity name."},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.0,
                max_tokens=20,
            )
            canonical = resp.choices[0].message.content.strip().strip('"\'')
            if canonical and len(canonical) < 100:
                print(f"  [Disambig·LLM] '{name}' → '{canonical}'")
                return canonical
        except Exception as exc:
            print(f"  [Disambig·LLM] Non-fatal error: {exc}")

        return None


# ── Module-level singleton factory ────────────────────────────────────────────
_instance: Optional[EntityDisambiguator] = None
_instance_lock = threading.Lock()


def get_disambiguator(
    neo4j_driver=None,
    groq_pool=None,
    embed_fn=None,
) -> EntityDisambiguator:
    """
    Returns the process-level singleton EntityDisambiguator.
    On first call, creates it with the provided drivers.
    Subsequent calls return the cached instance (drivers ignored after first call).
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EntityDisambiguator(
                    neo4j_driver=neo4j_driver,
                    groq_pool=groq_pool,
                    embed_fn=embed_fn,
                )
                _instance.warm_cache()
    return _instance
