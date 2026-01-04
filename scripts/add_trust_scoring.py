import psycopg2
import os
import json
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Environment
# CRITICAL FIX: dotenv.load_dotenv() WITHOUT arguments does NOT load system env vars on Render
env_path = os.path.join(os.path.dirname(__file__), '../ai_engine/.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    logger.info(f"✅ Loaded .env from {env_path}")
else:
    logger.info("ℹ️ No .env file found - using system environment variables (Render deployment)")
DATABASE_URL = os.getenv("DATABASE_URL")

def load_trusted_sources():
    """Load trusted sources with their trust scores."""
    sources_path = os.path.join(os.path.dirname(__file__), '../data/trusted_sources.json')
    with open(sources_path, 'r') as f:
        data = json.load(f)
    return data['trusted_sources']

def add_trust_score_column():
    """Add trust_score column to articles table."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        logger.info("🛡️  Adding trust_score column to articles table...")
        
        # Add column (default 0.5 = Unknown)
        cur.execute("""
            ALTER TABLE articles 
            ADD COLUMN IF NOT EXISTS trust_score FLOAT DEFAULT 0.5;
        """)
        
        # Create index for filtering by trust
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_trust 
            ON articles(trust_score DESC);
        """)
        
        conn.commit()
        logger.info("✅ trust_score column added.")
        
        # Now populate trust scores from trusted_sources.json
        logger.info("📊 Populating trust scores from trusted sources...")
        
        trusted_sources = load_trusted_sources()
        
        # Build domain -> trust_score mapping
        domain_scores = {}
        for source in trusted_sources:
            url = source['url']
            score = source['trust_score'] / 10.0  # Normalize to 0.0-1.0
            domain = urlparse(url).netloc
            # Remove 'www.' prefix
            domain = domain.replace('www.', '')
            domain_scores[domain] = score
        
        logger.info(f"📋 Loaded {len(domain_scores)} trusted domains.")
        
        # Update articles with trust scores
        updated_count = 0
        for domain, score in domain_scores.items():
            cur.execute("""
                UPDATE articles 
                SET trust_score = %s 
                WHERE url LIKE %s
            """, (score, f'%{domain}%'))
            updated_count += cur.rowcount
        
        conn.commit()
        logger.info(f"✅ Updated {updated_count} articles with trust scores.")
        
        # Show distribution
        cur.execute("""
            SELECT 
                CASE 
                    WHEN trust_score >= 0.9 THEN 'High (0.9-1.0)'
                    WHEN trust_score >= 0.7 THEN 'Medium (0.7-0.9)'
                    WHEN trust_score >= 0.5 THEN 'Low (0.5-0.7)'
                    ELSE 'Unknown (<0.5)'
                END as trust_tier,
                COUNT(*) as count
            FROM articles
            GROUP BY trust_tier
            ORDER BY trust_tier DESC
        """)
        
        logger.info("\n📊 Trust Score Distribution:")
        for tier, count in cur.fetchall():
            logger.info(f"   {tier}: {count} articles")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    add_trust_score_column()
