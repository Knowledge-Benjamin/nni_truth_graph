"""
Backfill Script: Process existing NNI.news articles into Truth Graph.
Connects to Neon PostgreSQL, fetches published articles, sends to AI Engine.
"""

import psycopg2
import requests
import time
import re
from datetime import datetime

# Database connection
NEON_DB = "postgresql://neondb_owner:npg_b2sNTig0IBmZ@ep-summer-fog-adlvchlm-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require"

# Truth Graph API
TRUTH_GRAPH_API = "http://localhost:3000/api/ingest"

def strip_html(html_text):
    """Remove HTML tags from content"""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', html_text)

def main():
    print("=" * 60)
    print("NNI.news → Truth Graph Backfill Script")
    print("=" * 60)
    
    try:
        # Connect to Neon DB
        print("\n[..] Connecting to Neon PostgreSQL...")
        conn = psycopg2.connect(NEON_DB)
        cur = conn.cursor()
        print("[OK] Connected!")
        
        # Fetch published articles
        print("\n[..] Fetching published articles...")
        cur.execute("""
            SELECT id, title, slug, content, "publishedAt" 
            FROM articles 
            WHERE status = 'published' AND content IS NOT NULL
            ORDER BY "publishedAt" ASC
        """)
        
        articles = cur.fetchall()
        total_articles = len(articles)
        articles = cur.fetchall()
        total_articles = len(articles)
        print(f"[OK] Found {total_articles} articles to process\n")
        
        if total_articles == 0:
            print("[WARN]  No articles to process. Exiting.")
            return
        
        # Process each article
        successful = 0
        failed = 0
        
        for idx, (article_id, title, slug, content, pub_date) in enumerate(articles, 1):
            print(f"\n[{idx}/{total_articles}] Processing: {title[:50]}...")
            
            # Clean HTML from content
            clean_content = strip_html(content)
            
            # Create payload
            payload = {
                "title": title,
                "text": clean_content,
                "article_id": article_id,
                "source_url": f"https://nni.news/{slug}",
                "published_date": pub_date.isoformat() if pub_date else datetime.now().isoformat()
            }
            
            # Send to Truth Graph API
            try:
                response = requests.post(
                    TRUTH_GRAPH_API, 
                    json=payload,
                    timeout=30
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    claims_extracted = len(result.get('data', []))
                    print(f"  [OK] Success! Extracted {claims_extracted} claims")
                    successful += 1
                else:
                    print(f"  [ERR] Failed: HTTP {response.status_code}")
                    print(f"     {response.text[:100]}")
                    failed += 1
                
                # Rate limit: wait between requests for API quota management
                if idx < total_articles:
                    # 1-hour cooldown to stay within HF free tier (300 req/hour)
                    # Each article: ~44 claims × 3 sources = 132 API calls
                    # 132 < 300 → Safe per hour
                    wait_time = 3600  # 1 hour in seconds
                    
                    print(f"\n{'='*60}")
                    print(f"[OK] Article {idx}/{total_articles} complete!")
                    print(f"[..] Cooling down for 1 hour before next article...")
                    print(f"   Next article starts at: {datetime.fromtimestamp(time.time() + wait_time).strftime('%I:%M %p')}")
                    print(f"   Progress: {idx}/{total_articles} ({(idx/total_articles)*100:.1f}%)")
                    print(f"   Estimated completion: {(total_articles - idx)} hours remaining")
                    print(f"{'='*60}\n")
                    
                    # Countdown timer (updates every 5 minutes)
                    for remaining in range(wait_time, 0, -300):  # Update every 5 min
                        mins = remaining // 60
                        print(f"  [..]  {mins} minutes remaining...", end='\r')
                        time.sleep(min(300, remaining))
                    
                    print("\n  [OK] Cooldown complete! Processing next article...\n")
                    
            except requests.exceptions.RequestException as e:
                print(f"  [ERR] Request failed: {e}")
                failed += 1
            except Exception as e:
                print(f"  [ERR] Unexpected error: {e}")
                failed += 1
        
        # Summary
        print("\n" + "=" * 60)
        print("BACKFILL COMPLETE!")
        print("=" * 60)
        print(f"[OK] Successful: {successful}")
        print(f"[ERR] Failed: {failed}")
        print(f"Total: {total_articles}")
        
        # Close database connection
        cur.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"\n[ERR] Database error: {e}")
    except Exception as e:
        print(f"\n[ERR] Unexpected error: {e}")

if __name__ == "__main__":
    main()
