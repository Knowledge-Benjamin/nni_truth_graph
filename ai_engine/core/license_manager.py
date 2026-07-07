"""
ai_engine/core/license_manager.py
─────────────────────────────────────────────────────────────────────────────
Production-grade Licensing & Credit Metering Engine.

Handles the Hybrid Edge-Cloud & BYOG commercial models.
- In 'dev' mode: Passes all checks immediately.
- In 'prod' mode: Cryptographically verifies the appliance license and 
  decrements investigation compute credits upon completion.
"""

import os
import time
import psycopg2
from psycopg2.extras import Json

try:
    import jwt  # PyJWT
except Exception:  # pragma: no cover - optional dependency in some environments
    jwt = None

# Using ENV vars to configure licensing backend
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev").lower()
LICENSE_PUB_KEY = os.getenv("LICENSE_PUB_KEY", "")  # Used to verify offline signed license keys
LICENSE_SERVER_URL = os.getenv("LICENSE_SERVER_URL", "")

class LicenseExhaustedError(Exception):
    """Raised when the client has run out of compute credits."""
    pass

class InvalidLicenseError(Exception):
    """Raised when the cryptographic license is invalid or tampered with."""
    pass


def _ensure_license_table(pg_conn):
    """Ensure the local license tracking table exists in the OVM."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS system_licenses (
                id SERIAL PRIMARY KEY,
                license_key TEXT UNIQUE NOT NULL,
                credits_remaining INT NOT NULL DEFAULT 0,
                valid_until TIMESTAMP WITH TIME ZONE,
                metadata JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """
        )
    pg_conn.commit()


def validate_license(pg_conn) -> bool:
    """
    Checks if the OVM has an active, valid license.
    Returns True if valid, False if expired or missing.
    """
    if ENVIRONMENT == "dev":
        return True
    
    _ensure_license_table(pg_conn)
    with pg_conn.cursor() as cur:
        # Check if there is an active, unexpired license with > 0 credits
        cur.execute(
            """
            SELECT SUM(credits_remaining) as total_credits
            FROM system_licenses
            WHERE valid_until > NOW() OR valid_until IS NULL
            """
        )
        row = cur.fetchone()
        total_credits = row[0] if row and row[0] is not None else 0
        
        if total_credits <= 0:
            print("[License Manager] CRITICAL: Zero compute credits remaining. System locked.")
            return False
            
    return True


def decrement_investigation_credit(pg_conn, investigation_id: int) -> None:
    """
    Called by terminator.py when an investigation successfully completes.
    Strictly deducts 1 compute credit from the local license pool in production.
    """
    if ENVIRONMENT == "dev":
        print(f"[License Manager] DEV MODE: Investigation {investigation_id} completed. No credits deducted.")
        return

    _ensure_license_table(pg_conn)
    
    with pg_conn.cursor() as cur:
        # Find the oldest valid license with credits and decrement by 1
        cur.execute(
            """
            UPDATE system_licenses
            SET credits_remaining = credits_remaining - 1
            WHERE id = (
                SELECT id FROM system_licenses
                WHERE credits_remaining > 0 AND (valid_until > NOW() OR valid_until IS NULL)
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING credits_remaining, license_key;
            """
        )
        row = cur.fetchone()
        
    pg_conn.commit()
    
    if row:
        rem, key = row[0], row[1]
        print(f"[License Manager] PROD MODE: Deducted 1 credit. Remaining credits: {rem}.")
    else:
        # If we hit this, they somehow completed an investigation with 0 credits.
        print("[License Manager] PROD MODE ALERT: Completed investigation but NO valid credits found to deduct!")
        raise LicenseExhaustedError("Zero compute credits remaining.")


def apply_license_key(pg_conn, signed_jwt: str) -> dict:
    """
    Offline/Air-Gapped license top-up. The client pastes a JWT provided by you.
    You sign the JWT offline with your private key. The OVM verifies it using LICENSE_PUB_KEY.
    """
    if jwt is None:
        raise RuntimeError("PyJWT is not installed; license verification is unavailable.")

    if not LICENSE_PUB_KEY:
        raise ValueError("LICENSE_PUB_KEY not configured on this appliance.")
        
    try:
        # In a real setup, you use algorithms=["RS256"] and a real RSA public key
        # Here we use HS256 for symmetric demonstration if RSA is not provided
        payload = jwt.decode(signed_jwt, LICENSE_PUB_KEY, algorithms=["HS256", "RS256"])
        
        credits_to_add = payload.get("credits", 0)
        valid_until = payload.get("exp_date", None)  # Optional expiration
        
        _ensure_license_table(pg_conn)
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_licenses (license_key, credits_remaining, valid_until, metadata)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (license_key) DO NOTHING
                RETURNING id;
                """,
                (signed_jwt, credits_to_add, valid_until, Json(payload))
            )
            if not cur.fetchone():
                return {"status": "error", "message": "License key already applied."}
        pg_conn.commit()
        
        return {"status": "success", "added": credits_to_add}
        
    except Exception as e:
        if jwt is not None and hasattr(jwt, "ExpiredSignatureError") and isinstance(e, jwt.ExpiredSignatureError):
            raise InvalidLicenseError("This license key has expired.")
        if jwt is not None and hasattr(jwt, "InvalidTokenError") and isinstance(e, jwt.InvalidTokenError):
            raise InvalidLicenseError(f"Cryptographic verification failed: {e}")
        raise InvalidLicenseError(f"Cryptographic verification failed: {e}")
