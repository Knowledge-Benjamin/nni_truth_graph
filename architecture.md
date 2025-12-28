# Architecture & Tech Stack

## Backend
- **Graph Database**: Neo4j
  - Stores: Nodes (Claim, Source, Entity) and Relationships.
  - Driver: Neo4j Node.js driver.
- **Relational Database**: PostgreSQL (via Prisma ORM)
  - Stores: Metadata, Users, Verification Queues, Article Content.
- **API Runtime**: Node.js.
- **Queues**: BullMQ (handling background verification tasks).
- **Scheduling**: Cron jobs (source re-checks).

## AI & ML
- **LLM Integration**: OpenAI API / Hugging Face.
- **Tasks**: 
  - Claim Extraction.
  - Source Suggestion.
- **Logic**: RAG (Retrieval-Augmented Generation) to ground outputs in trusted corpora.

## Frontend
- **Framework**: React (Vite).
- **Visualization**: 
  - `vis.js` or `Cytoscape.js` for graph rendering.
- **UI Components**: Interactive panel, tooltips, sub-graph zooming.

## Security
- **Authentication**: JWT based.
- **Audit**: Immutable logs (potential Blockchain integration in future).
