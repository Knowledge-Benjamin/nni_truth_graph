"""
scripts/merge_duplicate_entities.py
────────────────────────────────────────────────────────────────────────────
World-Class Entity Deduplication & Merger

Finds all duplicate/variant Entity nodes in Neo4j using a three-stage
clustering algorithm, then consolidates them into single canonical nodes.

Algorithm
─────────
  1. Fetch all Entity nodes from Neo4j
  2. Batch-embed all names using the HF Inference API
  3. Cluster names via union-find (UPGMA-style) on cosine similarity ≥ 0.88
  4. Use the EntityDisambiguator LLM signal to pick the canonical name per cluster
  5. Dry-run: print a full merge report — no writes
  6. Execute: call POST /api/entities/merge for each cluster (with --execute flag)

Usage
─────
  python scripts/merge_duplicate_entities.py --dry-run        # Preview (default)
  python scripts/merge_duplicate_entities.py --execute        # Apply merges
  python scripts/merge_duplicate_entities.py --threshold 0.85 # Custom sim threshold
"""

import os
import sys
import math
import json
import argparse
import requests
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, 'ai_engine', '.env'))

from neo4j import GraphDatabase
from ai_engine.core.groq_pool import groq_pool

NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
HF_TOKEN       = os.getenv("HF_TOKEN")
API_BASE       = f"http://localhost:{os.getenv('PORT', '8001')}/api"

HF_EMBED_URL = (
    "https://router.huggingface.co/hf-inference/models/"
    "sentence-transformers/all-mpnet-base-v2/pipeline/feature-extraction"
)
BATCH_SIZE = 64  # HF inference batch size


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed a batch of texts. Returns None for failed items."""
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    results = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        try:
            resp = requests.post(
                HF_EMBED_URL,
                headers=headers,
                json={"inputs": batch, "options": {"wait_for_model": True}},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            # data is list-of-list (one embedding per input)
            for item in data:
                results.append(item if isinstance(item, list) else None)
        except Exception as exc:
            print(f"  [embed_batch] Error on batch {i}: {exc}")
            results.extend([None] * len(batch))
    return results


# ── Cosine similarity ─────────────────────────────────────────────────────────

def cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


# ── Union-Find ────────────────────────────────────────────────────────────────

class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank   = [0] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


# ── Canonical name picker ─────────────────────────────────────────────────────

def pick_canonical(names: list[str]) -> str:
    """Use the LLM to pick the best canonical name from a cluster."""
    if len(names) == 1:
        return names[0]
    try:
        prompt = (
            "You are an expert entity normaliser.\n"
            "Pick the single best canonical Wikipedia-style English name from this list.\n"
            "Output ONLY the chosen name, nothing else.\n\n"
            "Candidates:\n" + "\n".join(f" - {n}" for n in names)
        )
        resp = groq_pool.chat_completions_create(
            model="TIER_HEAVY",
            messages=[
                {"role": "system",  "content": "You are a canonical entity name selector. Output ONLY the canonical name."},
                {"role": "user",    "content": prompt},
            ],
            temperature=0.0,
            max_tokens=20,
        )
        chosen = resp.choices[0].message.content.strip().strip('"\'')
        if chosen in names:
            return chosen
        # If LLM hallucinated something not in the list, fall back to most-mentioned
    except Exception as exc:
        print(f"  [pick_canonical] LLM error: {exc}")
    # Fallback: longest name (usually most complete)
    return max(names, key=len)


# ── Merge via API ─────────────────────────────────────────────────────────────

def apply_merge(target: str, sources: list[str]) -> bool:
    try:
        resp = requests.post(
            f"{API_BASE}/entities/merge",
            json={"targetEntity": target, "sourceEntities": sources},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"  ✅ Merged {data.get('merged', '?')} node(s) → '{target}'")
        return True
    except Exception as exc:
        print(f"  ❌ Merge failed for '{target}': {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Merge duplicate Entity nodes in Neo4j")
    parser.add_argument("--execute",   action="store_true", help="Apply merges (default: dry-run)")
    parser.add_argument("--threshold", type=float, default=0.88,
                        help="Cosine similarity threshold for clustering (default: 0.88)")
    parser.add_argument("--min-cluster", type=int, default=2,
                        help="Minimum cluster size to process (default: 2)")
    args = parser.parse_args()

    dry_run   = not args.execute
    threshold = args.threshold

    print("=" * 60)
    print("  NNI Truth Graph — Entity Deduplication Script")
    print(f"  Mode: {'DRY RUN' if dry_run else '⚡ EXECUTE'}")
    print(f"  Similarity threshold: {threshold}")
    print("=" * 60)

    # 1. Fetch all entities
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        records = session.run(
            "MATCH (e:Entity) RETURN e.name AS name, e.mention_count AS mc "
            "ORDER BY e.mention_count DESC"
        ).data()
    driver.close()

    names = [r["name"] for r in records if r.get("name")]
    counts = {r["name"]: r.get("mc", 0) or 0 for r in records}
    print(f"\n  Found {len(names)} Entity nodes in Neo4j.\n")

    if len(names) < 2:
        print("  Nothing to deduplicate.")
        return

    # 2. Embed all names
    print(f"  Embedding {len(names)} entity names in batches of {BATCH_SIZE}…")
    embeddings = embed_batch(names)
    valid = [(names[i], embeddings[i]) for i in range(len(names)) if embeddings[i]]
    print(f"  Got embeddings for {len(valid)}/{len(names)} entities.\n")

    if len(valid) < 2:
        print("  Not enough successful embeddings to cluster.")
        return

    # 3. Union-Find clustering by cosine similarity
    n     = len(valid)
    uf    = UnionFind(n)
    pairs = 0
    print(f"  Clustering {n} entities (O(n²) = {n*n:,} pairs)…")
    for i in range(n):
        for j in range(i + 1, n):
            ea, eb = valid[i][1], valid[j][1]
            if ea is None or eb is None:
                continue
            sim = cosine(ea, eb)
            if sim >= threshold:
                uf.union(i, j)
                pairs += 1
    print(f"  Found {pairs} similar pair(s) above threshold {threshold}.\n")

    # 4. Build clusters
    from collections import defaultdict
    clusters: dict[int, list[str]] = defaultdict(list)
    for i, (name, _) in enumerate(valid):
        clusters[uf.find(i)].append(name)

    merge_groups = [grp for grp in clusters.values() if len(grp) >= args.min_cluster]
    print(f"  {len(merge_groups)} cluster(s) candidates for merging:\n")

    # 5. For each cluster, pick canonical and show report
    merge_plan = []
    for grp in sorted(merge_groups, key=lambda g: -sum(counts.get(n, 0) for n in g)):
        canonical = pick_canonical(grp)
        sources   = [n for n in grp if n != canonical]
        total_mc  = sum(counts.get(n, 0) for n in grp)
        merge_plan.append((canonical, sources))
        print(f"  Cluster (total mentions: {total_mc}):")
        print(f"    Canonical  → {canonical!r}")
        for s in sources:
            print(f"    Merge      ← {s!r} ({counts.get(s, 0)} mentions)")
        print()

    if dry_run:
        print("─" * 60)
        print(f"  DRY RUN complete. {len(merge_plan)} merge group(s) identified.")
        print("  Run with --execute to apply.")
        return

    # 6. Apply merges
    print("─" * 60)
    print(f"  Applying {len(merge_plan)} merge group(s)…\n")
    ok = 0
    for canonical, sources in merge_plan:
        print(f"  Merging {sources} → '{canonical}'")
        if apply_merge(canonical, sources):
            ok += 1

    print(f"\n  Done. {ok}/{len(merge_plan)} merges succeeded.")
    print("  Re-run with --dry-run to confirm the graph is clean.")


if __name__ == "__main__":
    main()
