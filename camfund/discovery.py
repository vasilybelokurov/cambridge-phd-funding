"""Optional web-search discovery (Brave Search API) of funding pages on cam.ac.uk.

Enabled only when ``BRAVE_API_KEY`` is set. Hits are reported as unverified
DISCOVERED events, deduplicated against known sources and earlier hits.
API docs: https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)
ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

QUERIES = [
    'site:cam.ac.uk college PhD studentship 2027 physics OR astronomy OR "natural sciences"',
    'site:cam.ac.uk college graduate scholarship 2027-28 STEM',
    'site:cam.ac.uk "doctoral studentship" college 2027',
    "site:ast.cam.ac.uk PhD studentship funding",
    "site:phy.cam.ac.uk PhD studentship funding",
]


def _canonical(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc.lower()}{p.path.rstrip('/') or '/'}"


def search(api_key: str, queries: list[str] = QUERIES, freshness: str = "pm") -> list[dict]:
    """Return [{url, title, description}] for cam.ac.uk hits (``freshness='pm'``: past month)."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "X-Subscription-Token": api_key})
    hits = {}
    for q in queries:
        try:
            r = s.get(ENDPOINT, params={"q": q, "count": 20, "freshness": freshness}, timeout=30)
            r.raise_for_status()
        except Exception as e:
            log.warning("Brave query failed (%s): %s", q, e)
            continue
        for res in r.json().get("web", {}).get("results", []):
            url = res.get("url", "")
            host = urlparse(url).netloc.lower()
            if not (host == "cam.ac.uk" or host.endswith(".cam.ac.uk")):
                continue
            if "student-funding.cam.ac.uk" in host:  # covered by the database scraper
                continue
            hits.setdefault(_canonical(url), {"url": _canonical(url), "title": res.get("title", ""),
                                              "description": res.get("description", "")})
    return list(hits.values())
