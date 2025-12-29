from typing import List, Dict, Optional
import os
import requests
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SemanticLinker:
    """
    Generates semantic embeddings for claims and finds similar existing claims.
    Uses sentence-transformers/all-MiniLM-L6-v2 (80MB, fast, free).
    """
    
    def __init__(self):
        logger.info("🔄 Loading Semantic Similarity Model...")
        self.use_api = False
        self.api_token = os.getenv("HF_TOKEN")
        # Use task-specific pipeline endpoint for explicit feature extraction (2025)
        self.api_url = "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2"

        try:
            # Try loading local model
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("✅ Semantic model ready (Local: 384-dim)")
        except ImportError:
            # Fallback to API
            logger.warning("⚠️ Local 'sentence_transformers' not found. Switching to Cloud API Mode.")
            self.use_api = True
            if not self.api_token:
                logger.error("❌ HF_TOKEN is missing! Embeddings will fail.")

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Convert text to 384-dimensional vector (Local or Cloud API).
        
        Returns:
            384-dim vector or None if generation fails (prevents database corruption)
        """
        if self.use_api:
            if not self.api_token:
                logger.error("HF_TOKEN missing - cannot generate embeddings")
                return None
            
            headers = {"Authorization": f"Bearer {self.api_token}"}
            payload = {"inputs": text, "options": {"wait_for_model": True}}

            try:
                response = requests.post(self.api_url, headers=headers, json=payload)
                
                if response.status_code == 200:
                    result = response.json()
                    # Handle robustly: could be list of floats or list of list of floats
                    if isinstance(result, list):
                        if len(result) > 0 and isinstance(result[0], list):
                            return result[0] # [[0.1, ...]] -> [0.1, ...]
                        return result # [0.1, ...]
                    return result
                else:
                    logger.error(f"API Error {response.status_code}: {response.text}")
                    logger.debug(f"Payload: {payload}")
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
        print("🔄 Loading Entity Extraction Model...")
        try:
            import spacy
            try:
                from gliner_spacy.pipeline import GlinerSpacy
                
                self.nlp = spacy.blank("en")
                self.nlp.add_pipe("gliner_spacy", config={
                    "model": "urchade/gliner_medium-v2.1",
                    "labels": ["PERSON", "ORG", "GPE", "EVENT", "PRODUCT", "LAW", "DATE"],
                    "chunk_size": 250
                })
                print("✅ Entity extraction ready (GLiNER + spaCy)")
            except ImportError:
                print("⚠️  GLiNER not available, using basic spaCy NER")
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                except OSError:
                    # Model not downloaded
                    print("⚠️  spaCy model not found. Run: python -m spacy download en_core_web_sm")
                    self.nlp = None
        except Exception as e:
            print(f"⚠️  Entity extraction unavailable: {e}")
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
