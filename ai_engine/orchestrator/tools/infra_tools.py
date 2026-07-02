"""
ai_engine/orchestrator/tools/infra_tools.py
─────────────────────────────────────────────────────────────────────────────
Cyber/Infrastructure Agent Tools.
Handles IP and DOMAIN lead types using python-whois, socket, and Shodan.
"""

import os
import socket
import json
try:
    import whois
except ImportError:
    whois = None
try:
    import shodan
except ImportError:
    shodan = None
try:
    import requests as _req
except ImportError:
    _req = None

SHODAN_API_KEY = os.getenv("SHODAN_API_KEY", "")
CENSYS_API_ID = os.getenv("CENSYS_API_ID", "")
CENSYS_API_SECRET = os.getenv("CENSYS_API_SECRET", "")

def get_whois(domain: str) -> str:
    """Returns WHOIS information for a domain as a formatted string."""
    if not whois:
        return f"[WHOIS Error] python-whois not installed."
    try:
        w = whois.whois(domain)
        # Format the response clearly
        res = f"=== WHOIS Data for {domain} ===\n"
        res += f"Registrar: {w.registrar}\n"
        res += f"Creation Date: {w.creation_date}\n"
        res += f"Expiration Date: {w.expiration_date}\n"
        res += f"Name Servers: {', '.join(w.name_servers) if w.name_servers else 'None'}\n"
        if w.emails:
            emails = w.emails if isinstance(w.emails, list) else [w.emails]
            res += f"Emails found: {', '.join(emails)}\n"
        if w.org:
            res += f"Organization: {w.org}\n"
        return res
    except Exception as e:
        return f"[WHOIS Error for {domain}]: {e}"

def get_dns_resolution(domain: str) -> str:
    """Resolves domain to IP."""
    try:
        ip = socket.gethostbyname(domain)
        return f"=== DNS Data for {domain} ===\nResolved IP: {ip}\n"
    except Exception as e:
        return f"[DNS Error for {domain}]: {e}"

def get_shodan(ip: str) -> str:
    """Returns Shodan data for an IP address."""
    if not SHODAN_API_KEY:
        return (f"[BYOK Alert] SHODAN_API_KEY not configured. "
                f"To enable infrastructure scanning for {ip}, please inject your own Shodan API key "
                f"via the OVM environment variables (Bring Your Own Key mode).")
    if not shodan:
        return f"[Shodan Error] shodan library not installed."
        
    try:
        api = shodan.Shodan(SHODAN_API_KEY)
        host = api.host(ip)
        
        res = f"=== Shodan Data for {ip} ===\n"
        res += f"Organization: {host.get('org', 'n/a')}\n"
        res += f"Operating System: {host.get('os', 'n/a')}\n"
        res += f"Ports: {', '.join(str(p) for p in host.get('ports', []))}\n"
        
        vulns = host.get('vulns', [])
        if vulns:
            res += f"Vulnerabilities: {', '.join(vulns)}\n"
            
        hostnames = host.get('hostnames', [])
        if hostnames:
            res += f"Hostnames: {', '.join(hostnames)}\n"
            
        return res
    except Exception as e:
        return f"[Shodan Error for {ip}]: {e}"

def get_censys(target: str) -> str:
    """Query Censys Search 2.0 API for an IP or domain.
    
    Complements Shodan by providing TLS certificate data, ASN info, and
    additional service banners not always available via Shodan.
    """
    if not CENSYS_API_ID or not CENSYS_API_SECRET:
        return (f"[BYOK Alert] CENSYS_API_ID / CENSYS_API_SECRET not configured. "
                f"To enable certificate and ASN scanning for {target}, please inject your own Censys API credentials "
                f"via the OVM environment variables (Bring Your Own Key mode).")
    if not _req:
        return "[Censys Error] requests library not installed."

    try:
        auth = (CENSYS_API_ID, CENSYS_API_SECRET)
        res = f"=== Censys Data for {target} ===\n"

        # ── Host lookup ────────────────────────────────────────────────────
        host_resp = _req.get(
            f"https://search.censys.io/api/v2/hosts/{target}",
            auth=auth, timeout=15
        )
        if host_resp.status_code == 200:
            data = host_resp.json().get("result", {})
            asn  = data.get("autonomous_system", {})
            res += f"ASN: {asn.get('asn', 'n/a')} ({asn.get('name', 'n/a')})\n"
            res += f"Country: {data.get('location', {}).get('country', 'n/a')}\n"
            svcs = data.get("services", [])
            if svcs:
                ports_seen = ", ".join(
                    f"{s.get('port')}/{s.get('transport_protocol','tcp')}" for s in svcs[:10]
                )
                res += f"Open Services: {ports_seen}\n"
                # Pull TLS cert subjects from first HTTPS service
                for svc in svcs:
                    cert = svc.get("tls", {}).get("certificates", {}).get("leaf_data", {})
                    subject = cert.get("subject", {})
                    if subject:
                        res += f"TLS Certificate Subject: {subject}\n"
                        sans = cert.get("names", [])
                        if sans:
                            res += f"TLS SANs: {', '.join(sans[:10])}\n"
                        break
        elif host_resp.status_code == 404:
            res += "Host not indexed by Censys.\n"
        else:
            res += f"Censys host lookup returned HTTP {host_resp.status_code}.\n"

        # ── Certificate search for domain targets ─────────────────────────
        if not target[0].isdigit():  # likely a domain, not raw IP
            cert_resp = _req.post(
                "https://search.censys.io/api/v2/certificates/search",
                auth=auth,
                json={"q": f"parsed.names: {target}", "per_page": 5},
                timeout=15
            )
            if cert_resp.status_code == 200:
                hits = cert_resp.json().get("result", {}).get("hits", [])
                if hits:
                    res += f"\nCensys Certificates matching '{target}':\n"
                    for hit in hits[:5]:
                        names = hit.get("parsed", {}).get("names", [])
                        issuer = hit.get("parsed", {}).get("issuer", {}).get("organization", ["Unknown"])
                        res += f"  - SAN: {', '.join(names[:5])} | Issuer: {issuer[0] if issuer else 'n/a'}\n"

        return res
    except Exception as e:
        return f"[Censys Error for {target}]: {e}"
