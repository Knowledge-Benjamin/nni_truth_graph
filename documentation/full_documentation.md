# NNI Truth Graph — Full Documentation

> This document is maintained incrementally. Sections are added and expanded over time.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [AI Pipeline Stages](#3-ai-pipeline-stages)
4. [Core Modules](#4-core-modules)
5. [Microservices](#5-microservices)
6. [Backend API (server/)](#6-backend-api-server)
7. [Frontend (client/)](#7-frontend-client)
8. [Database Schema](#8-database-schema)
9. [Configuration & Environment](#9-configuration--environment)
10. [Deployment](#10-deployment)

---

## 1. Project Overview

### 1.1 — What Is NNI Truth Graph?

NNI Truth Graph is an **autonomous, self-healing, multi-modal epistemic knowledge graph** — a continuously operating AI infrastructure platform that autonomously ingests internet content (text, video, images), extracts atomic factual claims from it, scores each claim's credibility using a proprietary multi-signal algorithm, archives the provenance of every claim against the historical internet, and commits the verified results into a living, queryable Neo4j graph database. As new information arrives that contradicts, updates, or corroborates existing facts, the graph automatically evolves to reflect the current state of truth.

The system is public-facing. It allows anyone — journalists, researchers, voters, platforms — to explore a knowledge graph of verified facts organized by entity (people, organizations, places, events), where every node carries a machine-computable credibility score, a full audit trail of sources, and a temporal history of how that fact has changed over time.

**The simplest honest description:** An AI system that reads the internet 24 hours a day, extracts every verifiable fact it encounters, checks each fact against everything else it knows, scores it mathematically for trustworthiness, and stores the result in a graph that a user can query, explore, and fact-check against in real time. When a fact changes in the real world, the system detects the change and updates the graph automatically, without any human intervention.

#### What Makes It Architecturally Unique

To understand NNI Truth Graph, you must understand what it is **not**:

- It is **not** a RAG chatbot (retrieval-augmented generation). It does not retrieve documents at query time and ask an LLM to summarize them. It pre-processes everything, and every fact is a permanent, scored, richly attributed node in a graph — not a floating chunk of raw text.
- It is **not** a search engine. It does not return links. It returns structured, verified knowledge: `[Entity] → [Relationship] → [Entity]`, with a score, a source, a date range, and a lifecycle state.
- It is **not** a static knowledge base. Every fact has a lifecycle — it can be `ACTIVE`, `DISPUTED`, `SUPERSEDED`, `RETRACTED`, or `STALE`. The system continuously re-checks old facts against the live internet and evolves the graph when reality changes.
- It is **not** a human editorial operation. There are no fact-checkers on staff. The entire pipeline — from reading a news RSS feed to writing a graph edge in Neo4j — is executed autonomously by AI processes orchestrated by a pressure-aware scheduler.

#### The Full End-to-End Flow: What The System Actually Does

The system executes **11 sequential pipeline stages** plus a continuous revalidation loop, each handled by a dedicated Python process managed by a pressure-aware Celery scheduler:

**Stage 1 — Continuous URL Ingestion (`1_ingest.py`)**
The pipeline begins by perpetually reading the internet. Stage 1 has two ingestion modes operating simultaneously:

1. *Static RSS Ingestion*: Reads configured trusted news sources from a PostgreSQL `sources` table (seeded with Tier 1/2/3 outlets from a curated `trusted_sources.json`). For each source, it respects HTTP 304 Not Modified headers to avoid re-fetching unchanged feeds, stores `feed_etag` and `feed_modified` per-source for conditional requests, and writes new article URLs to a `raw_urls` staging table.

2. *Dynamic Topic Hunting via SearXNG*: Queries the system's own graph — specifically the most recently committed claim subjects — and searches the live internet for those entities using a self-hosted SearXNG instance (multi-engine: Google, Bing, DuckDuckGo). If the graph has just committed a claim about "Elon Musk", Stage 1 immediately hunts the internet for new Elon Musk stories. The pipeline is self-directing — what it already knows shapes what it looks for next.

**Stage 2 — Concurrent Browser-Level Web Scraping (`2_scrape.py`)**
For every URL in the staging queue, Stage 2 performs full browser-level extraction using Playwright (headless Chromium). This is not a simple `requests.get()` — it loads the full page in a real browser, waits 2 seconds for JavaScript frameworks to hydrate, then extracts clean `innerText`. Critically, it also:

- Discovers cross-linked article URLs within the article body and injects them back into the `raw_urls` queue as a crawler — the pipeline follows its own leads
- Extracts the article's Open Graph hero image URL and immediately forwards it to VisionInferenceServer for deepfake scoring and SigLIP embedding — visual media analysis begins at the moment of scraping
- Maintains pre-flight domain blocklists: known paywalls (Bloomberg, WSJ, FT, NYT) are skipped without wasting requests; known dead-ends (Reuters, APNews, most academic DOIs) are immediately marked `FAILED_NO_ACCESS`
- Implements per-URL retry logic with a `retry_count` metadata field, permanently failing after 3 attempts

**Stage 2A — Multimedia Video Scraping (`2a_video_scrape.py`)**
A parallel sub-process for video-domain URLs (YouTube, TikTok, Twitter/X, Instagram, Vimeo). Stage 2A:

- Downloads video files using `yt_dlp` (temporary `/tmp/` storage, immediately cleaned after processing)
- Transcribes the audio stream using **Groq's Whisper large-v3-turbo** — converting spoken words in news broadcasts, social videos, and press conferences to text
- Extracts 5 chronological keyframes using OpenCV, resizes them to 512×512, and base64-encodes them
- Sends all keyframes to VisionInferenceServer for the full dual-ViT deepfake ensemble analysis
- Stores the keyframe base64 blobs in `raw_articles.metadata` for the VLM narration step in Stage 4
- The result: a video news clip becomes a transcribed text document + a deepfake score, without discarding the visual evidence

**Stage 3 — Semantic Vector Classification (`3_classification.py`)**
Before expensive LLM claim extraction begins, Stage 3 generates a 768-dimensional semantic embedding of each article's first 4,000 characters using `sentence-transformers/all-mpnet-base-v2` via the self-hosted InferenceServer. This embedding is stored in a `article_categories` table backed by the `pgvector` extension in PostgreSQL. Its primary function is to enable Stage 6's vector-similarity deduplication — the embedding represents the article's semantic identity and is used to detect when two articles from different sources are reporting the same underlying fact, without any exact string matching.

**Stage 4 — Atomic Claim Extraction (`4_extraction.py`)**
This is the intellectual centrepiece of the pipeline. Stage 4 sends each article to **Llama 3.3 70B** via a rotating multi-key Groq pool, with a precisely engineered extraction prompt that enforces:

- **SPO triples**: Every claim is extracted as a `(Subject, Predicate, Object)` triple — `("Elon Musk", "ACQUIRED", "Twitter")`. Subjects and objects must be pure nouns or entity names, not sentences.
- **Anaphoric resolution**: Pronouns are forbidden. If an article says "He fired the executives", the LLM must resolve "He" to the actual entity name.
- **Standardized predicate ontology**: Relationships are normalized to a controlled vocabulary (IS_A, ACQUIRED, LAUNCHED, STATED, DISCOVERED, GOT_PHD, etc.)
- **Temporal and spatial anchors**: Every claim requires an explicit `temporal_anchor` ("2024-11-05") and `spatial_anchor` ("Washington D.C.")
- **Verifiability filter**: Subjective opinions are not extracted. Only claims that can be objectively proven true or false are included.
- **NOMA domain classification**: Claims are classified into one of four Non-Overlapping Magisteria: `EMPIRICAL`, `THEOLOGICAL`, `PHILOSOPHICAL`, or `LEXICAL`

For articles with video keyframes from Stage 2A, Stage 4 first calls a **Vision-Language Model (Llama 3.2 90B Vision)** to narrate what is happening chronologically in the video frames. This visual forensic narrative is appended to the text before claim extraction — the system reasons over the visual content, not just the transcript.

After extraction, each claim undergoes **cross-modal visual verification**: the claim text is embedded via SigLIP's text encoder, and the text embedding is compared against the hero image's SigLIP visual embedding stored from Stage 2 using pgvector cosine distance. A very low similarity (< 0.15) penalizes the claim's extraction confidence by 60% — the image contradicts the text (clickbait). A very high similarity (> 0.85) boosts it by 25%.

The preliminary epistemic score is then calculated and the claim is written to `extracted_claims`.

**Stage 5 — Provenance & Network Resolution (`5_resolution.py`)**
This stage answers the deepest question: *where did this fact actually originate on the internet, and what does the internet's archive say about it?*

For each claim, Stage 5:
1. Searches SearXNG across multiple engines for the claim's core SPO text, collecting URLs that discuss this combination of entities
2. For each found URL, queries both the **Wayback Machine CDX API** and the **Common Crawl CDX API** to determine the earliest archived occurrence — picking the definitive, historically-grounded first-appearance timestamp
3. Compares this timestamp against the article's own publication date. If the internet archived this claim *before* the article we scraped, the article is not the original source — the system marks the older discovered URL as the likely original and fires it into Stage 1 as a new ingestion target
4. Queries the Neo4j graph for existing claims with matching subjects using vector embedding similarity
5. Calls the LLM to determine the **epistemic stance** of the new claim against any existing graph claims: `ORIGINAL | CORROBORATES | CONTRADICTS | EVOLVES | ENRICHES | DUPLICATE`
6. Records the complete provenance record in the `claim_provenance` table: source URL, archive timestamps, stance, matched claim ID, and cosine similarity score

**Stage 6 — Three-Layer Claim Deduplication (`6_deduplication.py`)**
Before any claim reaches the graph, it must survive a three-layer deduplication process designed to prevent the same fact (reported by 50 different news outlets) from creating 50 duplicate graph nodes:

- **Layer 1 — Exact Fingerprint**: SHA-256 hash of normalized (lowercase, stripped) `subject + predicate + object_entity`. Zero API cost. Catches verbatim string-identical claims instantly.
- **Layer 2 — Vector Cosine Similarity**: Compares the article's 768D semantic embedding (from Stage 3) against embeddings of all existing canonical claims with the same predicate. Only pairs scoring above 0.94 advance to the LLM judge.
- **Layer 3 — LLM Semantic Judge (Llama 3.1 8B)**: Receives the full SPO + temporal/spatial context for both claims and determines `DUPLICATE` or `DISTINCT`. If DUPLICATE, a second LLM call determines `RICHER` or `SAME_DETAIL` — if the incoming claim has more specific temporal or spatial detail than what's already in the graph, it is marked as an ENRICHMENT rather than discarded.

When a claim is identified as a duplicate, it is not simply deleted — its source is added to the canonical claim's **Corroboration Fossil Record** (`claim_corroborations` table), and the canonical claim's epistemic score is recalculated to reflect the additional independent corroboration.

**Stage 7 — Epistemic Cross-Reference & Stance Detection (`7_cross_reference.py`)**
Stage 7 queries Neo4j for existing claims about the same subject entity, asks the LLM to classify the epistemic stance between the incoming and existing claims, then re-calculates the final epistemic score incorporating network-level intelligence: the number of supporting claims, the severity of any contradictions, the trust scores of the contradicting sources. The result determines the claim's routing: `AUTO_APPROVE` (score ≥ 0.75), `HUMAN_REVIEW` (score 0.40–0.75), or `AUTO_REJECT` (score < 0.40).

**Stage 8 — Graph Mutation (`8_graph_mutation.py`)**
Stage 8 commits approved claims to Neo4j. Before writing anything, entities are canonicalized through the Entity Disambiguator (4-signal pipeline: LRU cache → Neo4j embedding similarity → Wikidata → LLM), ensuring "Elon Musk", "E. Musk", and "the Tesla CEO" all resolve to the same graph node. The mutation creates or merges: `Entity` nodes, `Claim` nodes, `Source` nodes, `PREDICATE` edges (with `is_current`, `valid_from`, `valid_until`), `SUPPORTED_BY` edges to evidence, `PUBLISHED_BY` edges, and `Timeline` nodes grouping the full claim history per subject-predicate pair.

**Stage 9 — Truth Evolution Engine (`9_truth_evolution.py`)**
A continuous sweep that handles temporal reality drift. For every newly committed claim with an `EVOLVES` stance, Stage 9 retires the old claim in Neo4j (`is_current = false`, `lifecycle = SUPERSEDED`, `valid_until = now`) and creates a `SUPERSEDES` edge from new to old with an effective date and similarity score. For `CONTRADICTS` stances, both claims become `DISPUTED`, a `Controversy` node is created grouping them, both epistemic scores are reduced by 0.10, and both are routed to `HUMAN_REVIEW`. Source trust scores are adjusted after every resolution: correctly predicting evolution earns +0.01, publishing a contradicted claim costs -0.05.

**Stage 10 — Claim Revalidation Daemon (`10_revalidation.py`)**
On a 24-hour schedule, Stage 10 sweeps all `ACTIVE` claims older than 30 days. For each, it executes a fresh Google Search (via Serper API) for the claim's SPO text, then calls an LLM to assess each search result as `CORROBORATES | SUPERSEDES | CONTRADICTS | UNRELATED`. Corroborations boost the epistemic score and reset the freshness clock. Supersessions or contradictions fire the new source article directly into Stage 1 for a full pipeline run — the old claim will be retired by Stage 9 after the new evidence propagates through. Claims with zero corroboration after 90 days are marked `STALE` and their scores are decremented. The graph is not a snapshot — it is a living model of current truth.

#### Parallel Daemon: The Universal Ontology Engine (`ontology_worker.py`)

Running in parallel with the 11-stage pipeline — launched independently by `main.py` — is the **Ontology Engine**. Its job is to ensure every `Entity` node in the Neo4j graph is connected into a hierarchical semantic taxonomy, not just a flat collection of names.

After Stage 8 commits new `Entity` nodes to Neo4j, they exist in the graph but are "orphaned" — they have no type hierarchy, no conceptual ancestors, and no domain classification. The Ontology Engine continuously scans for these orphaned entities and, for each one, sends a single deterministic LLM query (temperature=0.0, Llama 3.3 70B, Pydantic-validated) to classify:

- **IS_A relationships**: What broader class does this entity belong to? (`Donald Trump IS_A Politician`, `Carbon IS_A Chemical Element`)
- **PART_OF relationships**: What physical or structural container does it sit inside? (`Paris PART_OF France`, `Finger PART_OF Hand`)
- **Epistemic Domain**: Classified into exactly one of `EMPIRICAL | THEOLOGICAL | PHILOSOPHICAL | LEXICAL`

The LLM response is immediately converted into bidirectional graph edges — `IS_A` + `SUBCLASS_OF`, `PART_OF` + `CONTAINS` — building a live ontological tree from the bottom up. This is what transforms the graph from a flat fact store into a navigable semantic hierarchy. It also ensures that the Stage 7 cross-reference engine can correctly scope its Neo4j queries by domain — theological claims are never cross-referenced against empirical ones.

#### Parallel Daemon: The Living Article Engine (`article_worker.py`)

The second parallel daemon — also launched independently by `main.py` — is the **Living Article Engine**. Its purpose is to synthesize every accumulated fact about an entity into a continuously-updated, Wikipedia-style encyclopedic article stored directly on the `Entity` node in Neo4j.

Every 10 minutes, the Article Engine identifies entities that have either never had an article generated, have had new claims committed since their last generation, or are flagged as `article_stale = true`. For each entity, it:

1. **Retrieves all committed claims** about that entity from PostgreSQL, sorted by corroboration count and epistemic score — the most well-supported facts rise to the top
2. **Fetches the corroboration fossil record** — the full timeline of when and by whom each fact was reported — to construct an evidence evolution narrative
3. **Finds semantically related source texts** using pgvector cosine distance against the article embeddings — providing raw journalistic depth to inject as background context
4. **Groups claims by section** according to a predicate ontology map (Foundational Overview, Structural Relationships, Geographic Context, Demographics & Culture, Historical Events, Ecosystem Interactions, Contradictions & Disputes)
5. **Generates each section incrementally** using Llama 3.3 70B with a 7-layer constraint system: entity hyperlink enforcement (`[[Entity Name]]` markdown), UUID-level citation referencing (`[REF:<uuid>]`), corroboration badge injection (🥇 for ≥3 corroborations, 🥈 for 2), visual evidence warnings (🚨 for claims with deepfake probability > 85%), authentic media verification (✅ for confirmed visual evidence), information evolution timeline embedding, and hallucination guardrails (only facts in the verified claim base may be cited)
6. **Skips unchanged sections** using MD5 hashing of the section's claim UUIDs — only sections with new or changed claims are regenerated (incremental update, not full regeneration)
7. **Stores the article as structured JSON** on the `Entity.article` property — each section has its own content block, hash, used UUIDs, and last_updated timestamp. A `_references` array provides a full source attribution table sorted by corroboration count.

The result: every entity in the knowledge graph has a living, continuously-updated, fully-cited, multi-section encyclopedic article that evolves as new facts are discovered. When a claim is superseded by Stage 9, the article engine regenerates the affected section. When a new corroboration is added by Stage 6, the article's evidence badges update. The article is never stale — it is always a real-time synthesis of the current ground truth.

#### How The System Starts: `main.py` as the Unified Process Supervisor

When the AI Engine starts, `main.py` acts as a **unified process supervisor**. It executes the following startup sequence, resulting in **4 subprocesses + 4 background daemon threads** all running concurrently under a single Python process:

**Subprocesses (long-lived, each with piped stdout/stderr streamed in real time):**
1. **Celery Worker** — `celery -A celery_app worker --pool=threads -c 2` — executes all 10 pipeline stage tasks, 10 concurrent slots, 30-minute hard kill per task
2. **Pipeline Orchestrator** — `worker.py` — pressure-aware scheduler that monitors PostgreSQL queue depths and dispatches stages via Celery
3. **Ontology Engine** — `scripts/ontology_worker.py` — continuous loop classifying orphaned Neo4j Entity nodes into IS_A/PART_OF hierarchies
4. **Living Article Engine** — `scripts/article_worker.py` — 10-minute cycle generating encyclopedic articles for all entities *(note: `seed_baseline_knowledge.py` is defined but currently commented-out / disabled)*

**Daemon threads (lightweight, in-process):**
5. **stdout stream readers** — one per subprocess (4 total), reading raw bytes and printing labeled output
6. **stderr stream readers** — one per subprocess (4 total), for error visibility
7. **Terminal Cleaner** — clears stdout every 5 minutes to prevent log buffer accumulation crashes in cloud/HF Spaces environments
8. **Inference Health Pinger** — pings InferenceServer `/health` every ~60 minutes (±5-min jitter) to prevent cold-start latency on Hugging Face Spaces

The **Task Dispatcher** (`tasks.py`) defines the Celery task surface — a single `launch_pipeline_stage(script_name)` shared task that dynamically imports and calls the correct pipeline function, plus a `run_tier3_ingestion` task for supplementary academic source ingestion (OpenAlex abstracts, GDELT event data).

#### The Epistemic Trust Score — Full Mathematical Specification

Every claim in the graph carries a single float `[0.0, 1.0]` — its **Epistemic Trust Score (ES)**. This is not a tag or a label. It is a continuously recalculated, multi-signal algebraic score defined precisely in `ai_engine/core/epistemic_trust.py`. The formula is:

```
ES = clamp( (α × 0.4) + (β × 0.6) + γ_net − δ + ε, 0.0, 1.0 )
```

Each signal is defined as follows:

**α — Extraction Confidence (weight: 0.4)**
The raw extraction confidence reported by Llama 3.3 70B during Stage 4, adjusted by cross-modal visual verification. If the claim's text embedding has cosine similarity < 0.15 with the article's SigLIP image embedding, confidence is penalized by 60% (clickbait detection). If similarity > 0.85, it is boosted by 25%.

**β — Source Reliability (weight: 0.6)**
The publishing source's historical epistemic trust score. If available, this is the source's dynamically maintained reliability score from the `sources` table. If not, it falls back to tier defaults:

| Tier | Example Sources | Default β |
|---|---|---|
| Tier 1 | Official Government, Peer-reviewed Journals | 0.90 |
| Tier 2 | Major News Outlets (AP, Reuters, BBC) | 0.70 |
| Tier 3 | Blogs, Social Media, Unknown | 0.40 |

**γ — Network Consensus (corroboration bonus − contradiction penalty)**
This is the most complex component. When corroboration records exist (the Fossil Record from Stage 6):

- The youngest corroboration's timestamp resets the temporal decay clock (δ revitalization)
- All corroboration timestamps are grouped into 4-hour windows to detect burst patterns
- **Bot Swarm Detection**: If ≥5 corroborations arrive within the same 4-hour window AND all are Tier 3 sources → bonus is severely capped (`log10(n+1) × 0.02`) — this is a social media bot swarm, not genuine consensus
- **Breaking News Detection**: If ≥5 corroborations burst AND at least one is Tier 1 or 2 → the maximum bonus (0.30) is immediately granted — genuine viral breaking news spreads this way
- **Healthy Temporal Spread**: Corroborations arriving across different time windows score `log10(n+1) × 0.10`, with a 20% bonus multiplier if corroborators span multiple source tiers (multi-domain independence reward)
- **Contradiction penalty**: `sum(contradicting_claim_score × 0.15)` for every contradicting claim found in Stage 7
- γ_net is capped: `corroboration_bonus − contradiction_penalty`, max corroboration bonus = 0.30

**δ — Temporal Decay**
Decay begins only after 30 days of complete silence (no new corroborations). After that grace period:
```
δ = min(0.20,  ((days_old − 30) / 30) × 0.01 )
```
The newest corroboration in the Fossil Record resets the clock — a claim that is still being actively reported never decays. Maximum possible decay: −0.20.

**ε — Visual Synthetic Probability (Absolute Override)**
The deepfake score from VisionInferenceServer's dual-ViT ensemble is the only signal that can **completely override** the rest of the algorithm:

| synthetic_prob | Effect |
|---|---|
| > 0.85 | **Nuclear Strike: return 0.0 immediately.** Algorithm bypassed entirely. |
| > 0.70 | ε = −0.40 (Massive penalty: high suspicion of synthesis) |
| < 0.10 | ε = +0.25 (Photographic Proof Bonus: cryptographically raw media) |
| < 0.30 | ε = +0.15 (Generally untouched visual support) |
| 0.30 – 0.70 | ε = 0.0 (Ambiguous/low-res: rely on text-only algorithm) |

**Routing thresholds** (from `determine_routing()`):

| Epistemic Score | Route |
|---|---|
| ≥ 0.85 | `AUTO_APPROVE` — committed to graph immediately |
| 0.40 – 0.84 | `HUMAN_REVIEW` — queued for manual verification |
| < 0.40 | `AUTO_REJECT` — not written to graph |

**Worked Examples** (from the scorer's own test suite):

| Scenario | Score | Route |
|---|---|---|
| Tier 1 source, 0.95 LLM confidence, 5 corroborations, 5 days old | High | AUTO_APPROVE |
| Tier 3 blog, 0.30 confidence, 2 contradictions from 0.85-score claims | Very low | AUTO_REJECT |
| Tier 3 citizen video, 0.85 confidence, synthetic_prob=0.03 (raw footage) | Boosted | HUMAN_REVIEW/AUTO_APPROVE |
| Any source, synthetic_prob=0.99 (deepfake confirmed) | **0.000** | AUTO_REJECT |

This is the infrastructure that makes trust mathematically computable — not an opinion, not a label, but a continuously recalculated, evidence-weighted float that reflects the current state of knowledge about a specific factual claim.

#### The Four Supporting Microservices

The AI pipeline does not operate in isolation. It depends on four purpose-built microservices, each independently deployed on Hugging Face Spaces or Google Cloud Run:

---

**VisionInferenceServer** (`VisionInferenceServer/main.py`) — *Visual Forensics Engine*

A FastAPI server that provides two critical AI capabilities:

1. **SigLIP Visual Embeddings** (`POST /embed_media`): Accepts base64-encoded images (or video keyframes from Stage 2A), produces 768-dimensional CLIP-style embeddings using Google's SigLIP model. These embeddings are stored in the `media_provenance` table and used by Stage 4's cross-modal verification to compute semantic alignment between text claims and visual evidence.

2. **Dual-ViT Deepfake Detection** (`POST /embed_media`): Alongside the embedding, runs a dual Vision Transformer ensemble scoring model that produces `synthetic_probability` — the probability that the image/frame was AI-generated or digitally manipulated. This score feeds directly into the ε term of the Epistemic Trust Algorithm as an absolute override signal.

3. **Text Embedding for Vision** (`POST /embed_text`): Embeds text strings through SigLIP's text encoder so they can be compared in the same latent space as visual embeddings — enabling the cross-modal cosine similarity check in Stage 4.

All requests are batched and the primary VisionInferenceServer communicates over a persistent HTTP connection from Stage 2 and Stage 4.

---

**InferenceServer** (`InferenceServer/main.py`) — *Text Embedding Service*

A secure FastAPI server deployed on Hugging Face Spaces that loads `sentence-transformers/all-mpnet-base-v2` with GPU acceleration when available. It exposes a single authenticated endpoint:

- `POST /embed`: Accepts a batch of up to 32 text strings, returns a list of 768-dimensional float vectors
- `GET /health`: Used by `main.py`'s inference health pinger daemon to keep the HF Space warm (prevents cold-start latency)

All pipeline modules that need text embeddings — Stage 3 classification, Stage 6 deduplication, the Article Engine's semantic excerpt search — go through the `inference_pool` singleton in `core/inference_pool.py`, which maintains an HTTP session with Bearer token auth, automatic retry logic, 429/503 cooldown handling, and thread-safe locking. `hf_pool` and `groq_pool` are both backward-compatibility aliases pointing to this infrastructure.

---

**SearchServer** (`SearchServer/`) — *Privacy-First Meta-Search Engine*

A self-hosted instance of **SearXNG** — an open-source, privacy-respecting metasearch engine. Configured to aggregate results from Google, Bing, and DuckDuckGo simultaneously and return structured JSON. The pipeline uses it in three places:

- **Stage 1 (Dynamic Topic Hunting)**: SearXNG hunts for new articles about the entities the graph has recently committed claims for
- **Stage 5 (Provenance Resolution)**: SearXNG finds all URLs discussing a specific claim's SPO triple, providing the raw URL list for Wayback Machine and Common Crawl archive lookups
- **Stage 10 (Revalidation)**: SearXNG (via Serper API as a supplementary fallback) re-searches claim text to find corroborating or superseding content

The SearchServer is configured with `safe_search: 0` (no filtering), `public_instance: false` (private to this deployment), and `limiter: false` (no rate limiting against itself).

---

**cc_proxy** (`cc_proxy/app.py`) — *Common Crawl Reverse Proxy*

A lightweight FastAPI reverse proxy that sits between the AI pipeline and the [Common Crawl](https://commoncrawl.org/) index API (`index.commoncrawl.org`). Common Crawl is a petabyte-scale public web archive containing billions of crawled pages going back years — used by Stage 5 to determine the earliest internet archive timestamp for any given URL, providing historically-grounded provenance evidence independent of the Wayback Machine.

The proxy serves two purposes:
1. **Collection Index Caching**: On startup, it fetches and caches `collinfo.json` (the list of all ~100 Common Crawl indexes spanning 2008–present) in memory, serving it instantly to pipeline requests — avoiding repeated slow cold fetches
2. **URL Rewriting**: Rewrites CDX API URLs in the collinfo response to route through the proxy itself, making Common Crawl appear as a local service to the pipeline
3. **Transparent Proxying**: All CDX queries (e.g., search for a URL's crawl records across all indexes) are transparently forwarded with a properly identified User-Agent (`KnowledgeBenjiTruthGraphBot/1.0`)

---

#### The Backend API Server (`server/`)

A Node.js/Express server running on port 4000. This is the **user-facing API layer** — the bridge between the databases (Neo4j + PostgreSQL) and the frontend or external consumers. It is architecturally independent of the Python AI pipeline and is deployed separately.

**On startup**, the server:
1. Runs PostgreSQL schema migrations automatically (creates all 9 tables: `sources`, `raw_urls`, `raw_articles`, `article_categories`, `media_provenance`, `extracted_claims`, `claim_provenance`, `auth_users`, `auth_invites`, plus `api_keys` and `graph_outbox`)
2. Sets up Neo4j constraints (`Entity.id UNIQUE`, `Claim.id UNIQUE`, `Source.url UNIQUE`) and indexes (`entity_name_idx`, `claim_epistemic_idx`, HNSW 768D vector index for `Claim.embedding`)
3. Forks the **Outbox Worker** as a child process
4. Attaches the **WebSocket Firehose** handler to the HTTP server

**API Route Surface:**

| Route | Description |
|---|---|
| `GET /api/health` | Liveness check — tests both Neo4j and PostgreSQL connections |
| `GET /api/search?q=` | Full-text entity/claim search across the graph |
| `GET /api/entity/:name` | All claims for a named entity + its article JSON |
| `GET /api/claim/:id` | Single claim with full evidence chain and provenance |
| `GET /api/timeline/:subject/:predicate` | Full truth timeline for a subject-predicate pair (history of how a fact evolved) |
| `GET /api/contradictions` | All open DISPUTED claims and Controversy nodes |
| `GET /api/contradiction/:id` | Single controversy with both competing claims |
| `GET /api/human-review` | Paginated queue of `HUMAN_REVIEW` claims for admin triage |
| `POST /api/human-review/:id/resolve` | Resolve a claim (APPROVE/REJECT/RETRACT) — writes to `graph_outbox` |
| `GET /api/sources` | Source trust leaderboard sorted by epistemic_trust_score |
| `GET /api/stats` | Live graph statistics — polled by the frontend every 15 seconds |
| `GET /api/media/*` | Media verification endpoints |
| `POST /api/auth/*` | User authentication (login, signup, invite-based onboarding) |
| `GET /api/v1/b2b/claims` | **B2B API**: paginated `GRAPH_COMMITTED` claims with `min_score` and `subject` filters |
| `GET /api/v1/b2b/sources` | **B2B API**: source trust rankings |
| `GET /api/v1/b2b/datasets/daily-snapshot` | **B2B API**: daily CSV snapshot download |
| `GET /api/developer/keys` | List authenticated user's API keys (prefix + metadata only, never raw) |
| `POST /api/developer/keys/generate` | Generate a new `sk_live_` prefixed API key (SHA-256 hashed before storage, raw key returned once) |
| `POST /api/developer/keys/:id/revoke` | Revoke an API key |
| `WS /firehose?api_key=` | **Enterprise WebSocket**: real-time stream of newly committed claims for subscribed entity subjects |

**The Outbox Worker** (`outbox_worker.js`): A forked child process that polls `graph_outbox` every 5 seconds using `FOR UPDATE SKIP LOCKED`. When a human reviewer resolves a claim via the API, the decision is written to `graph_outbox`. The outbox worker picks it up and applies the actual `extracted_claims` state change — APPROVE routes the claim to Stage 8, REJECT marks `AUTO_REJECT`, RETRACT marks `RETRACTED`. This decouples the HTTP response from the database mutation, preventing reviewer UI latency.

**The WebSocket Firehose** (`routes/firehose.js`): An enterprise-tier real-time event stream. It uses PostgreSQL's native `LISTEN/NOTIFY` mechanism — a `trigger_claim_committed` trigger fires `pg_notify('claim_committed', ...)` whenever a claim transitions to `GRAPH_COMMITTED` status. The firehose worker LISTENs on this channel and broadcasts new claim events to all connected WebSocket clients. Clients authenticate with a `sk_live_` API key and can subscribe to specific entity subjects — they only receive events for their subscribed entities (or `*` for all). Access is restricted to `enterprise`-tier API keys.

**SSR (Server-Side Rendering)**: The server intercepts `/entity/:slug` and `/claim/:id` routes before serving the React SPA, injecting OpenGraph meta tags (`og:title`, `og:description`, `twitter:card`) populated from live Neo4j data — enabling social previews when links are shared.

---

#### The Frontend (`client/`)

A **React + Vite** single-page application served statically from the Express server's `/client/dist`. The frontend is a full-featured UI for exploring, verifying, and managing the living knowledge graph.

**Pages:**

| Route | Page Component | Description |
|---|---|---|
| `/` | `ExplorerPane.jsx` | The primary UI — entity/claim search, interactive graph visualization, claim inspector, corroboration evidence, timeline view. The largest file in the codebase (~95KB, 1600+ lines) |
| `/entity/:slug` | `ExplorerPane.jsx` | Deep-links directly to an entity view |
| `/claim/:id` | `ExplorerPane.jsx` | Deep-links directly to a specific claim |
| `/verify` | `MediaPortal.jsx` | **Media Verification Portal** — upload an image or URL to check its deepfake score via VisionInferenceServer |
| `/contradictions` | `ContradictionsPanel.jsx` | Admin-only: browse all open `DISPUTED` Controversy nodes |
| `/review` | `HumanReviewQueue.jsx` | Admin-only: triage queue for `HUMAN_REVIEW` claims (APPROVE/REJECT/RETRACT) |
| `/articles` | `ArticleDashboard.jsx` | Admin-only: monitor and manage the Living Article Engine's output |
| `/developer` | `DeveloperDashboard.jsx` | Authenticated: API key management, usage metrics, firehose docs |
| `/docs` | `ApiDocs.jsx` | Public: full API documentation |
| `/login`, `/signup` | `Login.jsx`, `Signup.jsx` | Auth flows — invite-based signup |
| `/account` | `Account.jsx` | User account settings |

**Key architectural patterns:**
- **Auth model**: JWT-based, stored in httpOnly cookies. `ProtectedRoute` component gates admin-only pages. Role check is `user.role === 'admin'` — admin-restricted nav items (Controversies, Human Review, Article Engine) are hidden entirely from non-admin users.
- **Live stats**: `App.jsx` polls `GET /api/stats` every 15 seconds and displays `ACTIVE CLAIMS` count in the header. Human Review badge count updates live and turns red when non-zero.
- **Responsive**: Full mobile support with a hamburger/drawer navigation pattern with backdrop blur, replacing the desktop nav on small screens.
- **Entity hyperlinks**: The `ArticleRenderer.jsx` component parses `[[Entity Name]]` syntax in article JSON and renders them as clickable links that navigate to `/entity/:slug`, creating a Wikipedia-like internal link ecosystem.
- **NodeInspector.jsx**: Renders the full claim detail panel — epistemic score visualization, corroboration fossil record, provenance chain, source attribution, stance badges, deepfake probability indicator.

---

### 1.2 — The Problem Space

The system is built to solve **five interconnected, real-world information failures** that currently have no adequate automated solution.

---

#### Problem 1: Misinformation Spreads Faster Than Any Human Can Correct It

**The speed asymmetry is catastrophic.**

In January 2024, an AI-generated robocall using a cloned voice of President Biden told New Hampshire Democratic primary voters: *"Don't vote on Tuesday. Save your vote for November."* The call reached thousands of voters before it was identified as fake. By the time investigations began, the primary had already taken place.

In the 2024 US election cycle, false claims about Haitian immigrants in Springfield, Ohio "eating pets" — a claim with zero factual basis — generated **millions of views and thousands of memes** within 48 hours, was amplified by major political candidates on national television, and triggered bomb threats that forced school evacuations. No fact-check reached the same audience that the original claim did.

This is not a new problem. It is a **structural one**:

| | Misinformation | Traditional Fact-Checking |
|---|---|---|
| **Speed** | Seconds to minutes | Hours to days |
| **Scale** | Infinite (bots, shares) | Limited (human labor) |
| **Model** | Virality-first | Accuracy-first |
| **Reach** | Algorithmic amplification | Dependent on same algorithms |
| **Cost** | Near zero for bad actors | High (editorial labor) |

Snopes, PolitiFact, FullFact, and similar organizations are excellent — but they are **reactive, manual, and under-resourced**. They investigate *after* a claim has already gone viral. A correction almost never reaches the same audience as the original lie.

> **The core problem:** By the time truth catches up, the damage is done. What is needed is a system that operates at the *same speed as news itself* — autonomous, continuous, and scaled.

---

#### Problem 2: We Cannot Verify *Who* Published Something First

On March 15, 2022, dozens of outlets simultaneously published claims about troop movements near Kyiv. Within hours, conflicting reports emerged. The question "who reported this first and from what source?" became impossible to answer through normal search — Google shows results by *relevance*, not by *chronological provenance*.

This failure shows up constantly:

- A social media post claims a CEO resigned. Was this *original reporting* or did it copy a Reuters wire from 3 hours earlier?
- A political rumor claims a bill was passed. Was this from an official government press release or from an opinion blog that misread a draft?
- A viral image shows damage from a natural disaster. Was the photo taken this week, or was it recycled from a 2017 event in a different country?

**Current tools cannot automatically answer these questions.** Wayback Machine is a passive archive — it does not proactively cross-reference claims. Google Search does not return "first verified occurrence." Fact-checkers investigate manually, one claim at a time.

> **The core problem:** Provenance — *the origin trail of a fact* — is invisible in the modern information ecosystem. Without provenance, credibility cannot be quantified.

---

#### Problem 3: Deepfakes and Synthetic Media Have Broken Visual Evidence

Visual media has historically been a gold-standard of evidence. *"I'll believe it when I see it."* That standard no longer holds.

In 2023, AI-generated images of an explosion near the Pentagon circulated on Twitter/X and briefly caused a dip in the US stock market before the Pentagon confirmed no explosion had occurred. The "photographic evidence" was entirely fabricated.

In the 2024 US election, AI-generated images of Donald Trump posing with Black voters and a fabricated image of Kamala Harris in a hit-and-run incident from 2011 both achieved significant viral reach. Each required substantial manual investigation to debunk.

The **"liar's dividend"** compounds this: politicians now routinely claim that *real, genuine, damaging footage* of themselves is AI-generated. The existence of deepfakes gives bad actors a ready-made excuse to dismiss authentic evidence.

Current approaches to this problem:
- **Manual review** — not scalable
- **Watermarking** — trivially bypassed, not retroactive
- **Standalone deepfake detectors** — exist, but are completely disconnected from the fact-checking ecosystem

> **The core problem:** Visual evidence cannot be trusted at face value, and there is no production system that integrates AI-generated media detection *directly into the credibility score of a factual claim*.

---

#### Problem 4: Knowledge Goes Stale, But No System Notices

Wikipedia states that a particular politician holds a specific cabinet position. That politician resigned six months ago. Wikipedia has not been updated — or was updated, then vandalized back — or the editor who maintained that article left the platform.

This is not hypothetical. Studies have consistently found Wikipedia's overall accuracy at approximately **80%**, against ~95% for traditional encyclopedias. The gap is largest for fast-moving, time-sensitive information: current events, living persons, and evolving scientific consensus.

More critically: **AI systems train on and query Wikipedia as ground truth.** When a Wikipedia article contains an error or stale information, every AI assistant that uses it as a knowledge source inherits that error — and presents it with full confidence.

The same problem affects Wikidata (the structured knowledge graph behind many AI products), Google's Knowledge Graph, and virtually every static knowledge base: they are **point-in-time snapshots**, not temporal systems. They do not model the *history* of a fact or automatically deprecate information as new evidence emerges.

A fact about the world has a **lifecycle**:
```
ASSERTED → CORROBORATED → ACTIVE → [DISPUTED | SUPERSEDED | RETRACTED]
```

No mainstream knowledge system models this lifecycle. They treat facts as binary: true or not in the database.

> **The core problem:** Truth is not static. Facts evolve, get updated, get contradicted, and get retracted. No existing knowledge system models this temporal reality automatically and at scale.

---

#### Problem 5: Credibility Is Invisible and Non-Quantified

When you read a news article, you are exposed to a single fact: the claim. You are rarely exposed to:

- How many other independent sources corroborate it
- The historical reliability track record of the publishing outlet
- Whether the claim has been contradicted by higher-trust sources
- How old the underlying evidence is
- Whether any supporting visual media tested as synthetic

A front-page New York Times story and a WordPress blog post with a misleading headline appear in the same Google search results. A viral tweet based on a misread statistic and a peer-reviewed study summary sit side-by-side on a social feed. **There is no credibility signal attached to the unit of information itself** — only to the container it arrives in, and only if the reader already knows to look.

> **The core problem:** Credibility is contextual knowledge that 99% of readers do not have at the moment they encounter a claim. It needs to be computed, stored, and surfaced alongside the claim itself.

---

### 1.3 — Concrete Real-World Scenarios Where the System Provides Value

#### Scenario A: The Journalist Trying to Verify Fast-Breaking News

*It is 11:47 PM. A journalist's phone lights up — a source claims a major tech company's CEO has just quietly resigned. Twitter is buzzing. Three unverified blogs are running it. The journalist has to decide in minutes whether to publish.*

**Without NNI Truth Graph:** The journalist manually searches for corroborating sources, calls contacts, tries to trace the original claim. By the time verification completes, competitors have published (incorrectly or correctly) and the window has closed.

**With NNI Truth Graph:** The journalist queries the system. The graph shows:
- The claim first appeared 38 minutes ago on a blog with a historical reliability score of 0.41 (Tier 3)
- Two Tier 1 sources (Reuters, Associated Press) have no corroborating claims
- One CONTRADICTS edge exists — the company issued a statement 12 minutes ago (already ingested)
- Epistemic Score: **0.22 → AUTO_REJECT**

The journalist does not publish. The story was false.

---

#### Scenario B: The Voter Encountering a Political Claim

*A voter sees a video on Facebook claiming a local candidate voted against a school funding bill. The video has 40,000 shares. The voter is uncertain whether to believe it before tomorrow's election.*

**Without NNI Truth Graph:** No fact-check exists yet. The voter must either accept the claim, reject it on instinct, or spend time manually searching — which produces conflicting and time-consuming results.

**With NNI Truth Graph:** The voter searches the candidate's name. The graph shows:
- The voting record claim is ACTIVE, sourced from the official state legislature voting record (Tier 1 source, trust score 0.94)
- The claim that it was a *school funding* bill is DISPUTED — the bill's actual classification is contested across 3 sources, creating an open Controversy node
- Epistemic Score for the core vote: **0.87 → AUTO_APPROVE** (the vote happened)
- Epistemic Score for the framing: **0.41 → HUMAN_REVIEW** (the characterization is contested)

The voter gets a nuanced, accurate picture in seconds.

---

#### Scenario C: The Researcher Studying an Evolving Scientific Claim

*A public health researcher needs to know the current scientific consensus on a specific drug interaction. Papers from 2019 said X. A 2022 meta-analysis contradicted X. A 2024 clinical trial has now been published.*

**Without NNI Truth Graph:** The researcher must manually search databases, read papers, and synthesize the evolution of the consensus — a process that takes hours or days.

**With NNI Truth Graph:** The graph shows a full Timeline of the claim — three Claim nodes connected by SUPERSEDES and CONTRADICTS edges, each with epistemic scores reflecting the tier and citation count of each study. The most recent claim carries the highest score. The full provenance chain is one query away.

---

#### Scenario D: The Platform Needing Automated Content Moderation

*A social media platform wants to automatically flag posts that contain factual claims with low epistemic credibility before they go viral — without relying on human moderators and without censoring opinion.*

**Without NNI Truth Graph:** The platform either deploys blunt keyword filters (too restrictive, misses context) or relies on user reports (too slow, gameable) or outsources to human moderators (not scalable).

**With NNI Truth Graph:** The platform integrates the public API. Before a post reaches algorithmic amplification, it is cross-referenced against the graph. Posts containing claims with Epistemic Score < 0.40 receive a soft warning label and reduced distribution. Posts containing claims with CONTRADICTS edges to Tier 1 sources are flagged for human review. No content is deleted — only contextualized.

---

### 1.4 — How NNI Truth Graph Fights Misinformation

Misinformation is not defeated by contradiction — it is defeated by speed, scale, and structural integration. The system is specifically designed with these three properties at its core.

#### Speed: Operating at the Velocity of News Itself

The fundamental asymmetry of the modern information ecosystem is temporal: a false claim can reach a million people in 11 minutes (the documented average for viral misinformation on Twitter/X). A human fact-check takes 4–48 hours and reaches, on average, a fraction of the original audience.

NNI Truth Graph closes this gap from the source side. The pipeline runs **continuously 24/7**, ingesting new articles within minutes of publication via RSS feeds and dynamic SearXNG topic hunting. By the time a false claim has been shared 1,000 times, the system has often already:
- Scraped the original source article
- Extracted the factual claim as an SPO triple
- Cross-referenced it against all existing graph knowledge
- Computed a preliminary Epistemic Score
- Detected whether it is contradicted by Tier 1 sources already in the graph
- Committed a `CONTRADICTS` or `LOW_SCORE` result to the graph

Applications querying the API at that point receive a credibility signal *before* the claim goes viral — not after.

#### Scale: Autonomous, No Human Bottleneck

The system processes every article it discovers. There is no editorial team, no fact-checker roster, no per-claim approval required for most decisions. `AUTO_APPROVE` (score ≥ 0.85) and `AUTO_REJECT` (score < 0.40) routing means the overwhelming majority of claims are processed without human involvement. Only genuinely ambiguous claims (score 0.40–0.85) route to `HUMAN_REVIEW`. This means the effective throughput scales with compute, not with headcount.

Traditional fact-checking organizations — even the best-funded ones — fact-check hundreds of claims per month. The NNI Truth Graph pipeline is architected to process **thousands of claims per day**, limited only by LLM API rate limits (addressed by the rotating multi-key Groq pool in `llm_router.py`).

#### Structural Integration: Fighting Misinformation at the Graph Level

The most important design decision is that misinformation is not fought by deleting or suppressing claims — it is fought by making the *relationship between claims* visible and machine-readable. When a false claim is ingested, it doesn't get deleted: it gets a low Epistemic Score, a `CONTRADICTS` edge to the higher-trust claims that refute it, and a `DISPUTED` lifecycle state. The controversy itself is preserved as a `Controversy` node in Neo4j.

This matters because **suppression breeds distrust**. A system that scores and contextualizes is more defensible, more transparent, and harder to game than one that deletes. Bad actors can coordinate to suppress a claim. They cannot coordinate to manufacture 50 independent Tier 1 corroborations to raise its Epistemic Score — the bot swarm detection in the γ algorithm specifically prevents this.

---

### 1.5 — How the System Verifies Facts

Verification in NNI Truth Graph is not binary. It is not "true" or "false." It is a **continuous, multi-dimensional probability** expressed as the Epistemic Trust Score, updated dynamically as evidence accumulates. Here is the full verification chain for any given claim:

**Step 1 — Source Pre-qualification**
Before a single word is read, the source is evaluated. Does it have a historical `epistemic_trust_score` in the `sources` table? What tier is it? A blog post and a peer-reviewed journal both flow through the same pipeline, but their β signal differs by 0.50 before any claim is even extracted. This does not exclude low-tier sources — social media can break real news — but it correctly weights them lower until corroborated.

**Step 2 — LLM Extraction with Structured Constraints**
Claims are not extracted as free text. They are extracted as **strictly typed SPO triples** with mandatory temporal/spatial anchors, verified provability requirements (subjective opinion is explicitly excluded), and NOMA domain classification. The 70B LLM cannot hallucinate a claim into existence — it must derive the claim from the article text and report its own confidence. The Pydantic schema validation rejects malformed extractions before they reach the database.

**Step 3 — Cross-Modal Visual Verification**
For every article with an image, the system computes whether the image actually supports the text. SigLIP embeddings bring the visual and textual content into the same latent space. A headline claiming a disaster happened, paired with an image showing ordinary street scenes, will produce a very low cosine similarity — penalizing the extraction confidence by 60%. This is automated clickbait detection at the semantic level.

**Step 4 — Provenance Archaeology**
Stage 5 does not just ask "is this true?" — it asks "where did this fact come from, and when?" The Wayback Machine and Common Crawl CDX APIs are queried to determine the *earliest internet-archived occurrence* of the claim. If the article claiming to break news was actually published 6 hours after the claim first appeared elsewhere on the internet, the article is not the original source — and the system traces back to find what is. Source credibility is not just about the outlet: it is about whether the outlet is reporting original information or recycling someone else's reporting.

**Step 5 — Network Consensus Measurement**
The γ algorithm measures how many independent sources — with diversity across tiers and time windows — have reported the same fact. A claim corroborated by 3 Tier 1 sources across 48 hours is substantively different from one "corroborated" by 50 Tier 3 blogs in the same 4-hour window. The bot swarm detection penalizes the latter while rewarding the former with the maximum γ bonus.

**Step 6 — Temporal Revalidation**
Verification is not a one-time event. Every 30 days, old claims are re-searched against the live internet. If new evidence emerges — a correction, a retraction, a superseding study — that evidence re-enters Stage 1 and flows through the full pipeline. The `SUPERSEDES` and `CONTRADICTS` edges in Neo4j record the complete epistemic history. A claim that was true in 2023 and false in 2025 carries both states, timestamped, in the graph.

---

### 1.6 — How the System Builds Trusted Sources

Source trust in NNI Truth Graph is not a fixed label assigned at setup time. It is a **dynamically maintained float** (`epistemic_trust_score` in the `sources` table) that evolves based on the measured accuracy of the source's claims over time.

#### Trust is Earned Through Claim Outcomes

Every time a source's claim is:
- **Corroborated by independent Tier 1 sources**: the source's track record improves
- **Contradicted by higher-trust sources and later confirmed as false**: the source's score is decremented (`-0.05` per Stage 9 resolution)
- **Superseded by more accurate, later reporting**: a smaller adjustment reflecting that the source was partially right but incomplete

The trust algorithm is self-calibrating. A source that consistently publishes accurate claims — measured by subsequent independent corroboration, not by editorial opinion — naturally rises toward Tier 1. A source that consistently publishes claims that are refuted rises toward disqualification without any human intervention.

#### The Source Trust Leaderboard

The `/api/sources` endpoint and the `GET /api/v1/b2b/sources` B2B API endpoint expose the live source trust leaderboard — every source in the system ordered by epistemic_trust_score. This is a **live, evidence-based credibility ranking** of news outlets and information sources, derived entirely from the measured accuracy of their claims over time. No editorial bias, no blacklists, no whitelists — purely outcome-driven.

#### New Source Onboarding

When Stage 5 (provenance resolution) discovers a URL from a source not yet in the `sources` table, it is automatically added as a new source starting with the appropriate tier default (0.50 for revalidation-discovered sources, tier-appropriate defaults for others). The source participates in the trust system from the moment its first claim enters the pipeline. Over time, its track record is built automatically.

#### The Compounding Trust Network Effect

As the graph grows, source trust calculations become more accurate because there are more claim outcomes to measure against. A new source with 10 claims in the system has a less reliable trust score than an established source with 10,000 claims. This is by design — the system is explicitly more conservative with smaller corroboration datasets, which is mathematically reflected in the log-scaled γ corroboration bonus.

---

### 1.7 — How It Beats Existing Systems: A Precise Competitive Analysis

The gap between NNI Truth Graph and existing approaches is not marginal — it is architectural.

| Dimension | Traditional Fact-Checkers | LLMs / RAG | Wikidata / Wikipedia | **NNI Truth Graph** |
|---|---|---|---|---|
| **Speed** | Hours to days | Instant (but unreliable) | Days to weeks for updates | Minutes from publication |
| **Scale** | Hundreds of claims/month | Unlimited (but unverified) | Volunteer-dependent | Thousands of claims/day |
| **Provenance** | Manual citation | None (hallucinated) | Editor-attribution only | Full archive-verified chain |
| **Temporal model** | Static verdict | Knowledge cutoff | Point-in-time snapshot | Continuous lifecycle: ACTIVE → SUPERSEDED → STALE |
| **Visual evidence** | Ad hoc | None | None | Integrated dual-ViT deepfake scoring |
| **Source trust** | Editorial reputation | Training data bias | Community consensus | Evidence-based dynamic scoring |
| **Structured data** | Prose verdicts | Unstructured text | Wikidata triples (static) | Live SPO graph with epistemic scores |
| **API access** | None / limited | OpenAI/Anthropic API (unverified) | SPARQL (static) | REST + WebSocket Firehose (live) |
| **Hallucination risk** | Human error | High | Vandalism/staleness | Structurally constrained extraction |
| **Deepfake integration** | None | None | None | ε override in every trust score |

**The critical distinction** is not any single feature — it is the integration. A standalone deepfake detector tells you the image is synthetic. A standalone fact-checker tells you the claim is disputed. A standalone knowledge graph tells you what the claim says. 

NNI Truth Graph is the only system that answers: *"Given this specific factual claim, what is its source history, how many independent sources corroborate it, what does the supporting visual evidence score on the deepfake detector, how old is the evidence, what claims contradict it, what is its current lifecycle state, and what is the single mathematical confidence score I should attach to this claim right now?"*

---

### 1.8 — What New Solution Is Being Introduced

NNI Truth Graph introduces a category that does not currently exist in the market: a **Living Epistemic Graph** — a structured, queryable, continuously-maintained knowledge base where every fact carries a machine-computable trust score and a full evidence lifecycle.

This is distinct from:
- A *fact-checker* (which produces verdicts, not scores, and only for checked claims)
- A *knowledge graph* (which stores facts, but not their credibility or temporal evolution)
- A *search engine* (which retrieves documents, not verified structured facts)
- An *LLM* (which generates plausible text, not sourced, scored claims)

The new thing being introduced is the **Epistemic Trust Score as a first-class data primitive** — a continuously recalculated, evidence-weighted float that travels with every fact as a permanent property of that fact in the graph. Not a recommendation, not a label, not an editorial verdict: a piece of metadata as fundamental as a timestamp.

This primitive enables things that were previously impossible:

- **Credibility filtering at query time**: `SELECT claims WHERE epistemic_score >= 0.75` — an operation with no equivalent in any existing knowledge system
- **Contradiction auditing**: `MATCH (a:Claim)-[:CONTRADICTS]-(b:Claim) RETURN a, b, a.epistemic_score, b.epistemic_score` — instantly revealing which competing claims the graph considers more credible and why
- **Source outcome tracking**: measuring a source's historical accuracy not by reputation, but by the measured fate of its claims over time
- **Temporal truth queries**: "What was the scientific consensus on claim X on date Y?" — answerable by querying claims with `valid_from` and `valid_until` that encompass Y
- **Cross-modal truth validation**: verifying whether the visual evidence accompanying a claim is semantically consistent with the claim's text — automated at scale

---

### 1.9 — What This Makes Possible

The Epistemic Trust Score as a queryable, real-time, machine-readable primitive unlocks an entirely new category of downstream applications:

#### For News Organizations
- A **pre-publication fact-check API**: before filing a story, a reporter queries whether the claims they intend to publish already have CONTRADICTS edges in the graph. Errors caught before publication, not after.
- A **source vetting tool**: query an outlet's epistemic_trust_score and claim outcome history before citing them in reporting.

#### For Social Platforms
- **Claim-level contextualization at distribution time**: instead of content moderation (which deletes), attach an epistemic score label to posts containing low-trust factual claims *at the moment of distribution*, before algorithmic amplification. No censorship — only context.
- **Trending claim monitoring**: the Firehose WebSocket streams every newly committed claim in real time, filtered by entity subscription. A platform can detect when a specific entity is suddenly generating a surge of low-trust claims — an early warning signal for coordinated misinformation campaigns.

#### For Democracy and Elections
- **Voter verification tools**: a browser extension or widget that cross-references political claims in real time against the graph while users read news or social media. Epistemic scores surface alongside claims without requiring any platform cooperation.
- **Candidate claim tracking**: every public statement by a political candidate is a candidate for ingestion, extraction, and scoring against the historical record. "This candidate's claims have an average epistemic score of 0.62, with 14 CONTRADICTED claims in the last 90 days."

#### For the AI Ecosystem
- **Ground-truth training data**: the `GET /api/v1/b2b/datasets/daily-snapshot` endpoint provides structured, scored, sourced factual claims — exactly the format needed for RLHF grounding, fine-tuning, and reducing LLM hallucination. Today's LLMs hallucinate because they train on unverified internet text. Tomorrow's LLMs could train on epistemically-scored structured knowledge.
- **LLM grounding backend**: instead of RAG (which retrieves unverified documents), retrieve verified, scored, structured SPO claims. The claim "Elon Musk ACQUIRED Twitter (score: 0.94, corroborated by 47 Tier 1 sources)" is a fundamentally better grounding unit than a raw Wikipedia paragraph.
- **Agentic verification layer**: AI agents making decisions based on factual claims can query the graph to validate those claims before acting, with quantified confidence. "I will only execute this action if the claim's epistemic score is above 0.80."

#### For Research and Academia
- **Real-time consensus tracking**: the graph continuously models the current state of consensus across empirical, philosophical, theological, and lexical domains — queryable at any point in time.
- **Claim evolution archaeology**: the full `SUPERSEDES` chain for any claim reveals how scientific consensus, political positions, or legal interpretations evolved over time — machine-readable historiography.

#### The Largest Long-Term Possibility: Infrastructure for a Trusted Internet

The deepest implication of NNI Truth Graph is infrastructural. The internet was built without a credibility layer. Every link looks the same whether it leads to peer-reviewed science or coordinated propaganda. The DNS system tells you where a resource lives; HTTPS tells you it was transmitted securely; neither tells you whether the content is true.

NNI Truth Graph is a prototype of the **missing credibility layer** — an open, queryable system that attaches machine-readable epistemic metadata to factual claims discovered anywhere on the internet. If this infrastructure were to become as universally accessible as DNS or search, the foundational economics of misinformation would change: the asymmetry between the cost of creating false information (near zero) and the cost of flagging it (currently measured in human labor hours) would collapse. False claims would be identified not in hours, but in minutes, automatically, at the moment they enter the internet's information ecosystem.

---

### 1.10 — Summary Statement

The world is drowning in information. The crisis is not a shortage of facts — it is the structural inability to know **which facts to trust, where they came from, who verified them, and whether they are still true today.**

Every existing tool approaches this problem from one angle: fact-checkers check claims manually; search engines surface documents; knowledge graphs store facts; LLMs generate text; deepfake detectors score images. None of them compose these signals into a single, persistent, mathematically-grounded answer.

NNI Truth Graph is the **infrastructure layer that makes trust computable.**

It does not tell people what to think. It does not suppress content. It does not require human editors at scale. It shows — automatically, continuously, at internet speed — **how much the current body of evidence supports each specific factual claim, why, and what the graph of corroborating and contradicting evidence looks like.**

The Epistemic Trust Score is not an opinion. It is a measurement. And for the first time in the history of the public internet, that measurement is attached to the fact itself, travels with it as a first-class data property, updates when new evidence arrives, and is queryable by any application in real time.

---

*// Section 2: System Architecture — coming next.*
