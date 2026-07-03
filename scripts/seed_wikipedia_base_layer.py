import os
import sys
import time
import requests
import psycopg2
import urllib.parse
from typing import Optional
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# Force UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, 'ai_engine/.env'))
from ai_engine.core.logger import get_printer
print = get_printer(1)  # Cyan for Ingest

DATABASE_URL = os.getenv("DATABASE_URL")

# Wikipedia API Endpoint
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"

# Tier 1 Entities to Seed
SEED_ENTITIES = [
    # Global Powers & Nations
    "United States", "China", "Russia", "European Union", "India", 
    "United Kingdom", "France", "Germany", "Japan", "Israel", "Iran", "Ukraine",
    "Uganda", "Kenya", "Tanzania", "Rwanda", "South Africa", "Nigeria",
    
    # Major Global Leaders & Figures
    "Joe Biden", "Donald Trump", "Xi Jinping", "Vladimir Putin", 
    "Emmanuel Macron", "Olaf Scholz", "Rishi Sunak", "Narendra Modi",
    "Benjamin Netanyahu", "Volodymyr Zelenskyy", "Yoweri Museveni",
    "Elon Musk", "Sam Altman", "Mark Zuckerberg", "Tim Cook", "Satya Nadella",
    "Jensen Huang",
    
    # Technology & Corporations
    "OpenAI", "Google", "Microsoft", "Apple", "Meta Platforms", "Nvidia",
    "Tesla, Inc.", "SpaceX", "Amazon (company)", "Taiwan Semiconductor Manufacturing Company",
    
    # Major Geopolitical Events / Concepts
    "NATO", "United Nations", "World Health Organization", "BRICS",
    "Artificial intelligence", "Artificial general intelligence",
    "Climate change", "COVID-19 pandemic", "Russian invasion of Ukraine"
]

# --- Dynamic country/entity loading ---
import json
import urllib.request
import os



COUNTRY_JSON_PATH = os.path.join(os.path.dirname(__file__), '../ai_engine/scripts/country_by_name.json')
COUNTRY_JSON_URL = 'https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-name.json'

FALLBACK_ENTITIES = [
    "United States", "China", "Russia", "European Union", "India", 
    "United Kingdom", "France", "Japan", "Germany", "Iran", "Kenya", "Ukraine", "Tanzania", "Uganda", "Rwanda",
    "Donald Trump", "Joe Biden", "South Africa", "Xi Jinping", "Vladimir Putin", "Emmanuel Macron", "Rishi Sunak", "Olaf Scholz", "Narendra Modi", "Benjamin Netanyahu", "Volodymyr Zelenskyy", "Yoweri Museveni",
    "Sam Altman", "Elon Musk", "Mark Zuckerberg", "Satya Nadella", "Tim Cook", "OpenAI", "Jensen Huang", "Apple", "Nvidia", "Meta Platforms", "Google", "Microsoft", "SpaceX", "TSMC", "Tesla, Inc.", "Amazon (company)",
    "NATO", "United Nations", "World Health Organization", "BRICS",
    "Artificial intelligence", "Artificial general intelligence",
    "Climate change", "COVID-19 pandemic", "Russian invasion of Ukraine"
]

def download_country_json():
    try:
        print(f"[SEED] Downloading country list from {COUNTRY_JSON_URL} ...")
        urllib.request.urlretrieve(COUNTRY_JSON_URL, COUNTRY_JSON_PATH)
        print("[SEED] Download complete.")
    except Exception as e:
        print(f"[SEED][ERROR] Failed to download country list: {e}")

def load_seed_entities():
    if not os.path.exists(COUNTRY_JSON_PATH):
        download_country_json()
    try:
        with open(COUNTRY_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            countries = sorted({entry["country"] for entry in data if "country" in entry})
            print(f"[SEED] Loaded {len(countries)} countries from country_by_name.json.")
            return countries + [
                # Add extra global powers, organizations, and events
                "European Union", "NATO", "United Nations", "World Health Organization", "BRICS",
                "Artificial intelligence", "Artificial general intelligence",
                "Climate change", "COVID-19 pandemic", "Russian invasion of Ukraine"
            ]
    except Exception as e:
        print(f"[SEED][WARNING] Could not load country list: {e}")
        return FALLBACK_ENTITIES

SEED_ENTITIES = load_seed_entities()

# --- Integrate additional entities from parse_entity_datasets.py (AFTER load_seed_entities) ---
import sys
sys.path.append(os.path.join(os.path.dirname(__file__)))
try:
    from parse_entity_datasets import parse_nobel_laureates, parse_country_capitals, parse_kaggle_celebrities
    additional_entities = []
    try:
        n = parse_nobel_laureates()
        c = parse_country_capitals()
        k = parse_kaggle_celebrities()
        additional_entities = (n or []) + (c or []) + (k or [])
        print(f"[SEED] Loaded {len(additional_entities)} additional entities from parse_entity_datasets.py.")
    except Exception as e:
        print(f"[SEED][WARNING] Could not load additional entities: {e}")
    SEED_ENTITIES = SEED_ENTITIES + [e for e in additional_entities if e not in SEED_ENTITIES]
except Exception as e:
    print(f"[SEED][WARNING] Could not import or integrate additional entities: {e}")

def fetch_wikipedia_summary(entity_name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Fetches the title, extract (summary), and full URL from Wikipedia."""
    params = {
        "action": "query",
        "format": "json",
        "titles": entity_name,
        "prop": "info|extracts",
        "inprop": "url",
        "exintro": "1",          # Only the introduction
        "explaintext": "1",      # Plain text, no HTML
        "redirects": "1"         # Follow redirects automatically
    }
    try:
        headers = {
            "User-Agent": "LivingTruthGraph/1.0 (Research Project; contact@example.com) python-requests"
        }
        resp = requests.get(WIKI_API_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1":
                print(f"  [WIKIPEDIA SKIP] Entity not found: {entity_name}")
                return None, None, None
            
            title = page_data.get("title")
            extract = page_data.get("extract")
            url = page_data.get("fullurl")
            
            # Clean up the extract if it's too short (disambiguation pages)
            if not extract or len(extract) < 150:
                print(f"  [WIKIPEDIA SKIP] Extract too short for {title}")
                return None, None, None
                
            return title, extract, url
            
    except Exception as e:
        print(f"  [WIKIPEDIA ERROR] Failed fetching {entity_name}: {e}")
        return None, None, None
    return None, None, None  # Empty pages dict


def seed_worker(entity_name: str):
    """Fetches the entity and writes it to the raw_articles pipeline."""
    title, text, url = fetch_wikipedia_summary(entity_name)
    if not title or not text or not url:
        return
        
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            # 1. Ensure Wikipedia is a registered Source
            cur.execute("""
                INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
                VALUES ('Wikipedia (Foundational)', 'https://en.wikipedia.org', 'wikipedia.org', 'API', 0.95)
                ON CONFLICT (url) DO NOTHING
                RETURNING id;
            """)
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM sources WHERE url = 'https://en.wikipedia.org'")
                row = cur.fetchone()
            source_id = row[0]
            
            # 2. Add URL
            cur.execute("""
                INSERT INTO raw_urls (source_id, url, status)
                VALUES (%s, %s, 'SCRAPED')
                ON CONFLICT (url) DO NOTHING
                RETURNING id;
            """, (source_id, url))
            row = cur.fetchone()
            
            if not row:
                print(f"  [SKIP] {title} already exists in database.")
                conn.close()
                return
                
            url_id = row[0]
            
            # 3. Inject directly into Stage 3 (PENDING_CLASSIFICATION)
            cur.execute("""
                INSERT INTO raw_articles (url_id, title, author, publish_date, raw_text, status)
                VALUES (%s, %s, 'Wikipedia Contributors', CURRENT_TIMESTAMP, %s, 'PENDING_CLASSIFICATION')
            """, (url_id, title, text))
            
            conn.commit()
            print(f"  [SUCCESS] Injected foundational knowledge for: {title} ({len(text or '')} chars)")
            
    except Exception as e:
        print(f"  [DB ERROR] Failed injecting {title}: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def run_seeder():
    print(f"=== Tier 1 Active Ingestion: Wikipedia Base Layer ===")
    print(f"Targeting {len(SEED_ENTITIES)} global entities to pre-warm the Knowledge Graph.")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        for entity in SEED_ENTITIES:
            executor.submit(seed_worker, entity)
            time.sleep(0.1) # Small delay to be polite to Wikipedia API

    print("\nFoundational Seeding Complete. The items are now in PENDING_CLASSIFICATION.")
    print("Run `npm run auto` (or tasks.py) to process them through the AI pipeline.")

if __name__ == "__main__":
    run_seeder()
