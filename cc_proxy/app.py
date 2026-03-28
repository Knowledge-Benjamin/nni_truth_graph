from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import httpx
import os
import logging

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

# Cache the CC index info on startup — avoids per-request overhead
CACHED_COLLINFO = None
COMMON_CRAWL_BASE = "https://index.commoncrawl.org"
HEADERS = {"User-Agent": "KnowledgeBenjiTruthGraphBot/1.0 (Contact: admin@example.com)"}

@app.on_event("startup")
async def startup_event():
    global CACHED_COLLINFO
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COMMON_CRAWL_BASE}/collinfo.json", headers=HEADERS, timeout=30.0)
            CACHED_COLLINFO = resp.json()
            logger.info(f"Loaded collinfo: {len(CACHED_COLLINFO)} indexes")
    except Exception as e:
        logger.error(f"Failed to load collinfo: {e}")
        CACHED_COLLINFO = []

@app.get("/")
def read_root():
    return {"message": "KnowledgeBenji Common Crawl Proxy is running.", "indexes": len(CACHED_COLLINFO or [])}

@app.get("/collinfo.json")
async def get_collinfo():
    """Return cached collinfo (with cdx-api paths rewritten to point through this proxy)."""
    if not CACHED_COLLINFO:
        return JSONResponse(status_code=503, content={"error": "Index not loaded yet, retry shortly"})
    
    # Rewrite cdx-api URLs so callers route through us
    rewritten = []
    for entry in CACHED_COLLINFO:
        e = dict(entry)
        if "cdx-api" in e:
            e["cdx-api"] = e["cdx-api"].replace(COMMON_CRAWL_BASE, "")
        rewritten.append(e)
    return rewritten

@app.get("/{path:path}")
async def proxy_get(path: str, request: Request):
    """Transparently proxy any CDX query to index.commoncrawl.org."""
    target_url = f"{COMMON_CRAWL_BASE}/{path}"
    params = dict(request.query_params)
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(target_url, params=params, headers=HEADERS, timeout=90.0)
            
            safe_headers = {}
            for k, v in resp.headers.items():
                if k.lower() in ("content-type", "content-length"):
                    safe_headers[k] = v
                    
            return Response(content=resp.content, status_code=resp.status_code, headers=safe_headers)
        except Exception as e:
            logger.error(f"Proxy error for {target_url}: {e}")
            return Response(content=f"Proxy error: {str(e)}", status_code=500)
