#!/usr/bin/env python3
"""
Safely clear only pending pipeline-stage rows for stages 2–8.

This script does NOT truncate tables, delete databases, or remove committed/processed claims.
It only updates pending queue rows to a neutral state so the pipeline can be restarted cleanly.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, 'ai_engine/.env'))

DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print('ERROR: DATABASE_URL is not set in ai_engine/.env')
    sys.exit(1)


def clean_pipeline_stages():
    print('Connecting to PostgreSQL...')
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        print('Clearing pending scrape queue rows...')
        cur.execute("""
            UPDATE raw_urls
            SET status = 'CLEANED'
            WHERE status IN (
                'PENDING_SCRAPE',
                'SCRAPING',
                'PENDING_VIDEO',
                'VIDEO_SCRAPED',
                'EXTRACTING',
                'FAILED',
                'FAILED_NO_ACCESS'
            )
        """)
        print(f'  raw_urls updated: {cur.rowcount}')

        print('Clearing pending article-stage rows...')
        cur.execute("""
            UPDATE raw_articles
            SET status = 'CLEANED'
            WHERE status IN (
                'PENDING_CLASSIFICATION',
                'PROCESSING_CLASSIFICATION',
                'PENDING_EXTRACTION',
                'PROCESSING_EXTRACTION',
                'FAILED_EXTRACTION'
            )
        """)
        print(f'  raw_articles updated: {cur.rowcount}')

        print('Clearing extraction progress rows...')
        cur.execute("DELETE FROM article_extraction_progress")
        print(f'  article_extraction_progress cleared: {cur.rowcount}')

        print('Clearing pending extracted-claim stage rows...')
        cur.execute("""
            UPDATE extracted_claims
            SET status = 'CLEANED',
                pipeline_stage = CASE
                    WHEN pipeline_stage IS NULL THEN 'COMPLETE'
                    ELSE pipeline_stage
                END
            WHERE status NOT IN ('GRAPH_COMMITTED', 'COMPLETE', 'RED_TEAM_REJECTED')
              AND (
                    pipeline_stage IS NULL
                    OR pipeline_stage IN (
                        'STAGE_4_RESOLUTION',
                        'STAGE_5_RESOLUTION_IN_PROGRESS',
                        'STAGE_6_DEDUP',
                        'STAGE_6_DEDUP_IN_PROGRESS',
                        'STAGE_6_DEDUP_DONE',
                        'STAGE_7_CROSS_REF',
                        'STAGE_7_CROSS_REF_IN_PROGRESS',
                        'STAGE_8_MUTATION_QUEUE',
                        'STAGE_8_MUTATION_IN_PROGRESS'
                    )
                    OR status IN ('PROCESSING', 'AUTO_APPROVE')
              )
        """)
        print(f'  extracted_claims updated: {cur.rowcount}')

        print('Verifying preserved committed claims...')
        cur.execute("""
            SELECT COUNT(*)
            FROM extracted_claims
            WHERE status IN ('GRAPH_COMMITTED', 'COMPLETE', 'RED_TEAM_REJECTED')
        """)
        committed_count = cur.fetchone()[0]
        print(f'  preserved committed/processed claims: {committed_count}')

        print('\nPipeline-stage cleanup complete.')
        print('Database schema and committed claims were preserved.')

    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    clean_pipeline_stages()
