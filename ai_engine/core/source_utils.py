from urllib.parse import urlparse


def get_domain_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.netloc.lower()


def canonical_source_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path or ""
    return f"{scheme}://{netloc}".rstrip("/")


def resolve_source_for_url(cursor, url: str, name: str | None = None, category: str = "Discovered") -> int | None:
    canonical = canonical_source_url(url)
    domain = get_domain_from_url(canonical)

    cursor.execute(
        """
        INSERT INTO sources (name, url, domain, category, epistemic_trust_score)
        VALUES (%s, %s, %s, %s, 0.50)
        ON CONFLICT (url) DO NOTHING
        RETURNING id;
        """,
        (name or domain or "Discovered Source", canonical, domain, category)
    )
    row = cursor.fetchone()
    if row:
        return row[0]

    cursor.execute("SELECT id FROM sources WHERE url = %s", (canonical,))
    row = cursor.fetchone()
    return row[0] if row else None
