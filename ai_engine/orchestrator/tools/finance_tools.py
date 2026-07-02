"""
ai_engine/orchestrator/tools/finance_tools.py
─────────────────────────────────────────────────────────────────────────────
Financial / Corporate Agent Tools.
Handles ORGANISATION leads using OpenCorporates and SEC EDGAR.
"""

import os
import requests

OPENCORPORATES_API_KEY = os.getenv("OPENCORPORATES_API_KEY", "")
SEC_EDGAR_USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "OSINT Agent agent@example.com")

def search_opencorporates(company_name: str) -> str:
    """Searches OpenCorporates for a company name."""
    if not OPENCORPORATES_API_KEY:
        return f"[OpenCorporates Info] OPENCORPORATES_API_KEY not configured. Skipping search for {company_name}."
        
    url = "https://api.opencorporates.com/v0.4/companies/search"
    params = {
        "q": company_name,
        "api_token": OPENCORPORATES_API_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            companies = data.get("results", {}).get("companies", [])
            if not companies:
                return f"=== OpenCorporates Data for {company_name} ===\nNo records found.\n"
                
            res = f"=== OpenCorporates Data for {company_name} ===\nFound {len(companies)} potential matches (showing top 3):\n"
            for c in companies[:3]:
                comp = c.get("company", {})
                res += f"\n- Name: {comp.get('name')}\n"
                res += f"  Jurisdiction: {comp.get('jurisdiction_code')}\n"
                res += f"  Status: {comp.get('current_status')}\n"
                res += f"  Incorporation Date: {comp.get('incorporation_date')}\n"
                res += f"  Company Number: {comp.get('company_number')}\n"
                
                address = comp.get('registered_address_in_full')
                if address:
                    res += f"  Address: {address}\n"
            return res
        else:
            return f"[OpenCorporates Error] API returned status {response.status_code}."
    except Exception as e:
        return f"[OpenCorporates Error for {company_name}]: {e}"

def search_edgar(company_name: str) -> str:
    """Searches SEC EDGAR for public company filings (Free, requires User-Agent)."""
    if SEC_EDGAR_USER_AGENT == "OSINT Agent agent@example.com":
         return f"[SEC EDGAR Info] SEC_EDGAR_USER_AGENT uses placeholder. Skipping EDGAR search to prevent blocks."
         
    # To search EDGAR effectively without CIK, we use the company tickers exchange JSON
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {
        "User-Agent": SEC_EDGAR_USER_AGENT
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            tickers = response.json()
            # Simple substring match
            matches = []
            search_name = company_name.lower()
            for key, data in tickers.items():
                if search_name in data.get("title", "").lower():
                    matches.append(data)
                    
            if not matches:
                return f"=== SEC EDGAR Data for {company_name} ===\nNo public SEC records found (not a listed US company or name mismatch).\n"
                
            res = f"=== SEC EDGAR Data for {company_name} ===\nFound {len(matches)} matching US public companies:\n"
            for m in matches[:3]:
                res += f"- Name: {m.get('title')} | Ticker: {m.get('ticker')} | CIK: {m.get('cik_str')}\n"
            return res
        else:
            return f"[SEC EDGAR Error] API returned status {response.status_code}."
    except Exception as e:
        return f"[SEC EDGAR Error for {company_name}]: {e}"
