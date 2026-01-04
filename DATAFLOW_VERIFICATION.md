# NNI Truth Graph - Complete Data Flow Documentation

## 1. INGESTION & STORAGE FLOW (Articles → PostgreSQL → Neo4j)

### Stage 1: Data Ingestion (RSS/GDELT → PostgreSQL)
**Scripts**: `ingest_rss.py`, `ingest_gdelt.py`
**Frequency**: Every 30 minutes
**Flow**:
```
RSS Feeds/GDELT URLs
  ↓
Parse feed items
  ↓
Create `articles` table entries (id, url, title, publisher, ingestion_source)
  ↓
INSERT INTO processing_queue (article_id, status='PENDING', attempts=0)
  ↓
PostgreSQL `articles` table populated
PostgreSQL `processing_queue` table populated
```
**Key Point**: Both scripts insert into `processing_queue` to mark articles for downstream processing

---

### Stage 2: Content Hydration (Scrape content)
**Script**: `scrape_content_pro.py`
**Frequency**: Every 30 minutes (after ingestion)
**Flow**:
```
SELECT FROM processing_queue WHERE status='PENDING'
  ↓
FOR EACH article:
  - Fetch fresh HTML using Trafilatura
  - Extract full text, metadata, dates
  ↓
UPDATE articles SET raw_text=$extracted_text, published_date=$date
UPDATE processing_queue SET status='SCRAPED'
```
**Key Point**: Updates `raw_text` column in `articles` table for fact extraction

---

### Stage 3: Classification & Metadata
**Scripts**: `classify_topics_api.py`, `add_trust_scoring.py`
**Frequency**: Every 30 min + hourly
**Flow**:
```
FOR EACH article with raw_text:
  ↓ classify_topics_api.py
  - Send text to topic classifier API
  - Determine article topic/category
  ↓
INSERT INTO article_topics (article_id, topic_id)
  ↓ add_trust_scoring.py
  - Calculate trust_score based on domain, publisher, reputation
  ↓
INSERT INTO article_trust (article_id, trust_score)
```
**Quality Gate**: `sync_truth_graph.py` only accepts articles WITH topics (Quality Gate 2)

---

### Stage 4: Fact Extraction & Vectorization
**Script**: `digest_articles.py`
**Frequency**: Every 5 minutes (runs frequently)
**Flow**:
```
SELECT articles WHERE processed_at IS NULL
  ↓
FOR EACH article:
  1. Extract full_text from raw_text (or fresh Trafilatura fetch)
  
  2. CALL Groq LLM (llama-3.3-70b-versatile):
     - Input: article text
     - Output: JSON with facts [{subject, predicate, object, confidence}]
  
  3. FOR EACH extracted fact:
     a) Generate statement = "{subject} {predicate} {object}"
     
     b) GET embedding via HuggingFace API:
        - nlp_models.SemanticLinker.get_embedding(statement)
        - Cloud mode: Uses HF_TOKEN via huggingface_hub.InferenceClient
        - Returns: 384-dimensional vector
     
     c) DEDUPLICATION GATE (Vector similarity):
        - Query PostgreSQL: WHERE embedding <=> $embedding::vector < 0.05
        - If match found: SKIP (duplicate)
        - If NOT found: INSERT
     
     d) INSERT INTO extracted_facts:
        (article_id, subject, predicate, object, confidence, embedding::vector)
  
  4. UPDATE articles SET processed_at=NOW()
```
**Output**: `extracted_facts` table with columns:
- `id`: Fact ID
- `article_id`: Source article
- `subject`, `predicate`, `object`: RDF triple
- `confidence`: LLM confidence score (0-1)
- `embedding`: 384-dim pgvector (stored in PostgreSQL)
- `created_at`: Timestamp

---

### Stage 5: Provenance Verification
**Script**: `hunt_provenance.py`
**Frequency**: Every 10 minutes
**Flow**:
```
SELECT extracted_facts WHERE provenance_status IS NULL
  ↓
FOR EACH fact:
  1. Check Google Fact Check API
     - Search for existing fact checks
     - If found: Mark as VERIFIED/DEBUNKED
  
  2. Search web citations:
     - Find external sources supporting/contradicting
     - Create Reference Articles (is_reference=TRUE)
  
  3. Set is_original:
     - TRUE if fact originated in this article
     - FALSE if fact is external reference
  
  4. UPDATE extracted_facts:
     SET is_original=$bool,
         checked_at=NOW(),
         provenance_url=$external_url
```
**Quality Gate**: `sync_truth_graph.py` only accepts facts WHERE:
- `is_original = TRUE`
- `checked_at IS NOT NULL` (verified)

---

### Stage 6: Graph Synchronization (PostgreSQL → Neo4j)
**Script**: `sync_truth_graph.py` + `push_to_neo4j.js`
**Frequency**: Every 1 hour
**Flow**:
```
Python Phase:
  1. Query PostgreSQL with Quality Gates:
     SELECT extracted_facts WHERE is_original=TRUE AND checked_at IS NOT NULL
     SELECT articles WHERE (classified AND processed_at IS NOT NULL) OR is_reference=TRUE
     SELECT fact-article relationships
  
  2. Include embeddings in payload:
     {
       "facts": [{id, subject, predicate, object, confidence, embedding}, ...],
       "articles": [{id, title, url, published_date, is_reference}, ...],
       "assertions": [{id, article_id, provenance_id, is_original}, ...]
     }
  
  3. Write to temp_graph_data.json
  
  4. LAUNCH Node.js bridge

Node.js Phase (push_to_neo4j.js):
  1. Read temp_graph_data.json
  
  2. Connect to Neo4j (neo4j+s:// with SSL)
  
  3. FOR EACH fact:
     MERGE (f:Fact {id: $id})
     SET f.text = statement,
         f.subject = $subject,
         f.predicate = $predicate,
         f.object = $object,
         f.confidence = $confidence,
         f.embedding = $embedding  ← 384-DIM VECTOR STORED HERE
  
  4. FOR EACH article:
     MERGE (a:Article {id: $id})
     SET a.title = $title,
         a.url = $url,
         a.published_date = $date,
         a.is_reference = $bool
  
  5. FOR EACH assertion:
     MATCH (a:Article), (f:Fact)
     MERGE (a)-[:ASSERTED]->(f)
```
**Neo4j Result**: Facts stored WITH embeddings in `f.embedding` property

---

## 2. CLIENT QUERY FLOW (Client → Server → Neo4j → Client)

### Phase 1: Client sends natural language query
```
HTTP POST /api/query/natural
{
  "query": "Turkey detains 357 suspected IS members"
}
```

---

### Phase 2: Server processes query
**Endpoint**: `server/index.js` line 265 onwards

#### Step 2a: Query validation & sanitization
```javascript
- Validate query (required, length limits, no injections)
- Sanitize for Cypher injection
- Query: "Turkey detains 357 suspected IS members"
```

#### Step 2b: Parallel AI Engine calls (Promise.allSettled)
```javascript
// Call 1: Query Expansion
POST /ai/expand_query
{query: "Turkey detains 357 suspected IS members"}
Response: {
  "variations": [
    "Turkey detains ISIS members",
    "Turkish military raid ISIS",
    "357 suspected militants arrested"
  ]
}

// Call 2: Query Embedding (HuggingFace API)
POST /ai/embed_query
{query: "Turkey detains 357 suspected IS members"}
Response: {
  "embedding": [0.123, -0.456, 0.789, ..., 0.234]  // 384 floats
}
```

#### Step 2c: Build Cypher Query
```javascript
// Three decision paths:

IF vector available (length === 384):
  → Use HYBRID search (keyword + vector similarity)
  → buildHybridSearchQuery({fulltextQuery, embedding, vectorWeight=0.5, limit=15})

ELSE IF partial vector:
  → Use VECTOR-ONLY search
  → buildVectorSearchQuery({embedding, similarityThreshold=0.65, limit=15})

ELSE:
  → Use KEYWORD-ONLY search
  → buildSearchFactsQuery({fulltextQuery, useHybrid=false, limit=15})
```

#### Step 2d: Execute Cypher Query on Neo4j

**HYBRID Search Query (with native Cypher cosine similarity)**:
```cypher
// Keyword search scoring
MATCH (f1:Fact)
WHERE toLower(f1.text) CONTAINS toLower($fulltextQuery)
   OR toLower(f1.subject) CONTAINS toLower($fulltextQuery)
   ...
WITH f1, $keywordWeight * f1.confidence AS keywordScore

// Vector similarity scoring (NATIVE CYPHER - NO GDS)
MATCH (f2:Fact)
WHERE f2.embedding IS NOT NULL
WITH f1, keywordScore, f2,
     reduce(sum=0, i in range(0, size(f2.embedding)) | sum + f2.embedding[i]*$embedding[i]) AS dotProduct,
     sqrt(reduce(sum=0, i in range(0, size(f2.embedding)) | sum + f2.embedding[i]*f2.embedding[i])) AS factMag,
     sqrt(reduce(sum=0, i in range(0, size($embedding)) | sum + $embedding[i]*$embedding[i])) AS queryMag

WITH f1, keywordScore, f2,
     CASE WHEN factMag = 0 OR queryMag = 0 THEN 0 
          ELSE dotProduct / (factMag * queryMag) END AS cosineSim,
     $vectorWeight * CASE WHEN factMag = 0 OR queryMag = 0 THEN 0
                         ELSE dotProduct / (factMag * queryMag) END AS vectorScore

// Combine & rank
WITH COALESCE(f1, f2) as f,
     COALESCE(keywordScore, 0) + COALESCE(vectorScore, 0) AS hybridScore,
     COALESCE(f1.confidence, f2.confidence) AS confidence

WITH f, hybridScore, confidence,
     (CASE WHEN confidence > 0.8 THEN 1.2 ELSE 1.0 END) AS confBoost,
     (CASE WHEN confidence > 0.9 THEN 1.5 ELSE 1.0 END) AS highConfBoost

WITH f, (hybridScore * confidence * confBoost * highConfBoost) AS finalScore

ORDER BY finalScore DESC
LIMIT $limit

RETURN f as fact, finalScore as relevance
```

**Parameters passed**:
```javascript
{
  fulltextQuery: "Turkey detains 357 suspected IS members",
  embedding: [0.123, -0.456, 0.789, ..., 0.234],  // 384 values
  keywordWeight: 0.5,
  vectorWeight: 0.5,
  limit: neo4j.int(15)
}
```

**Neo4j Response**: Array of records
```javascript
[
  {fact: {properties: {id, text, subject, predicate, object, confidence, embedding}}, relevance: 0.87},
  {fact: {properties: {id, text, subject, predicate, object, confidence, embedding}}, relevance: 0.76},
  ...
]
```

---

### Phase 3: Server formats and returns results

#### Step 3a: Extract fact data from Neo4j response
```javascript
const records = result.records.map((record) => {
  const fact = record.get("fact");  // Neo4j node with properties
  const relevance = record.get("relevance");
  
  return {
    id: fact.properties.id,
    text: fact.properties.text,
    subject: fact.properties.subject,
    predicate: fact.properties.predicate,
    object: fact.properties.object,
    confidence: fact.properties.confidence,
    relevance: typeof relevance === "object" ? relevance.low : relevance
  };
});
```

#### Step 3b: AI Analysis (optional)
```javascript
IF records.length > 0:
  POST /ai/analyze_results
  {
    query: "Turkey detains 357 suspected IS members",
    results: [records from Neo4j]
  }
  
  Response:
  {
    "analysis": "Natural language synthesis of findings...",
    "cleaned_results": [cleaned fact records]
  }
```

#### Step 3c: Return to client
```javascript
HTTP 200 OK
{
  "success": true,
  "query": "Turkey detains 357 suspected IS members",
  "count": 12,
  "analysis": "AI synthesis of results...",
  "results": [
    {
      "id": "fact_123",
      "text": "Turkey detained 357 suspected IS militants in nationwide raid",
      "subject": "Turkey",
      "predicate": "detained",
      "object": "357 suspected IS members",
      "confidence": 0.92,
      "relevance": 0.87
    },
    ...
  ]
}
```

---

## 3. DATA TRANSFORMATIONS SUMMARY

| Stage | Input | Process | Output | Storage |
|-------|-------|---------|--------|---------|
| 1. Ingestion | RSS URLs | Parse | articles (id, url, title) | PostgreSQL |
| 2. Hydration | articles | Fetch HTML | raw_text | PostgreSQL |
| 3. Classification | raw_text | Topic API | article_topics | PostgreSQL |
| 4. Extraction | raw_text | Groq LLM | Facts (S-P-O) | extracted_facts table |
| 4a. Vectorization | statement | HF API | 384-dim vector | embedding column (pgvector) |
| 4b. Dedup | embedding | Vector distance | Skip if < 0.05 | N/A |
| 5. Verification | facts | Google/Web API | is_original, checked_at | extracted_facts table |
| 6a. Sync (Python) | PostgreSQL | Filter+Transform | JSON payload | temp_graph_data.json |
| 6b. Sync (JS) | JSON payload | Cypher MERGE | Fact nodes | Neo4j (f.embedding stored) |
| 7. Query | natural language | Expand + Embed | keywords + vector | AI Engine |
| 8. Search | Neo4j | Hybrid Cypher | ranked facts | Server response |
| 9. Analysis | facts | Gemini synthesis | cleaned results | HTTP response |

---

## 4. KEY INSIGHTS

### ✅ Vector Chain is COMPLETE:
1. ✅ Embeddings GENERATED in `digest_articles.py` via HuggingFace API
2. ✅ Embeddings STORED in PostgreSQL as `pgvector` type
3. ✅ Embeddings FETCHED in `sync_truth_graph.py`
4. ✅ Embeddings WRITTEN to Neo4j as `f.embedding` property
5. ✅ Embeddings USED in hybrid search with native Cypher cosine similarity

### ✅ Native Cypher Cosine Similarity (NO GDS required):
- Uses REDUCE to compute dot product: `reduce(sum=0, i in range(...) | sum + a[i]*b[i])`
- Calculates magnitudes using nested REDUCE: `sqrt(reduce(sum=0, i | sum + a[i]*a[i]))`
- Computes cosine similarity: `dotProduct / (magA * magB)`
- Works on Neo4j free tier (no GDS plugin needed)

### ✅ Query Flow is SYMMETRIC:
- **Ingestion → Neo4j**: Facts flow: Articles → Extraction → Vectorization → Graph Sync
- **Query → Results**: Facts flow: Client → Server → AI Engine → Neo4j → Results → Client

### Quality Gates Ensure Data Integrity:
- **Gate 1** (Extract): Vector deduplication (< 0.05 distance)
- **Gate 2** (Sync): Facts must be VERIFIED (checked_at IS NOT NULL)
- **Gate 3** (Sync): Articles must be CLASSIFIED (have topic)

---

## 5. VECTOR PIPELINE END-TO-END

```
Article Text
     ↓
Groq LLM
 {S, P, O}
     ↓
Statement = "S P O"
     ↓
HuggingFace API (Cloud Mode)
HF_TOKEN authenticated
     ↓
384-dim vector
     ↓
PostgreSQL pgvector column
     ↓
Vector dedup check
     ↓
Neo4j f.embedding property
     ↓
Query embedding (HF API)
     ↓
Native Cypher cosine similarity
     ↓
Ranked facts returned
```

---

## 6. CURRENT STATUS

- ✅ Embeddings: Generated, stored, synchronized
- ✅ Native cosine similarity: Implemented (no GDS dependency)
- ✅ Hybrid search: Active (keyword + vector)
- ✅ Data flow: Complete ingestion → storage → query → response
- ✅ Cloud mode: Uses HuggingFace API (no local models)
- ⏳ Testing: Ready for vector search testing on production data

