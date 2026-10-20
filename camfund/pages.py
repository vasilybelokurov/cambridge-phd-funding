"""Monitor college and department funding pages that are not in the central database.

Change detection uses only *funding-relevant* lines (those mentioning awards,
deadlines, money or eligibility), so navigation, news tickers and cookie banners do
not raise alerts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from .courses import parse_date

KEYWORDS = re.compile(
    r"studentship|scholarship|bursar|award|grant|funding|fund\b|stipend|deadline|"
    r"closing date|closes|apply|application|£|\bphd\b|doctoral|graduate|postgraduate|"
    r"eligib|home fee|overseas|international",
    re.I,
)
DEADLINE_HINT = re.compile(r"deadline|closing date|closes|close on|by \d|apply by", re.I)
BOILERPLATE = re.compile(r"cookie|javascript|privacy policy|accessibility statement|skip to", re.I)


@dataclass
class PageSnapshot:
    url: str
    status: int
    final_url: str
    title: str
    lines: list[str] = field(default_factory=list)
    deadlines: list[dict] = field(default_factory=list)  # {"date": iso, "context": str}
    digest: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def relevant_lines(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg"]):
        t.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    lines = []
    for block in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "dt", "dd"]):
        if block.find(["p", "li", "td"]):  # take leaf blocks only
            continue
        s = re.sub(r"\s+", " ", block.get_text(" ")).strip()
        if 12 <= len(s) <= 600 and KEYWORDS.search(s) and not BOILERPLATE.search(s):
            lines.append(s)
    seen, out = set(), []
    for s in lines:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return title, out


def snapshot(html: str, url: str, status: int = 200, final_url: str | None = None) -> PageSnapshot:
    title, lines = relevant_lines(html)
    deadlines = []
    for s in lines:
        if DEADLINE_HINT.search(s):
            d = parse_date(s)
            if d:
                deadlines.append({"date": d.isoformat(), "context": s[:200]})
    digest = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    return PageSnapshot(url=url, status=status, final_url=final_url or url, title=title,
                        lines=lines, deadlines=deadlines, digest=digest)


def fetch_page(session, url: str) -> PageSnapshot:
    try:
        r = session.get(url)
    except Exception as e:  # network failure: report, do not crash the run
        return PageSnapshot(url=url, status=0, final_url=url, title="", error=f"{type(e).__name__}: {e}")
    if r.status_code != 200:
        return PageSnapshot(url=url, status=r.status_code, final_url=r.url, title="",
                            error=f"HTTP {r.status_code}")
    snap = snapshot(r.text, url, r.status_code, r.url)
    if not snap.lines:
        snap.error = "No funding-related text found (JS-rendered page or layout change?)"
    return snap


def diff_lines(old: list[str], new: list[str], limit: int = 12) -> tuple[list[str], list[str]]:
    o, n = set(old), set(new)
    added = [s for s in new if s not in o][:limit]
    removed = [s for s in old if s not in n][:limit]
    return added, removed
