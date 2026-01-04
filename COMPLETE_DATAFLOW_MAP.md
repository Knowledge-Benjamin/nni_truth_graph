# Complete DataFlow Map - NNI Truth Graph

**This document traces the ENTIRE data flow: from ingestion through storage to query response.**

---

## Part 1: INGESTION FLOW (Data → PostgreSQL)

### Stage 1A: RSS Feed Ingestion
**File**: `scripts/ingest_rss.py`

```
Trusted Sources (data/trusted_sources.json)
         ↓
Feedparser parse() → Entries
         ↓
INSERT INTO articles (url, title, publisher, raw_text, ingestion_source='RSS_TRUSTED', published_date)
         ↓
INSERT INTO processing_queue (article_id, status='PENDING')
         ↓
PostgreSQL: articles table + processing_queue
```

**Key Columns**: `id`, `url`, `title`, `publisher`, `ingestion_source`, `published_date`

---

### Stage 1B: GDELT Ingestion
**File**: `scripts/ingest_gdelt.py`

```
HTTP fetch: http://data.gdeltproject.org/gdeltv2/lastupdate.txt
         ↓
Download & unzip GDELT Events CSV
         ↓
Parse Tab-Separated Values
         Filter: mentions >= 10
         Extract: URL (last column), mentions count (index 31)
         ↓
INSERT INTO articles (url, publisher='', ingestion_source='GDELT')
         ↓
INSERT INTO processing_queue (article_id, status='PENDING')
         ↓
PostgreSQL: articles table + processing_queue
```

**Quality Gate**: `num_mentions >= 10` (to filter low-impact events)

---

### Stage 2: Scraping (Hydration)
**File**: `scripts/scrape_content_pro.py`

```
processing_queue (status='PENDING')
         ↓
SELECT articles WHERE processed_at IS NULL AND raw_text IS NULL (LIMIT 50)
         ↓
Playwright async scrape (BATCH_SIZE=5 concurrent tabs)
         ↓
Trafilatura: Download → Extract plain text
         ↓
UPDATE articles SET raw_text = <text>, processed_at NOW()
         ↓
UPDATE processing_queue SET status='SCRAPED'
         ↓
PostgreSQL: articles.raw_text populated + queue status updated
```

**Important**: Server-side stored articles.raw_text, but digest_articles prefers fresh Trafilatura fetches (not stale DB)

---

### Stage 3: Fact Extraction & Embedding Generation
**File**: `scripts/digest_articles.py` (Groq + SemanticLinker)

```
articles (WHERE processed_at IS NULL)
         ↓
A. Fetch fresh content:
   - Trafilatura fetch_url() + extract()
   - Update published_date if found
   ↓
B. Extract facts using Groq (Llama 3.3-70b):
   - Prompt: Extract atomic facts (subject, predicate, object)
   - Response: {"facts": [{subject, predicate, object, confidence}]}
   ↓
C. Generate embeddings FOR EACH FACT:
   - Statement = "{subject} {predicate} {object}"
   - embedding = SemanticLinker.get_embedding(statement)  ← CLOUD MODE: Uses HuggingFace API
   - Convert to pgvector: '[val1,val2,...,val384]'
   ↓
D. Deduplication gate (Vector distance check):
   - SELECT * FROM extracted_facts WHERE embedding <=> $vector < 0.05
   - If similar fact exists: SKIP (don't insert)
   - If new unique fact: INSERT
   ↓
INSERT INTO extracted_facts (article_id, subject, predicate, object, confidence, embedding)
  VALUES (?, ?, ?, ?, ?, $embedding::vector)
         ↓
UPDATE articles SET processed_at = NOW()
         ↓
PostgreSQL: extracted_facts table + embeddings
```

**Critical Detail**: 
- `embedding` stored as **pgvector** type in PostgreSQL
- Used later for deduplication & provenance hunting
- Dimension: 384-dimensional vector

---

### Stage 4: Provenance Hunting (Verification)
**File**: `scripts/hunt_provenance.py`

```
extracted_facts (WHERE checked_at IS NULL AND embedding IS NOT NULL)
         ↓
For each unchecked fact:
  - Get fact date from article
  - Vector search in PostgreSQL: embedding <=> $vector < 0.15 (85% similarity)
  - Find semantically similar facts
  ↓
External search (Serper API):
  - Search web for: "{subject} {predicate} {object}"
  - Check dates: External date >= fact date
  ↓
If found externally:
  - is_original = FALSE
  - Mark as provenance checked
  - Insert external source as "Reference Article" (is_reference=TRUE)
  ↓
IF found internally (similar fact already exists):
  - provenance_id = similar_fact_id
  - is_original = FALSE
  ↓
UPDATE extracted_facts SET checked_at=NOW(), is_original=?, provenance_id=?
         ↓
PostgreSQL: extracted_facts verification complete
```

**Quality Gate**: `checked_at IS NOT NULL` (Provenance verified) → Required for sync to Neo4j

---

## Part 2: STORAGE FLOW (PostgreSQL → Neo4j)

### Stage 5: Sync to Neo4j (Graph Publication)
**File**: `scripts/sync_truth_graph.py` → `scripts/push_to_neo4j.js`

#### 5A: Python: Fetch Verified Facts
```
sync_truth_graph.py:
  ↓
1. SELECT * FROM extracted_facts WHERE is_original=TRUE AND checked_at IS NOT NULL
   → Fetch: id, subject, predicate, object, confidence, embedding
   ↓
2. SELECT * FROM articles WHERE processed_at IS NOT NULL AND id IN (article_topics)
   OR is_reference = TRUE
   → Fetch: id, title, url, published_date, is_reference
   ↓
3. SELECT * FROM extracted_facts
   JOIN articles WHERE article_id IS NOT NULL
   AND (is_original=TRUE OR provenance_id IS NOT NULL)
   → Fetch: fact_id, article_id, provenance_id, is_original
   ↓
Quality Gates:
  - Facts: Only ORIGINAL + VERIFIED (checked_at IS NOT NULL)
  - Articles: Only CLASSIFIED (in article_topics) OR REFERENCES
  ↓
Payload = {facts: [...], articles: [...], assertions: [...]}
         ↓
JSON serialize → temp_graph_data.json
```

**Quality Gates Enforced**:
1. Facts must be: `is_original=TRUE` AND `checked_at IS NOT NULL`
2. Articles must be: `processed_at IS NOT NULL` AND in `article_topics` OR `is_reference=TRUE`
3. Result: Only HIGH-QUALITY facts sync to Neo4j

#### 5B: Node.js: Write to Neo4j
```
push_to_neo4j.js:
  ↓
Read temp_graph_data.json
  → facts array with EMBEDDINGS included
  → articles array
  → assertions array
  ↓
Neo4j driver: driver(NEO4J_URI, auth.basic())
  ↓
1. Apply constraints:
   MERGE (f:Fact {id: unique_id})
   MERGE (a:Article {id: unique_id})
   ↓
2. Merge Articles:
   FOR EACH article IN articles:
     MERGE (a:Article {id: a.id})
     SET a.title, a.url, a.date, a.is_reference
   ↓
3. Merge Facts (WITH EMBEDDINGS):
   FOR EACH fact IN facts:
     MERGE (f:Fact {id: f.id})
     SET f.text,
         f.subject,
         f.predicate,
         f.object,
         f.confidence,
         f.embedding = $embedding  ← 384-dim vector stored here!
   ↓
4. Create Article-Fact Relationships:
   FOR EACH assertion IN assertions:
     MATCH (a:Article {id: assertion.article_id})
     MATCH (f:Fact {id: assertion.fact_id})
     MERGE (a)-[:ASSERTED]->(f)
   ↓
Neo4j Graph:
  Articles -[:ASSERTED]-> Facts
  Each Fact has: subject, predicate, object, confidence, embedding (384-dim)
```

**CRITICAL**: 
- Embeddings ARE stored in Neo4j as `f.embedding` property
- Used later for vector similarity queries
- Format: 384-element array

---

## Part 3: QUERY FLOW (Client → Neo4j → Client)

### Stage 6A: Client Query
**File**: `client/src/components/SearchBar.jsx`

```
User enters query in SearchBar
         ↓
onClick: POST /api/query/natural
  Body: { query: "Turkey detains 357 suspected IS members..." }
         ↓
server/index.js receives at /api/query/natural endpoint
```

---

### Stage 6B: Server - Parallel AI Engine Calls
**File**: `server/index.js` (lines 304-310)

```
Parallel Promise.allSettled([
  POST http://AI_ENGINE/expand_query { query },
  POST http://AI_ENGINE/embed_query { query }
])
         ↓
┌──────────────────────────────────────────┐
│ AI Engine - /expand_query endpoint       │
├──────────────────────────────────────────┤
│ ai_engine/main.py:265-273                │
│   query_translator.expand_query(query)   │
│   → Uses Gemini 2.5 Flash                │
│   → Returns variations array             │
│   Response: {                            │
│     variations: [                        │
│       "query1 (synonym)",                │
│       "query2 (related)",                │
│       "query3 (specific)"                │
│     ]                                    │
│   }                                      │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│ AI Engine - /embed_query endpoint        │
├──────────────────────────────────────────┤
│ ai_engine/main.py:274-287                │
│   semantic_linker.get_embedding(query)   │
│   → CLOUD MODE:                          │
│      SemanticLinker init:                │
│        - Reads EXECUTION_MODE='cloud'    │
│        - Forces use_api=True             │
│        - Initializes HuggingFace client  │
│      get_embedding():                    │
│        - Calls HF API: feature_extraction│
│        - Model: sentence-transformers/   │
│          all-MiniLM-L6-v2               │
│        - Returns 384-dim vector          │
│   Response: {                            │
│     embedding: [0.123, 0.456, ...]      │
│   }                                      │
└──────────────────────────────────────────┘
```

**Execution Mode Behavior** (from ai_engine/nlp_models.py):
- `EXECUTION_MODE=cloud` → Forces SemanticLinker to use HuggingFace API (no local models)
- `EXECUTION_MODE=local` → Loads local sentence-transformers model
- Cloud mode: NO model downloads, pure API calls

---

### Stage 6C: Server - Build Search Query
**File**: `server/index.js` (lines 311-341)

```
Results from parallel calls:
  expansionResp: { variations: [...] }
  embeddingResp: { embedding: [0.123, 0.456, ...] }
  ↓
Process expansion:
  searchTerms = [original_query, ...variations]
  Filter: remove empty strings
  Log: "Expansion: query1, query2, query3"
  ↓
Process embedding:
  IF embeddingResp.status === "fulfilled" AND embedding exists:
    vector = embeddingResp.value.data.embedding  ← 384-dim array
    Log: "Embedding generated (384-dim)"
  ELSE:
    vector = null
    Log: "Vector search unavailable (using keyword only)"
```

---

### Stage 6D: Choose Query Strategy
**File**: `server/index.js` (lines 352-380)

```
IF vector && vector.length === 384:
  ↓ Hybrid Search (Keyword + Vector)
  ┌─────────────────────────────────────┐
  │ buildHybridSearchQuery() from        │
  │ server/cypher-builder.js             │
  │                                      │
  │ Cypher Query:                        │
  │ 1. Keyword matching:                │
  │    MATCH (f1:Fact)                  │
  │    WHERE ANY(kw IN $keywords        │
  │      toLower(f1.text) CONTAINS kw   │
  │      OR ...subject/predicate/object │
  │    )                                │
  │    ↓                                │
  │ 2. Vector similarity (NATIVE):      │
  │    reduce(sum=0, i in range(0,384)  │
  │      sum + f2.embedding[i]*$[i]     │
  │    ) AS dotProduct                  │
  │    sqrt(reduce(sum=0, ...)) AS mag  │
  │    cosine = dotProduct/(mag1*mag2)  │
  │    ↓                                │
  │ 3. Combine scores (50/50):          │
  │    hybridScore = 0.5*keywordScore   │
  │      + 0.5*cosineSimilarity         │
  │    ↓                                │
  │ 4. TruthRank boost:                 │
  │    IF confidence > 0.8: *1.2        │
  │    IF confidence > 0.9: *1.5        │
  │                                      │
  │ RETURN f as fact, finalScore as rel │
  └─────────────────────────────────────┘
         ↓
ELSE IF vector (but length != 384):
  ↓ Vector-Only Search
  ┌─────────────────────────────────────┐
  │ buildVectorSearchQuery()             │
  │ WHERE cosine_similarity >= 0.65     │
  │ ORDER BY cosine_similarity DESC     │
  └─────────────────────────────────────┘
         ↓
ELSE (no vector):
  ↓ Keyword-Only Search
  ┌─────────────────────────────────────┐
  │ buildSearchFactsQuery()              │
  │ WHERE ANY(kw IN $keywords)          │
  │ Split query into individual keywords│
  │ No vector calculations              │
  └─────────────────────────────────────┘
```

---

### Stage 6E: Execute Cypher Query
**File**: `server/index.js` (lines 380-400)

```
session.run(querySpec.query, querySpec.params)
         ↓
Neo4j receives Cypher query with parameters:
  - $keywords (or $fulltextQuery)
  - $embedding (384-dim array, if hybrid)
  - $similarityThreshold
  - $limit (default 15)
  ↓
Cypher executes:
  1. MATCH Facts
  2. Calculate similarities (native Cypher REDUCE)
  3. Sort by score
  4. Return f as fact, score as relevance
  ↓
Neo4j returns records:
  Array of Fact nodes matching query
  Each has: id, text, subject, predicate, object, confidence, relevance
```

---

### Stage 6F: Format Response
**File**: `server/index.js` (lines 380-410)

```
result.records
  ↓
Map each record:
  r.fact → Fact node properties
  r.relevance → Similarity/confidence score
  ↓
Format for client:
  {
    success: true,
    query: "original_query",
    analysis: null (or AI analysis if available),
    results: [
      {
        id: fact_id,
        statement: "subject predicate object",
        subject: "...",
        predicate: "...",
        object: "...",
        confidence: 0.95,
        relevance: 0.87  ← Similarity score
      },
      ...
    ],
    count: 5,
    timestamp: "2026-01-04T..."
  }
  ↓
res.json(response)
```

---

### Stage 6G: Client Receives Response
**File**: `client/src/App.jsx` (lines 18-35)

```
Response from POST /api/query/natural
  ↓
handleSearch(data):
  data.analysis → setAnalysis()
  data.results → Map to Fact format
  ↓
For each result:
  fact = r.f || r.fact || r
  statement = fact.statement || built from subject/predicate/object
  ↓
Create Fact objects:
  {
    id, statement, subject, predicate, object,
    confidence, relevance, source_url, source_title
  }
  ↓
setSearchResults(mappedResults)
  ↓
Render in FactCard components
  - Show statement
  - Show confidence %
  - Show relevance score
  - Link to source articles
```

---

## COMPLETE SYSTEM FLOW DIAGRAM

```
┌─────────────────────────────────────────────────────────────────┐
│ INGESTION PIPELINE                                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  RSS Feeds / GDELT Events                                       │
│    ↓                                                            │
│  ingest_rss.py / ingest_gdelt.py                               │
│    ↓  (INSERT articles + processing_queue)                      │
│  PostgreSQL: articles table                                     │
│    ↓                                                            │
│  scrape_content_pro.py                                          │
│    ↓  (UPDATE articles.raw_text)                                │
│  PostgreSQL: articles.raw_text populated                        │
│    ↓                                                            │
│  digest_articles.py                                             │
│    ├─ Trafilatura: Fetch fresh content                         │
│    ├─ Groq (Llama 3.3): Extract facts {subject, predicate...} │
│    ├─ SemanticLinker (HF API, Cloud Mode): Generate embedding  │
│    └─ Deduplication: Vector search (embedding <=> 0.05)        │
│    ↓  (INSERT extracted_facts + embedding)                     │
│  PostgreSQL: extracted_facts + embedding (pgvector)            │
│    ↓                                                            │
│  hunt_provenance.py                                             │
│    ├─ Vector search for similar facts                          │
│    ├─ Serper API: External verification                        │
│    └─ Set: is_original, checked_at, provenance_id            │
│    ↓  (UPDATE extracted_facts.checked_at, etc)                 │
│  PostgreSQL: Facts verified + checked_at=NOW()                │
│    ↓                                                            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ SYNC TO NEO4J                                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  sync_truth_graph.py                                            │
│    ├─ SELECT facts WHERE is_original=TRUE AND checked_at!=NULL │
│    ├─ SELECT articles WHERE (classified OR reference)           │
│    └─ Serialize to temp_graph_data.json (WITH EMBEDDINGS)     │
│    ↓                                                            │
│  push_to_neo4j.js                                               │
│    ├─ MERGE (a:Article) nodes                                  │
│    ├─ MERGE (f:Fact {id, text, subject, predicate, object,    │
│    │                   confidence, embedding})                  │
│    └─ MERGE (a)-[:ASSERTED]->(f) relationships                 │
│    ↓                                                            │
│  Neo4j Database                                                 │
│    Fact nodes with 384-dim embedding vectors                   │
│    Article nodes with metadata                                 │
│    Article -[:ASSERTED]-> Fact relationships                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ QUERY PIPELINE (CLIENT → RESPONSE)                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Frontend: SearchBar.jsx                                        │
│    User enters query → POST /api/query/natural {query}         │
│    ↓                                                            │
│  Server: /api/query/natural endpoint                           │
│    Parallel calls to AI Engine:                                 │
│    ├─ /expand_query → Gemini generates variations             │
│    └─ /embed_query → HF API generates 384-dim embedding       │
│    ↓                                                            │
│  Query Strategy Decision:                                       │
│    IF vector exists:                                            │
│      └─ HYBRID: buildHybridSearchQuery()                       │
│         (keyword matching + native Cypher cosine similarity)   │
│    ELSE:                                                        │
│      └─ KEYWORD: buildSearchFactsQuery()                       │
│    ↓                                                            │
│  Neo4j Executes Cypher:                                         │
│    • Native cosine calculation (REDUCE + sqrt)                │
│    • No GDS required                                           │
│    • Returns Facts sorted by relevance score                   │
│    ↓                                                            │
│  Response Formatting:                                           │
│    Map Neo4j records to {id, statement, subject, ...}         │
│    Include relevance scores & confidence                       │
│    ↓                                                            │
│  Frontend: Display Results                                      │
│    Render FactCards with statements & sources                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## KEY TECHNICAL DETAILS

### Vector Embedding Storage
- **Format**: pgvector in PostgreSQL, Neo4j array property
- **Dimension**: 384 (all-MiniLM-L6-v2 model)
- **Generation**: SemanticLinker.get_embedding() via HuggingFace API (cloud mode)
- **Storage Pipeline**: PostgreSQL → JSON temp file → Neo4j

### Query Execution (No GDS Required)
```cypher
// Native Cypher cosine similarity calculation
reduce(sum=0, i in range(0, size(f.embedding)) 
  | sum + f.embedding[i]*$embedding[i]
) AS dotProduct

sqrt(reduce(sum=0, i in range(0, size(f.embedding))
  | sum + f.embedding[i]*f.embedding[i]
)) AS magnitude

CASE 
  WHEN magnitude = 0 THEN 0
  ELSE dotProduct / (magnitude * queryMagnitude)
END AS cosine_similarity
```

### Quality Gates (3-Stage Filtering)
1. **Ingestion**: `num_mentions >= 10` (GDELT)
2. **Verification**: `is_original=TRUE AND checked_at IS NOT NULL`
3. **Publication**: Articles must be `processed_at IS NOT NULL` AND classified OR reference

### Execution Mode (Cloud vs Local)
- **EXECUTION_MODE=cloud**: No local ML models, pure API calls (HF)
- **EXECUTION_MODE=local**: Downloads 80MB local sentence-transformers
- Current: `EXECUTION_MODE=cloud` (no RAM overhead)

---

## VERIFICATION CHECKLIST

✅ **Ingestion**: RSS/GDELT → PostgreSQL articles  
✅ **Hydration**: Playwright scraping → articles.raw_text  
✅ **Extraction**: Groq LLM → extracted_facts  
✅ **Embedding**: SemanticLinker (HF API) → 384-dim vectors in PostgreSQL  
✅ **Deduplication**: Vector distance check → unique facts only  
✅ **Verification**: Provenance hunting → checked_at flag  
✅ **Sync**: Quality-gated facts → Neo4j with embeddings  
✅ **Query**: Native Cypher cosine → vector similarity scores  
✅ **Response**: Format and return to frontend  

---

**Last Updated**: January 4, 2026
**System**: NNI Truth Graph with Vector Search (Native Cypher, No GDS)
