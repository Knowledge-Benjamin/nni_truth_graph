"""
Shared Database Utilities
Provides centralized connection management for PostgreSQL and Neo4j.
Eliminates duplication across 40+ scripts.
"""

import os
import psycopg2
from psycopg2 import pool
from neo4j import GraphDatabase, TrustAll
import logging

logger = logging.getLogger(__name__)

# PostgreSQL Connection Pool
_pg_pool = None
_neo4j_driver = None


def get_pg_connection():
    """
    Get a connection from the PostgreSQL connection pool.
    Initializes pool if not already done.
    """
    global _pg_pool
    
    if _pg_pool is None:
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        
        try:
            _pg_pool = psycopg2.pool.SimpleConnectionPool(
                1, 10,  # Min/max connections
                db_url
            )
            logger.info("PostgreSQL connection pool initialized")
        except psycopg2.OperationalError as e:
            logger.error(f"Failed to create PostgreSQL pool: {e}")
            raise
    
    try:
        return _pg_pool.getconn()
    except psycopg2.pool.PoolError as e:
        logger.error(f"Failed to get connection from pool: {e}")
        raise


def release_pg_connection(conn):
    """Return a connection to the pool."""
    global _pg_pool
    if _pg_pool and conn:
        _pg_pool.putconn(conn)


def close_pg_pool():
    """Close all PostgreSQL connections."""
    global _pg_pool
    if _pg_pool:
        _pg_pool.closeall()
        _pg_pool = None
        logger.info("PostgreSQL connection pool closed")


def get_neo4j_driver():
    """
    Get Neo4j driver instance.
    Creates if not already initialized.
    Handles self-signed certificates on Neo4j cloud instances.
    """
    global _neo4j_driver
    
    if _neo4j_driver is None:
        uri = os.getenv('NEO4J_URI')
        user = os.getenv('NEO4J_USER')
        password = os.getenv('NEO4J_PASSWORD')
        
        if not all([uri, user, password]):
            raise ValueError("Neo4j credentials not set in environment variables")
        
        try:
            # Convert neo4j+s:// to bolt+s:// for encrypted bolt connection
            bolt_uri = uri.replace('neo4j+s://', 'bolt+s://').replace('neo4j://', 'bolt://')
            
            # For encrypted schemes (bolt+s), let the URI handle encryption
            # Don't pass encrypted/trust settings with bolt+s as it causes conflicts
            if bolt_uri.startswith('bolt+s://'):
                _neo4j_driver = GraphDatabase.driver(
                    bolt_uri,
                    auth=(user, password),
                    trusted_certificates=TrustAll()
                )
            else:
                _neo4j_driver = GraphDatabase.driver(
                    bolt_uri,
                    auth=(user, password)
                )
            logger.info(f"Neo4j driver initialized: {bolt_uri}")
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            raise
    
    return _neo4j_driver


def close_neo4j_driver():
    """Close Neo4j driver."""
    global _neo4j_driver
    if _neo4j_driver:
        _neo4j_driver.close()
        _neo4j_driver = None
        logger.info("Neo4j driver closed")


def query_neo4j(query_str, parameters=None):
    """
    Execute a Neo4j read query.
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run(query_str, parameters or {})
        return result.data()


def write_neo4j(query_str, parameters=None):
    """
    Execute a Neo4j write transaction.
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        return session.write_transaction(
            lambda tx: tx.run(query_str, parameters or {}).data()
        )


def cleanup():
    """Close all database connections."""
    close_pg_pool()
    close_neo4j_driver()
