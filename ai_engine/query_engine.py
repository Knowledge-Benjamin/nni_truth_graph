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
        - Prioritize finding the exact claim or most relevant claims.
        - ALWAYS return the node as `c` and relevance as `relevance`.
        - DO NOT return "c.statement" directly. Use "RETURN c, relevance".
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
    
        return True

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

