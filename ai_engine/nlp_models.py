from typing import List, Dict, Optional
import os
import logging
import numpy as np
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SemanticLinker:
    """
    Generates semantic embeddings for claims and finds similar existing claims.
    Uses sentence-transformers/all-MiniLM-L6-v2 (80MB, fast, free) in local mode only.
    In cloud mode: Uses HuggingFace Inference API (no local models downloaded).
    """
    
    def __init__(self):
        print("___SL_INIT_START___", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        
        try:
            print("___SL_ENV_CHECK___", flush=True)
            sys.stdout.flush()
            
            logger.info("[INFO] Loading Semantic Similarity Model...")
            self.use_api = False
            self.api_token = os.getenv("HF_TOKEN")
            self.model_name = "sentence-transformers/all-MiniLM-L6-v2"
            self.hf_client = None  # Lazy initialization
            execution_mode = os.getenv("EXECUTION_MODE", "heuristic")

            print(f"___SL_MODE_{execution_mode}___", flush=True)
            sys.stdout.flush()

            # Cloud mode MUST use API, not local models - NEVER download models in cloud
            if execution_mode == "cloud":
                print("___SL_CLOUD_MODE___", flush=True)
                sys.stdout.flush()
                
                logger.info("[INFO] ☁️  CLOUD MODE: Disabling local model download - using HuggingFace API only")
                self.use_api = True
                self.model = None
                
                print("___SL_CLOUD_VARS_CHECK___", flush=True)
                sys.stdout.flush()
                
                # Defer InferenceClient initialization (avoid blocking on network calls)
                if not self.api_token:
                    print("___SL_NO_TOKEN___", flush=True)
                    sys.stdout.flush()
                    logger.error("❌ HF_TOKEN is missing from environment variables!")
                else:
                    print(f"___SL_TOKEN_OK___len={len(self.api_token)}___", flush=True)
                    sys.stdout.flush()
                    logger.info(f"✅ HF_TOKEN found (length: {len(self.api_token)} chars)")
                    logger.info("✅ HuggingFace API mode enabled - client will initialize on first use")
            else:
                print("___SL_LOCAL_MODE___", flush=True)
                sys.stdout.flush()
                
                # Local or Heuristic mode: try to load local model (if sentence-transformers is installed)
                try:
                    print("___SL_IMPORTING_ST___", flush=True)
                    sys.stdout.flush()
                    
                    from sentence_transformers import SentenceTransformer
                    logger.info("[INFO] sentence-transformers available - loading local model...")
                    
                    print("___SL_LOADING_ST_MODEL___", flush=True)
                    sys.stdout.flush()
                    
                    self.model = SentenceTransformer('all-MiniLM-L6-v2')
                    logger.info("[SUCCESS] Semantic model ready (Local: 384-dim)")
                    
                    print("___SL_ST_LOADED___", flush=True)
                    sys.stdout.flush()
                    
                except ImportError:
                    print("___SL_ST_NOTFOUND___", flush=True)
                    sys.stdout.flush()
                    
                    # Fallback to API using official client library
                    logger.warning("[WARN] Local 'sentence_transformers' not found. Switching to Cloud API Mode.")
                    self.use_api = True
                    self.model = None
                    
                    # Debug token availability
                    if not self.api_token:
                        print("___SL_FALLBACK_NO_TOKEN___", flush=True)
                        sys.stdout.flush()
                        logger.error("❌ HF_TOKEN is missing from environment variables!")
                    else:
                        print(f"___SL_FALLBACK_TOKEN_OK___len={len(self.api_token)}___", flush=True)
                        sys.stdout.flush()
                        logger.info(f"✅ HF_TOKEN found (length: {len(self.api_token)} chars)")
                        logger.info("✅ HuggingFace API mode enabled - client will initialize on first use")
            
            print("___SL_INIT_DONE___", flush=True)
            sys.stdout.flush()
            
        except Exception as e:
            import traceback
            print(f"___SL_INIT_ERROR___: {str(e)}", flush=True)
            sys.stdout.flush()
            print(traceback.format_exc(), flush=True)
            sys.stdout.flush()
            raise

    def _ensure_hf_client(self):
        """Lazily initialize HuggingFace InferenceClient on first use."""
        if self.hf_client is not None:
            return True  # Already initialized
        
        if not self.api_token:
            logger.error("❌ HF_TOKEN environment variable is not set!")
            return False
        
        try:
            from huggingface_hub import InferenceClient
            logger.info("✅ Initializing HuggingFace InferenceClient (deferred initialization)...")
            self.hf_client = InferenceClient(token=self.api_token, timeout=30)
            logger.info("✅ HuggingFace InferenceClient initialized successfully")
            return True
        except ImportError as e:
            logger.error(f"❌ huggingface_hub not installed! Error: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ InferenceClient initialization failed: {e}")
            return False

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Convert text to 384-dimensional vector (Local or Cloud API).
        
        Returns:
            384-dim vector or None if generation fails (prevents database corruption)
        """
        if self.use_api:
            if not self._ensure_hf_client():
                return None
            
            try:
                # Use official client's feature_extraction method (handles routing correctly)
                result = self.hf_client.feature_extraction(
                    text,
                    model=self.model_name
                )
                
                # Result is already the embedding vector
                # Handle numpy array (default return type)
                if hasattr(result, 'tolist'):
                    result = result.tolist()
                
                if isinstance(result, list):
                    if len(result) > 0 and isinstance(result[0], list):
                        return result[0]  # Unwrap if nested
                    return result
                
                logger.error(f"Unexpected result type: {type(result)}")
                return None
                
            except Exception as e:
                logger.error(f"Embedding API Failed: {e}")
                return None
        else:
            embedding = self.model.encode(text, convert_to_tensor=False)
            return embedding.tolist()
    
    def batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts efficiently.
        
        Args:
            texts: List of claim statements
        
        Returns:
            List of embedding vectors
        """
        embeddings = self.model.encode(texts, batch_size=32, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings.
        
        Returns:
            Similarity score (0.0 - 1.0)
        """
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        
        dot_product = np.dot(vec1, vec2)
        norm_product = np.linalg.norm(vec1) * np.linalg.norm(vec2)
        
        return float(dot_product / norm_product) if norm_product > 0 else 0.0


class EntityExtractor:
    """
    Extracts named entities from text using GLiNER (state-of-the-art 2024).
    Supports zero-shot entity recognition.
    Falls back to basic spaCy if GLiNER unavailable.
    """
    
    def __init__(self):
        print("[INFO] Loading Entity Extraction Model...")
        try:
            import spacy
            try:
                from gliner_spacy.pipeline import GlinerSpacy
                
                self.nlp = spacy.blank("en")
                self.nlp.add_pipe("gliner_spacy", config={
                    "gliner_model": "urchade/gliner_medium-v2.1",
                    "labels": ["PERSON", "ORG", "GPE", "EVENT", "PRODUCT", "LAW", "DATE"],
                    "chunk_size": 250
                })
                print("[SUCCESS] Entity extraction ready (GLiNER + spaCy)")
            except ImportError:
                print("[WARN] GLiNER not available, using basic spaCy NER")
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                except OSError:
                    # Model not downloaded
                    print("[WARN] spaCy model not found. Run: python -m spacy download en_core_web_sm")
                    self.nlp = None
        except Exception as e:
            print(f"[ERROR] Entity extraction unavailable: {e}")
            self.nlp = None
    
    def extract_entities(self, text: str) -> List[Dict[str, str]]:
        """
        Extract named entities from text.
        
        Args:
            text: Claim statement or article text
        
        Returns:
            List of entities: [{"text": "NASA", "type": "ORG"}, ...]
        """
        if not self.nlp:
            return []  # Graceful degradation
        
        doc = self.nlp(text)
        entities = []
        
        for ent in doc.ents:
            entities.append({
                "text": ent.text,
                "type": ent.label_,
                "start": ent.start_char,
                "end": ent.end_char
            })
        
        return entities
    
    def extract_unique_entities(self, text: str) -> List[Dict[str, str]]:
        """
        Extract entities and deduplicate by text.
        
        Returns:
            Unique entities only
        """
        entities = self.extract_entities(text)
        seen = set()
        unique = []
        
        for ent in entities:
            key = (ent['text'].lower(), ent['type'])
            if key not in seen:
                seen.add(key)
                unique.append(ent)
        
        return unique
