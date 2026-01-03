from google import genai
from google.genai import types
from neo4j import GraphDatabase, TrustAll
import os
from typing import Dict

class QueryTranslator:
    """
    Translates natural language questions to Neo4j Cypher queries.
    Uses Gemini 2.0 Flash (free tier: 1500 queries/day).
    """
    
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        
        if not api_key:
            print("⚠️  GEMINI_API_KEY not set. Natural language queries disabled.")
            self.client = None
            return
        
        self.client = genai.Client(api_key=api_key)
        print("✅ Gemini query translator ready")
        
        # Graph schema for context
        self.schema_context = """
        Neo4j Graph Schema:
        
        Nodes:
        - (:Fact {id, text, subject, predicate, object, confidence, embedding})
        - (:Article {id, title, url, date, is_reference})
        
        Relationships:
        - (Article)-[:ASSERTED]->(Fact)
        
        Rules:
        - confidence: float 0.0-1.0
        - Use `CALL db.index.fulltext.queryNodes("fact_statement_fulltext", "search terms") YIELD node, score` for keyword search relevance.
        - Use `CALL db.index.vector.queryNodes("fact_embeddings", limit, embedding) YIELD node, score` for semantic search.
        - Return ONLY Cypher query, no markdown formatting.
        - Prioritize finding the exact fact or most relevant facts.
        - ALWAYS return the node as `f` and relevance as `relevance`.
        - DO NOT return "f.text" directly. Use "RETURN f, relevance".
        - Use LIMIT 20.
        """
    
    def translate_to_cypher(self, user_query: str) -> Dict[str, str]:
        """
        Convert natural language to Cypher query.
        
        Args:
            user_query: Natural language question
            Example: "Show me vaccine claims with high confidence"
        
        Returns:
            {"query": "MATCH (c:Claim)...", "explanation": "..."}
        """
        if not self.client:
            return {
                "query": None,
                "error": "Gemini API not configured. Set GEMINI_API_KEY environment variable."
            }
        
        prompt = f"""{self.schema_context}
        
        User Question: "{user_query}"
        
        Generate a Cypher query to answer this question.
        Prefer using Fulltext Index `fact_statement_fulltext` for relevance if searching for a topic.
        If strict filtering is needed (e.g. "confidence > 0.9"), combine it with WHERE.
        
        Return format:
        QUERY: <cypher here>
        EXPLANATION: <brief explanation>
        """
        
        try:
            # Simple retry logic for 429 errors
            import time
            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    break 
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        time.sleep(2 ** (attempt + 1)) # Exponential backoff: 2s, 4s
                        continue
                    raise e

            text = response.text.strip()
            
            # Parse response
            lines = text.split('\n')
            cypher = None
            explanation = None
            
            for line in lines:
                if line.startswith('QUERY:'):
                    cypher = line.replace('QUERY:', '').strip()
                elif line.startswith('EXPLANATION:'):
                    explanation = line.replace('EXPLANATION:', '').strip()
            
            # Fallback: treat entire response as query
            if not cypher:
                cypher = text.replace('```cypher', '').replace('```', '').strip()
            
            return {
                "query": cypher,
                "explanation": explanation or "Generated Cypher query",
                "original_question": user_query
            }
            
        except Exception as e:
            return {
                "query": None,
                "error": f"Query translation failed: {str(e)}"
            }

    def expand_query(self, user_query: str) -> Dict[str, list]:
        """
        Generates search variations to improve recall.
        Returns: { "variations": ["q1", "q2", "q3"] }
        """
        if not self.client:
            return {"variations": [user_query]}
            
        prompt = f"""
        User Search: "{user_query}"
        
        Task: Generate 3 effective search queries to find verified fact-checks for this topic.
        Include synonyms, specific entity names, and related concepts.
        Keep them concise keyword-based queries.
        
        Return JSON:
        {{
            "variations": ["query 1", "query 2", "query 3"]
        }}
        """
        
        try:
            import time
            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type='application/json'
                        )
                    )
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    raise e
            
            import json
            return json.loads(response.text)
        except Exception as e:
            print(f"Expansion Error: {e}")
            return {"variations": [user_query]}

    def extract_claims_from_text(self, text: str) -> list:
        """
        Extracts verifiable claims from text using Gemini.
        """
        if not self.client:
             return []
        
        prompt = f"""
        Analyze the following text and extract all verifiable factual claims.
        
        CRITICAL RULES:
        1. STAND-ALONE FACTS: Rewrite every claim so it makes sense in isolation.
           - Replace pronouns (he, she, it, they) with specific names.
           - Replace "the event" or "the disaster" with the specific event name.
           - Add dates and locations if they are mentioned earlier in the text but missing from the sentence.
        
        2. VERIFIABLE: Only extract objective facts (numbers, dates, actions). Ignore opinions.
        
        Example:
        Text: "A massive earthquake struck Japan in 2025. It killed 956 people."
        Output: ["A massive earthquake struck Japan in 2025.", "The 2025 Japan earthquake killed 956 people."]
        
        Text:
        "{text[:8000]}"
        
        Return ONLY a JSON list of strings:
        [ "Claim 1", "Claim 2", ... ]
        """
        
        try:
            import time
            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type='application/json'
                        )
                    )
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    raise e

            import json
            data = json.loads(response.text)
            if isinstance(data, list):
                return data
            # Handle if AI returns object with key
            for key in data:
                if isinstance(data[key], list):
                    return data[key]
            return []
        except Exception as e:
            print(f"Extraction Error: {e}")
            return []

class ResultAnalyzer:
    """
    Analyzes Neo4j search results using Gemini to provide a natural language summary
    and clean up dirty data (HTML tags, etc.).
    """
    
    def __init__(self):
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        load_dotenv(env_path)
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print(f"[ERROR] GEMINI_API_KEY not found in {env_path}")
            self.client = None
            return
            
        self.client = genai.Client(api_key=api_key)
    
    def analyze_results(self, query: str, results: list) -> Dict:
        """
        Analyze search results and provide summary + cleaned data.
        
        Args:
            query: The user's original question
            results: List of records from Neo4j
            
        Returns:
            {
                "analysis": "Natural language summary...",
                "cleaned_results": [ ...modified results... ]
            }
        """
        if not self.client:
            return {
                "analysis": "AI Analysis Unavailable (API Key missing)",
                "cleaned_results": results
            }
            
        # construct prompt
        prompt = f"""
        User Query: "{query}"
        
        Search Results (Raw Data):
        {results}
        
        Task:
        1. Analyze the search results to answer the user's query. Synthesis the information into a clear, concise cohesive paragraph.
        2. Filter the results: Keep ONLY the results that are directly relevant and used as evidence for your answer. Discard irrelevant matches.
        3. Clean the 'statement' field of the RELEVANT results by removing ALL HTML tags, style attributes, and weird artifacts.
        
        Return JSON format:
        {{
            "analysis": "<your synthesis here>",
            "cleaned_results": [
                {{ "statement": "<cleaned statement>", "confidence": <num>, ...all original fields... }}
            ]
        }}
        """
        
        try:
            import time
            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type='application/json'
                        )
                    )
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    raise e
            
            import json
            data = json.loads(response.text)
            return data
            
        except Exception as e:
            print(f"Analysis Error: {e}")
            return {
                "analysis": "Failed to generate analysis.",
                "cleaned_results": results
            }


# --- SEARCH ENGINE (Phase 6.2) ---

class GraphSearcher:
    """
    Advanced Neo4j graph search with hybrid search capabilities.
    Combines keyword search, vector similarity, and relationship analysis.
    """
    
    def __init__(self):
        """Initialize Neo4j driver and search utilities."""
        from neo4j import GraphDatabase
        
        self.neo4j_uri = os.getenv("NEO4J_URI")
        self.neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = os.getenv("NEO4J_PASSWORD")
        
        if not all([self.neo4j_uri, self.neo4j_user, self.neo4j_password]):
            print("⚠️  Neo4j credentials not fully configured. Graph search unavailable.")
            self.driver = None
            return
        
        try:
            # For neo4j+s:// URIs, use TrustAll() but don't pass encrypted=True
            # The +s in the URI already indicates encryption
            self.driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password),
                trusted_certificates=TrustAll()
            )
            # Test connection
            with self.driver.session() as session:
                session.run("RETURN 1")
            print("✅ GraphSearcher initialized (Neo4j connected)")
        except Exception as e:
            print(f"❌ Failed to initialize GraphSearcher: {e}")
            self.driver = None
    
    def hybrid_search(self, query: str, embedding: list = None, limit: int = 15):
        """
        Perform hybrid search combining keyword and vector similarity.
        
        Args:
            query: Search query string
            embedding: Optional embedding vector (384-dim)
            limit: Maximum results to return
            
        Returns:
            Dictionary with search results and metadata
        """
        if not self.driver:
            return {"error": "Graph search unavailable", "results": []}
        
        try:
            with self.driver.session() as session:
                # Select search strategy based on embedding availability
                if embedding and len(embedding) == 384:
                    cypher = """
                        // Hybrid: Keyword + Vector similarity
                        MATCH (f:Fact)
                        WHERE toLower(f.text) CONTAINS toLower($query)
                           OR toLower(f.subject) CONTAINS toLower($query)
                           OR toLower(f.object) CONTAINS toLower($query)
                        WITH f,
                             f.confidence AS keywordScore
                        
                        // Vector similarity scoring
                        WITH f,
                             keywordScore,
                             1 - gds.similarity.cosine(f.embedding, $embedding) AS vectorDistance
                        
                        // Hybrid score (50/50 weight)
                        WITH f,
                             (0.5 * keywordScore + 0.5 * (1 - vectorDistance)) AS hybridScore
                        
                        ORDER BY hybridScore DESC
                        LIMIT $limit
                        
                        RETURN f.id as id,
                               f.text as statement,
                               f.subject as subject,
                               f.predicate as predicate,
                               f.object as object,
                               f.confidence as confidence,
                               hybridScore as relevance
                    """
                    result = session.run(cypher, {
                        "query": query,
                        "embedding": embedding,
                        "limit": limit
                    })
                else:
                    # Keyword-only search
                    cypher = """
                        MATCH (f:Fact)
                        WHERE toLower(f.text) CONTAINS toLower($query)
                           OR toLower(f.subject) CONTAINS toLower($query)
                           OR toLower(f.object) CONTAINS toLower($query)
                        
                        ORDER BY f.confidence DESC
                        LIMIT $limit
                        
                        RETURN f.id as id,
                               f.text as statement,
                               f.subject as subject,
                               f.predicate as predicate,
                               f.object as object,
                               f.confidence as confidence,
                               f.confidence as relevance
                    """
                    result = session.run(cypher, {
                        "query": query,
                        "limit": limit
                    })
                
                # Format results
                results = [dict(record) for record in result]
                
                return {
                    "query": query,
                    "count": len(results),
                    "results": results,
                    "search_type": "hybrid" if embedding else "keyword"
                }
        
        except Exception as e:
            print(f"❌ Hybrid search error: {e}")
            return {"error": str(e), "results": []}
    
    def get_fact_history(self, fact_id: str):
        """
        Get the evolution/history of a fact over time.
        Traces how facts evolve and their relationships.
        
        Args:
            fact_id: The ID of the fact to trace
            
        Returns:
            Timeline of fact evolution
        """
        if not self.driver:
            return {"error": "Graph search unavailable", "history": []}
        
        try:
            with self.driver.session() as session:
                # Get fact evolution chain
                cypher = """
                    MATCH (f:Fact {id: $fact_id})
                    OPTIONAL MATCH (f)-[:EVOLVES_TO]->(evolved:Fact)
                    OPTIONAL MATCH (f)<-[:EVOLVES_TO]-(predecessor:Fact)
                    OPTIONAL MATCH (a:Article)-[:ASSERTED]->(f)
                    
                    RETURN f.id as id,
                           f.text as statement,
                           f.confidence as confidence,
                           f.created_at as created_at,
                           predecessor.id as predecessor_id,
                           evolved.id as evolved_to_id,
                           collect(DISTINCT a.title) as sources
                """
                
                result = session.run(cypher, {"fact_id": fact_id})
                record = result.single()
                
                if not record:
                    return {"error": "Fact not found", "history": []}
                
                # Build evolution timeline
                history = {
                    "fact_id": record["id"],
                    "statement": record["statement"],
                    "confidence": record["confidence"],
                    "created_at": record["created_at"],
                    "predecessor": record["predecessor_id"],
                    "evolved_to": record["evolved_to_id"],
                    "sources": record["sources"]
                }
                
                return {
                    "fact_id": fact_id,
                    "history": history,
                    "timeline": [history]  # Can be extended to full chain
                }
        
        except Exception as e:
            print(f"❌ Fact history retrieval error: {e}")
            return {"error": str(e), "history": []}
    
    def find_contradictions(self, fact_id: str, confidence_threshold: float = 0.5):
        """
        Find facts that contradict a given fact.
        
        Args:
            fact_id: The fact to find contradictions for
            confidence_threshold: Minimum confidence for both facts
            
        Returns:
            List of contradicting facts
        """
        if not self.driver:
            return {"error": "Graph search unavailable", "contradictions": []}
        
        try:
            with self.driver.session() as session:
                cypher = """
                    MATCH (f1:Fact {id: $fact_id})
                    -[rel:CONTRADICTS]->(f2:Fact)
                    
                    WHERE f1.confidence > $threshold
                      AND f2.confidence > $threshold
                    
                    RETURN f1.id as source_id,
                           f1.text as source_statement,
                           f1.confidence as source_confidence,
                           f2.id as target_id,
                           f2.text as target_statement,
                           f2.confidence as target_confidence,
                           rel.weight as contradiction_weight
                    
                    ORDER BY contradiction_weight DESC
                """
                
                result = session.run(cypher, {
                    "fact_id": fact_id,
                    "threshold": confidence_threshold
                })
                
                contradictions = [dict(record) for record in result]
                
                return {
                    "fact_id": fact_id,
                    "count": len(contradictions),
                    "contradictions": contradictions
                }
        
        except Exception as e:
            print(f"❌ Contradiction search error: {e}")
            return {"error": str(e), "contradictions": []}
    
    def close(self):
        """Close Neo4j driver connection."""
        if self.driver:
            self.driver.close()
            print("✅ GraphSearcher closed")
