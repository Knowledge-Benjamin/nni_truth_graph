"""
ai_engine/orchestrator/tools/threat_tools.py
─────────────────────────────────────────────────────────────────────────────
Threat Intel / Dark Web Agent Tools.
Handles EMAIL and DOMAIN leads using HaveIBeenPwned and VirusTotal.
"""

import os
import requests
import time

HIBP_API_KEY = os.getenv("HIBP_API_KEY", "")
VIRUSTOTAL_API_KEY = os.getenv("VIRUSTOTAL_API_KEY", "")

def get_hibp_breaches(email: str) -> str:
    """Checks an email against the HaveIBeenPwned API."""
    if not HIBP_API_KEY:
        return f"[HIBP Info] HIBP_API_KEY not configured. Skipping breach check for {email}."
        
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    headers = {
        "hibp-api-key": HIBP_API_KEY,
        "User-Agent": "OSINT-Agent-v1"
    }
    
    try:
        response = requests.get(url, headers=headers, params={"truncateResponse": "false"})
        if response.status_code == 404:
            return f"=== Breach Data for {email} ===\nNo known breaches found.\n"
        elif response.status_code == 200:
            breaches = response.json()
            res = f"=== Breach Data for {email} ===\nFound in {len(breaches)} breaches:\n"
            for b in breaches:
                res += f"- {b.get('Name')} (Date: {b.get('BreachDate')})\n"
                res += f"  Data leaked: {', '.join(b.get('DataClasses', []))}\n"
            return res
        else:
            return f"[HIBP Error] API returned status {response.status_code} for {email}."
    except Exception as e:
        return f"[HIBP Error for {email}]: {e}"

def get_virustotal(target: str, target_type: str) -> str:
    """Checks an IP or DOMAIN against VirusTotal."""
    if not VIRUSTOTAL_API_KEY:
        return f"[VirusTotal Info] VIRUSTOTAL_API_KEY not configured. Skipping scan for {target}."
        
    headers = {
        "x-apikey": VIRUSTOTAL_API_KEY
    }
    
    # Endpoint depends on type
    if target_type == "IP":
        url = f"https://www.virustotal.com/api/v3/ip_addresses/{target}"
    elif target_type == "DOMAIN":
        url = f"https://www.virustotal.com/api/v3/domains/{target}"
    else:
        return ""
        
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get("data", {}).get("attributes", {})
            stats = data.get("last_analysis_stats", {})
            
            res = f"=== VirusTotal Data for {target} ===\n"
            res += f"Malicious flags: {stats.get('malicious', 0)}\n"
            res += f"Suspicious flags: {stats.get('suspicious', 0)}\n"
            res += f"Harmless flags: {stats.get('harmless', 0)}\n"
            res += f"Reputation Score: {data.get('reputation', 0)}\n"
            
            # Extract tags if available
            tags = data.get("tags", [])
            if tags:
                res += f"Tags: {', '.join(tags)}\n"
                
            return res
        else:
            return f"[VirusTotal Error] API returned status {response.status_code} for {target}."
    except Exception as e:
        return f"[VirusTotal Error for {target}]: {e}"
