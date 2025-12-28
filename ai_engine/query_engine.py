from google import genai
from google.genai import types
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
        - (:Claim {statement, confidence, first_seen, last_verified, embedding})
        - (:Source {url, publisher, stance, snippet, published_date})
        - (:Article {id, title, url, published_date})
        - (:Entity {name, type})
        - (:Topic {name})
        
        Relationships:
        - (Article)-[:MENTIONS]->(Claim)
        - (Claim)-[:CITED_BY {stance}]->(Source)
        - (Claim)-[:VERIFIED_BY]->(Source)
        - (Claim)-[:SIMILAR_TO {score}]->(Claim)
        - (Claim)-[:CONTRADICTS]->(Claim)
        - (Claim)-[:MENTIONS]->(Entity)
        - (Claim)-[:RELATES_TO]->(Topic)
        
        Rules:
        - stance values: "SUPPORT", "CONTRADICT", "NEUTRAL"
        - confidence: float 0.0-1.0
        - Use `CALL db.index.fulltext.queryNodes("claim_statement_fulltext", "search terms") YIELD node, score` for keyword search relevance.
        - Return ONLY Cypher query, no markdown formatting.
        - Prioritize finding the exact claim or most relevant claims.
        - Return `node` as `c`, `score` as `relevance` if using fulltext.
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
        Prefer using Fulltext Index `claim_statement_fulltext` for relevance if searching for a topic.
        If strict filtering is needed (e.g. "confidence > 0.9"), combine it with WHERE.
        
        Return format:
        QUERY: <cypher here>
        EXPLANATION: <brief explanation>
        """
        
        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
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
    
    def validate_query(self, cypher: str) -> bool:
        """
        Basic validation of generated Cypher.
        
        Returns:
            True if query looks safe
        """
        dangerous_keywords = ['DELETE', 'DETACH', 'DROP', 'CREATE INDEX', 'ALTER']
        cypher_upper = cypher.upper()
        
        for keyword in dangerous_keywords:
            if keyword in cypher_upper:
                return False
        
        return True
