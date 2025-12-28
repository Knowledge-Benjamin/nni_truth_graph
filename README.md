# NNI Truth Graph
## A Provenance-Rich Citation System for Nexa News Insights (NNI)

The Truth Graph is a dynamic system designed to enhance news credibility by mapping factual claims to their origins, supporting evidence, contradictions, and updates. It functions as an interactive, auditable network that combats misinformation by providing a visual "chain of custody" for information.

### Core Philosophy
Unlike traditional fact-checking which verifies isolated claims, the Truth Graph builds a web of relationships, allowing users to trace how a fact evolves over time, who endorses or debunks it, and why it's trustworthy.

## How It Works

### 1. Claim Extraction
- **Process**: automated AI scanning of article drafts.
- **Output**: 5-10 factual assertions per article with confidence scores.
- **Human-in-the-loop**: Automated extraction is reviewed by humans to prevent hallucinations.

### 2. Source Linking & Verification
- **Graph Structure**: Claims linked to sources (URLs, PDFs, tweets).
- **Edge Types**: 
  - `SUPPORTED_BY` (Green)
  - `CONTRADICTED_BY` (Red)
  - `CITED_IN` (Gray)
  - `UPDATES` (Blue)
- **Verification**: AI suggests matches from trusted databases (Google Fact Check, Reuters). Verifiers/Experts approve/reject.

### 3. Graph Building & Update
- **Storage**: Hybrid approach.
  - **Neo4j**: Nodes (Claim, Source, Entity) and Edges.
  - **Prisma/Postgres**: Metadata (Article IDs, Timestamps, User data).
- **Dynamics**: "Living Graph" that auto-updates when sources change status (e.g., a source is debunked).

### 4. Query & Audit
- **Public**: API to query the graph (e.g., "Show contradictions for X").
- **Internal**: Immutable audit logs of all changes.

## Visuals
- Interactive panel beside articles.
- Nodes as circles (Claims=Blue, Sources=Green/Red/Gray).
- Weighted edges with confidence scores.
- Timeline views for claim propagation.
