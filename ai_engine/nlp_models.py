from typing import List, Dict

class SemanticLinker:
    """
    Generates semantic embeddings for claims and finds similar existing claims.
    Uses sentence-transformers/all-MiniLM-L6-v2 (80MB, fast, free).
    """
    
    def __init__(self):
        print("🔄 Loading Semantic Similarity Model...")
        # Lazy import to avoid loading heavy dependencies at module level
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        print("✅ Semantic model ready (384-dim embeddings)")
    
    def get_embedding(self, text: str) -> List[float]:
        """
        Convert text to 384-dimensional vector.
        
        Args:
            text: Claim statement or any text
        
        Returns:
            List of 384 floats (semantic embedding)
        """
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
