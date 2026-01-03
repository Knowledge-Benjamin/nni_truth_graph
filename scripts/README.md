# Scripts Directory

This directory contains data processing scripts for the NNI Truth Graph.

## JavaScript Scripts

Some scripts require Node.js dependencies. To install them:

```bash
cd scripts
npm install
```

This will install:
- `dotenv` - Environment variable management
- `neo4j-driver` - Neo4j database driver

## Python Scripts

Python scripts use dependencies from the main project. Ensure you have installed the requirements:

```bash
pip install -r ai_engine/requirements.txt
```

## Running Scripts

### JavaScript Scripts
```bash
node scripts/push_to_neo4j.js
node scripts/verify_graph.js
```

### Python Scripts
```bash
python scripts/sync_truth_graph.py
python scripts/digest_articles.py
# etc.
```

