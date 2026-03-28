"""
World-Class Vision Embedding Inference Server
=============================================

A high-performance FastAPI server for generating unified Vision-Language 
embeddings using `google/siglip-base-patch16-224`. Designed to run 
freely on Hugging Face Spaces.

Features:
- Image URL downloading & Base64 parsing
- SigLIP Tensor embeddings
- API key authentication 
- Async concurrent endpoints
"""

import os
import io
import asyncio
import base64
import requests
from typing import List, Optional
from contextlib import asynccontextmanager

import torch
from PIL import Image
import imagehash
from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from transformers import AutoProcessor, AutoModel
from loguru import logger

# Model configuration
MODEL_NAME = "google/siglip-base-patch16-224"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
API_KEY = os.getenv("INFERENCE_API_KEY", "default-key-change-in-production")
VERSION = "1.0.0"

# Global model instance
processor = None
model = None

security = HTTPBearer()

class VisionEmbedRequest(BaseModel):
    image_urls: Optional[List[str]] = Field(default=[], description="List of image URLs to embed")
    image_base64: Optional[List[str]] = Field(default=[], description="List of base64 encoded images")

class VisionTextEmbedRequest(BaseModel):
    texts: List[str] = Field(..., description="List of texts to embed into the visual latent space")

class VisionEmbedResponse(BaseModel):
    embeddings: List[List[float]] = Field(..., description="List of 768-dimensional visual vectors")
    phashes: Optional[List[str]] = Field(default=None, description="Perceptual hashes for exact/similar crop duplicate detection")

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        logger.warning("Invalid API key attempt")
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials

@asynccontextmanager
async def lifespan(app: FastAPI):
    global processor, model
    logger.info(f"Loading Vision Model {MODEL_NAME} on {DEVICE}")
    try:
        processor = AutoProcessor.from_pretrained(MODEL_NAME)
        model = AutoModel.from_pretrained(MODEL_NAME).to(DEVICE)
        model.eval()
        logger.info("Vision Model loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load vision model: {e}")
        raise
    yield
    logger.info("Shutting down Vision server")

app = FastAPI(
    title="Vision Inference Server (SigLIP)",
    description="Multi-modal embedding service for NNI Truth Graph. Maps text and images to the same unified latent space.",
    version=VERSION,
    lifespan=lifespan
)

def _process_images(urls: List[str], b64s: List[str]) -> List[Image.Image]:
    images = []
    # Process URLs
    for url in urls:
        try:
            resp = requests.get(url, timeout=5)
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            images.append(img)
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
            raise ValueError(f"Failed to download image from {url}")
            
    # Process Base64
    for b64 in b64s:
        try:
            if "," in b64: b64 = b64.split(",")[1]
            img_data = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            images.append(img)
        except Exception as e:
            logger.error(f"Failed to decode base64 image: {e}")
            raise ValueError("Failed to decode base64 image")
            
    return images

def _embed_images(images: List[Image.Image]) -> tuple[List[List[float]], List[str]]:
    try:
        # 1. Tensor Vectors (Semantic Similarity)
        inputs = processor(images=images, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            vision_outputs = model.vision_model(**inputs)
            # Pool and Normalize to match text space exactly natively
            image_embeds = vision_outputs.pooler_output
            image_embeds = image_embeds / image_embeds.norm(dim=-1, keepdim=True)
            
        vector_list = image_embeds.cpu().tolist()
        
        # 2. Perceptual Hashes (Exact Duplicate/Cropping Match)
        phash_list = []
        for img in images:
             phash_list.append(str(imagehash.phash(img)))
             
        return vector_list, phash_list
    except Exception as e:
        logger.error(f"Computation failed: {e}")
        raise ValueError("Computation failed")

@app.post("/embed_media", response_model=VisionEmbedResponse)
async def embed_media(request: VisionEmbedRequest, _: str = Depends(verify_api_key)):
    try:
        total = len(request.image_urls) + len(request.image_base64)
        if total == 0 or total > 16:
            raise HTTPException(status_code=400, detail="Must provide 1 to 16 images")
            
        logger.info(f"Processing batch of {total} images")
        
        # IO bound (download) + CPU bound
        images = await asyncio.to_thread(_process_images, request.image_urls, request.image_base64)
        
        # GPU/CPU bound (matrix math + perceptual hashing)
        embeddings, phashes = await asyncio.to_thread(_embed_images, images)
        
        return VisionEmbedResponse(embeddings=embeddings, phashes=phashes)
        
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Embedding error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

def _embed_text_sync(texts: List[str]) -> List[List[float]]:
    try:
        inputs = processor(text=texts, padding="max_length", return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            text_outputs = model.text_model(**inputs)
            # Pool and Normalize to match image space exactly
            text_embeds = text_outputs.pooler_output
            text_embeds = text_embeds / text_embeds.norm(dim=-1, keepdim=True)
            
        return text_embeds.cpu().tolist()
    except Exception as e:
        logger.error(f"Text computation failed: {e}")
        raise ValueError("Text computation failed")

@app.post("/embed_text", response_model=VisionEmbedResponse)
async def embed_text(request: VisionTextEmbedRequest, _: str = Depends(verify_api_key)):
    try:
        if not request.texts:
            raise HTTPException(status_code=400, detail="Must provide texts")
            
        embeddings = await asyncio.to_thread(_embed_text_sync, request.texts)
        return VisionEmbedResponse(embeddings=embeddings)
        
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Text embedding error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "model": MODEL_NAME, "device": DEVICE, "capabilities": ["siglip_embedding"]}

if __name__ == "__main__":
    import uvicorn
    # 7860 is the default port for Hugging Face Spaces dockerized apps
    uvicorn.run(app, host="0.0.0.0", port=7860)
