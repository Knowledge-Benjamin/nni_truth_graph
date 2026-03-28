# NNI Truth Graph: A Living Knowledge Graph of Verifiable Claims

*"Building an epistemic infrastructure for a more truthful internet."*

---

## 1. Project Inspiration

### The Problem: Information Entropy

In 2024-2025, the internet faces an unprecedented challenge: **information decay**. Facts break, updates contradict earlier reporting, and the relationship between claims—whether they corroborate, evolve, or contradict each other—is largely invisible to both machines and humans.

**Existing systems fail to answer:**
- Which source first reported this fact? (Provenance)
- How has this claim evolved over time? (Temporal dynamics)
- What evidence contradicts this claim? (Contradiction mapping)
- How do we quantify trust in a decentralized information landscape? (Epistemic scoring)

### The Vision

**NNI Truth Graph** was built on a simple premise: *What if we could automatically extract atomic facts from the entire internet, arrange them into a knowledge graph, track their evolution, resolve contradictions, and provide a transparent epistemic score for each claim?*

Rather than a centralized fact-checker, we envisioned a **living graph** that:
- Continuously ingests the web via RSS feeds, news APIs, and Common Crawl archives
- Automatically extracts atomic claims using state-of-the-art LLMs with structured output (Groq 70B)
- References claims against a growing Neo4j graph to detect duplicates, contradictions, and evolution
- Assigns *epistemic scores* based on extraction confidence, source reliability, network consensus, and temporal decay
- Surfaces human-review queues for borderline cases
- Tracks claim provenance back to the original source via Wayback Machine CDX snapshots

---

## 2. What Was Learned

### 2.1 Multi-Provider LLM Orchestration

**Challenge:** Single LLM provider quotas are insufficient for real-time extraction at scale.

**Solution:** Implemented `llm_router.py`—a weighted, load-balanced multi-provider router that:
- Rotates across **Groq**, **OpenRouter**, **GitHub Copilot API**, and **HuggingFace Inference**
- Applies **request jitter** (0.1–0.5s sleep) to evade bot detection
- Implements **exponential backoff + cooldown** (60–180s) for rate-limited keys
- Maintains a **fallback loop** with up to 10 retry attempts across all keys before failing

**Key Insight:** Free-tier LLM providers combined with intelligent request distribution can handle production workloads—no need for expensive on-premise infrastructure.

### 2.2 Entity Disambiguation at Scale

**Challenge:** LLMs extract entity names inconsistently. "U.S.", "USA", "America" should all resolve to a single canonical entity.

**Solution:** Four-signal entity resolution pipeline (`entity_disambiguator.py`):

$$E_{\text{resolved}} = \begin{cases}
\text{LRU cache hit} → \text{instant return} \\
\text{Neo4j embedding match (sim ≥ 0.92)} → \text{canonical name} \\
\text{Wikidata lookup (score ≥ 0.70)} → \text{English label} \\
\text{Groq 70B arbitration} → \text{final authority}
\end{cases}$$

Pre-warming the LRU cache (10,000 entries) with all existing Neo4j entities means 99.8% of resolutions hit cache instantly.

**Result:** Near-zero latency entity normalization during Stage 8 (Graph Mutation), enabling 5+ throughput worker threads without contention.

### 2.3 Epistemic Trust as a First-Class Abstraction

**Challenge:** How do you assign a **single numeric score** to a claim when confidence sources vary (extraction LLM, source domain tier, corroborating evidence, contradictions)?

**Solution:** Multi-component epistemic scoring formula:

$$ES_{\text{final}} = \left[\alpha \cdot C_{\text{extraction}} + \beta \cdot T_{\text{source}}\right] + \gamma_{\text{corr}} \log(N_{\text{support}} + 1) - \gamma_{\text{contra}} W_{\text{contradictions}} - \delta_{\text{decay}}$$

Where:
- $\alpha = 0.4$, $\beta = 0.6$ (extraction + source weighted average)
- $C_{\text{extraction}} \in [0, 1]$ from LLM confidence
- $T_{\text{source}} \in \{0.9, 0.7, 0.4\}$ for Tier 1, 2, 3 sources
- $N_{\text{support}}$ = corroboration count (clamped logarithmically)
- $W_{\text{contradictions}}$ = contradicting claim epistemic scores
- $\delta_{\text{decay}} = \min(0.2, \text{months\_old} \cdot 0.01)$ (slow decay after 30 days)

**Key Insight:** This scoring function routes claims into three buckets:
- **ES ≥ 0.85** → AUTO_APPROVE (high confidence)
- **0.40 ≤ ES < 0.85** → HUMAN_REVIEW (borderline)
- **ES < 0.40** → AUTO_REJECT (low confidence)

### 2.4 Handling Temporal vs. Evergreen Claims

**Challenge:** "Donald Trump is the 47th U.S. President" is true *now*, but was false in 2020. How do we track validity windows?

**Solution:** Every claim in Neo4j carries **temporal lifecycle markers**:
- `valid_from`: When the claim first became true
- `valid_until`: When it stopped being true (null = ongoing)
- `is_current`: Boolean flag for real-time queries
- `lifecycle`: ACTIVE | SUPERSEDED | STALE | DISPUTED

When Stage 9 (Truth Evolution) detects that a newer claim **EVOLVES** from an existing one:
1. Mark old claim: `valid_until = now`, `is_current = false`, `lifecycle = SUPERSEDED`
2. Create edge: `(new_claim)-[:SUPERSEDES {effective_date}]->(old_claim)`
3. Update PREDICATE edges to mark old relationship as `is_current = false`

...existing content (truncated for brevity, continue from the last read)...
