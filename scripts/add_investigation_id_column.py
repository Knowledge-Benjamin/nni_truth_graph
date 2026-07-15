#!/usr/bin/env python3
"""
Migration script to add investigation_id column to extracted_claims table.
Supports investigation-specific claim routing in pipeline stages 7-8.
"""

import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(__file__), '../ai_engine/.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

def add_investigation_id_column():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'extracted_claims' 
                AND column_name = 'investigation_id'
            )
        """)
        
        if cursor.fetchone()[0]:
            print("✓ investigation_id column already exists in extracted_claims table")
            cursor.close()
            conn.close()
            return
        
        # Add the investigation_id column with foreign key constraint
        print("Adding investigation_id column to extracted_claims table...")
        cursor.execute("""
            ALTER TABLE extracted_claims 
            ADD COLUMN investigation_id INTEGER REFERENCES investigations(id) ON DELETE CASCADE;
        """)
        
        # Create index on investigation_id for query performance
        print("Creating index on investigation_id...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_claims_investigation_id 
            ON extracted_claims(investigation_id);
        """)
        
        conn.commit()
        print("✓ investigation_id column added successfully")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"✗ Error adding investigation_id column: {e}")
        raise

if __name__ == "__main__":
    add_investigation_id_column()
