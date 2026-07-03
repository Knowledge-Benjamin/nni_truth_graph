---
title: CC Proxy Server
emoji: 🛡️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Common Crawl Proxy Server

A standalone microservice proxy that securely interfaces with the Common Crawl Index (CDX) API to resolve web archives without getting rate-limited.
Designed for the NNI Truth Graph framework.

## Recommended deployment

This proxy can be deployed from a dedicated GitHub repo such as `https://github.com/Knowledge-Benjamin/proxyServer`.
Use the same Hugging Face account as `SearchServer` and configure a Space with a name like `cc-proxy`.

### Environment variables for HF Space
- `HF_TOKEN`
- `HF_ACCOUNT` (optional, defaults to the token owner)
- `HF_SPACE_NAME` (optional, defaults to `cc-proxy`)

### Runtime environment variables
- `CC_PROXY_BASE` should point to the deployed proxy URL.
- `CC_PROXY_TIMEOUT` can be increased if archive proxy responses are slow.
