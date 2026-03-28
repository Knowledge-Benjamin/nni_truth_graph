import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

EXTENDED_SOURCES = [
    # Wikipedia
    {"name": "Wikipedia: Featured Articles", "url": "https://en.wikipedia.org/w/api.php?action=featuredfeed&feed=featured&feedformat=rss", "domain": "wikipedia.org", "category": "Encyclopedia", "trust_score": 0.95},
    {"name": "Wikipedia: In the News", "url": "https://en.wikipedia.org/w/api.php?action=featuredfeed&feed=onthisday&feedformat=rss", "domain": "wikipedia.org", "category": "Encyclopedia", "trust_score": 0.90},
    
    # arXiv (Cornell University)
    {"name": "arXiv: Computer Science", "url": "https://rss.arxiv.org/rss/cs", "domain": "arxiv.org", "category": "Science", "trust_score": 0.95},
    {"name": "arXiv: Physics", "url": "https://rss.arxiv.org/rss/physics", "domain": "arxiv.org", "category": "Science", "trust_score": 0.95},
    {"name": "arXiv: Mathematics", "url": "https://rss.arxiv.org/rss/math", "domain": "arxiv.org", "category": "Science", "trust_score": 0.95},
    {"name": "arXiv: Quantitative Biology", "url": "https://rss.arxiv.org/rss/q-bio", "domain": "arxiv.org", "category": "Science", "trust_score": 0.95},
    {"name": "arXiv: Quantitative Finance", "url": "https://rss.arxiv.org/rss/q-fin", "domain": "arxiv.org", "category": "Business", "trust_score": 0.90},
    
    # PLOS (Public Library of Science)
    {"name": "PLOS Biology", "url": "https://journals.plos.org/plosbiology/article/feed", "domain": "plos.org", "category": "Science", "trust_score": 0.98},
    {"name": "PLOS Computational Biology", "url": "https://journals.plos.org/ploscompbiol/article/feed", "domain": "plos.org", "category": "Science", "trust_score": 0.98},
    {"name": "PLOS Medicine", "url": "https://journals.plos.org/plosmedicine/article/feed", "domain": "plos.org", "category": "Science", "trust_score": 0.98},
    
    # Universities
    {"name": "MIT News", "url": "https://news.mit.edu/rss/feed", "domain": "mit.edu", "category": "Science", "trust_score": 0.95},
    {"name": "Stanford News", "url": "https://news.stanford.edu/feed/", "domain": "stanford.edu", "category": "Science", "trust_score": 0.95},
    {"name": "Harvard Gazette", "url": "https://news.harvard.edu/gazette/feed/", "domain": "harvard.edu", "category": "Science", "trust_score": 0.95},
    
    # Project Gutenberg (New Books)
    {"name": "Project Gutenberg: New Books", "url": "https://www.gutenberg.org/cache/epub/feeds/today.rss", "domain": "gutenberg.org", "category": "Books", "trust_score": 0.90},

    # East Africa / Uganda
    {"name": "Daily Monitor Uganda", "url": "https://www.monitor.co.ug/feed", "domain": "monitor.co.ug", "category": "World", "trust_score": 0.70},
]

def add_extended_sources():
    print("Connecting to PostgreSQL to inject extended open knowledge sources...")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        inserted = 0
        for source in EXTENDED_SOURCES:
            try:
                cursor.execute("""
                    INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id;
                """, (source["name"], source["url"], source["domain"], source["category"], source["trust_score"]))
                
                if cursor.fetchone():
                    inserted += 1
            except Exception as e:
                print(f"Error inserting {source['name']}: {e}")
                
        print(f"Successfully injected {inserted} new high-value knowledge sources into the dynamic database!")
        
    except psycopg2.Error as e:
        print(f"PostgreSQL connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    add_extended_sources()
