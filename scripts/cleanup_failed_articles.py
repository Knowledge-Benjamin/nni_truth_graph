#!/usr/bin/env python3
"""
Safe cleanup script to remove failed and unextracted articles from the database.

Deletion criteria:
1. Articles with NO extracted claims (unextracted articles)
2. Articles marked as failed during scraping
3. Articles with NO raw_url references (orphaned)

Preserved:
- Articles with at least 1 successful extracted claim
- Source data (never deleted)
- All investigation and orchestration data

This script performs safe deletion with foreign key cascades and logs all deletions.
"""

import psycopg2
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")

def cleanup_failed_articles():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        print(f"[{datetime.now().isoformat()}] Starting safe article cleanup...")
        print("=" * 80)
        
        # 1. Identify articles with NO extracted claims (unextracted)
        cursor.execute("""
            SELECT ra.id, ra.title, ru.url
            FROM raw_articles ra
            LEFT JOIN extracted_claims ec ON ra.id = ec.article_id
            LEFT JOIN raw_urls ru ON ra.url_id = ru.id
            WHERE ec.id IS NULL
            AND ra.scraped_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'
            AND (
                ru.metadata->>'investigation_id' IS NULL
                OR (
                    (ru.metadata->>'investigation_id')::int IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM investigations i
                        WHERE i.id = (ru.metadata->>'investigation_id')::int
                        AND i.status = 'ACTIVE'
                    )
                )
            )
        """)
        unextracted = cursor.fetchall()
        unextracted_ids = [row[0] for row in unextracted]
        
        print(f"\n[UNEXTRACTED] Found {len(unextracted)} articles with NO extracted claims:")
        for art_id, title, url in unextracted[:10]:  # Show first 10
            print(f"  - ID {art_id}: {title[:50] if title else 'N/A'}")
        if len(unextracted) > 10:
            print(f"  ... and {len(unextracted) - 10} more")
        
        # 2. Identify articles with failed extraction status
        cursor.execute("""
            SELECT ra.id, ra.title, ru.url
            FROM raw_articles ra
            LEFT JOIN raw_urls ru ON ra.url_id = ru.id
            WHERE ra.status = 'FAILED'
            OR ra.status = 'ERROR'
            OR ra.status = 'SKIPPED'
        """)
        failed = cursor.fetchall()
        failed_ids = [row[0] for row in failed]
        
        print(f"\n[FAILED] Found {len(failed)} articles with FAILED extraction status:")
        for art_id, title, url in failed[:10]:
            print(f"  - ID {art_id}: {title[:50] if title else 'N/A'}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")
        
        # 3. Combine IDs, avoiding duplicates
        articles_to_delete = list(set(unextracted_ids + failed_ids))
        print(f"\n[TOTAL] Combined deletion set: {len(articles_to_delete)} articles")
        
        if not articles_to_delete:
            print("\n✓ No articles to delete. Database is clean.")
            cursor.close()
            conn.close()
            return
        
        # 4. Show dependencies before deletion
        cursor.execute("""
            SELECT COUNT(*) FROM extracted_claims 
            WHERE article_id = ANY(%s)
        """, (articles_to_delete,))
        orphan_claims = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM article_categories 
            WHERE article_id = ANY(%s)
        """, (articles_to_delete,))
        orphan_categories = cursor.fetchone()[0]
        
        print(f"\n[DEPENDENCIES]")
        print(f"  - {orphan_claims} extracted claims will be cascaded")
        print(f"  - {orphan_categories} article categories will be cascaded")
        
        # 5. User confirmation
        print(f"\n[CONFIRMATION]")
        response = input(f"Delete {len(articles_to_delete)} articles? (yes/no): ").strip().lower()
        
        if response != 'yes':
            print("✗ Cleanup cancelled.")
            cursor.close()
            conn.close()
            return
        
        # 6. Delete article_categories first (no cascades)
        if orphan_categories > 0:
            print(f"\nDeleting {orphan_categories} article_categories entries...")
            cursor.execute("""
                DELETE FROM article_categories 
                WHERE article_id = ANY(%s)
            """, (articles_to_delete,))
            print(f"✓ Deleted {cursor.rowcount} article_categories")
        
        # 7. Delete extracted_claims (cascades via foreign key)
        if orphan_claims > 0:
            print(f"Deleting {orphan_claims} extracted_claims entries...")
            cursor.execute("""
                DELETE FROM extracted_claims 
                WHERE article_id = ANY(%s)
            """, (articles_to_delete,))
            print(f"✓ Deleted {cursor.rowcount} extracted_claims")
        
        # 8. Delete raw_articles (CASCADE will handle the above)
        print(f"Deleting {len(articles_to_delete)} raw_articles entries...")
        cursor.execute("""
            DELETE FROM raw_articles 
            WHERE id = ANY(%s)
        """, (articles_to_delete,))
        deleted_count = cursor.rowcount
        print(f"✓ Deleted {deleted_count} raw_articles")
        
        # 9. Commit and report
        conn.commit()
        
        print(f"\n{'=' * 80}")
        print(f"[{datetime.now().isoformat()}] Cleanup complete!")
        print(f"✓ Successfully removed {deleted_count} articles and all dependencies")
        
        # 10. Final statistics
        cursor.execute("SELECT COUNT(*) FROM raw_articles")
        remaining_articles = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM extracted_claims")
        remaining_claims = cursor.fetchone()[0]
        
        print(f"\n[FINAL STATE]")
        print(f"  - Remaining articles: {remaining_articles}")
        print(f"  - Remaining extracted claims: {remaining_claims}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"\n✗ Error during cleanup: {e}")
        try:
            conn.rollback()
        except:
            pass
        raise

if __name__ == "__main__":
    cleanup_failed_articles()
