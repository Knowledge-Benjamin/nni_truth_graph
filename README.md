---
title: TRUTH AI ENGINE
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# NNI Truth - AI Engine

This is the background processing AI Engine for the NNI Truth project. This Space runs a backend Docker container with a Celery worker, PostgreSQL connections, and headless Chromium (Playwright) to continuously extract structured claims from the web into Neo4j.

**Note:** This space does not have a web frontend. It exposes port 7860 internally to pass Hugging Face health checks while running the Python Celery orchestrator in the background.
