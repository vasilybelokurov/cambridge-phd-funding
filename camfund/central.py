"""Scraper for the University Postgraduate Funding Search (student-funding.cam.ac.uk).

The site is a PrimeFaces/JSF application. Facts established by probing it
(October 2026):

* ``GET /?awardYear=NEXT_YEAR&student=APPLICANT&qualification=PHD`` renders the
  first 10 result cards server-side, plus ``rowCount:<N>`` in an inline script.
* Further pages come from an AJAX POST to the form action with the paginator
  parameters. The POST **must** carry ``primefaces.nonce`` (from
  ``PrimeFaces.csp.init('<nonce>')``); without it the server truncates the
  response after the first card.
* ``departments=<CODE>`` (e.g. ``PCAY`` Institute of Astronomy) filters to awards
  that explicitly list that department. Awards open to all subjects are NOT
  returned by that filter, so we enumerate the full result set instead.
* Award detail pages ``/fund/<slug>`` hold the structured facets (Award details,
  Eligibility, Application Process, Key dates). Long department/course lists are
  "See full list" dialogs whose content is loaded lazily via
  ``<dialogId>_contentLoad=true``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from bs4 import BeautifulSoup, Tag

BASE = "https://www.student-funding.cam.ac.uk"
AJAX_HEADERS = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}
PAGE_ROWS = 50  # allowed paginator values: 10, 25, 50


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------


@dataclass
class Card:
    """One result card from the search listing."""

    slug: str
    funder: str
    title: str
    value_text: str
    closes_text: str
    closed: bool
    eligibility_tags: list[str]
    requires_additional_application: bool


@dataclass
class Award:
    """Structured record parsed from an award detail page."""

    slug: str
    url: str
    funder: str
    title: str
    website: str | None = None
    value_text: str | None = None
    value_min: int | None = None
    value_max: int | None = None
    purpose: list[str] = field(default_factory=list)
    duration: str | None = None
    n_awards: str | None = None
    award_year: str | None = None
    basis: str | None = None
    who_can_apply: list[str] = field(default_factory=list)
    level: list[str] = field(default_factory=list)
    fee_status: list[str] | None = None  # None = no fee-status restriction listed
    nationality: str | None = None  # summary text, e.g. "Malaysia" or "255 nationalities"
    residence: str | None = None
    departments: list[str] | None = None  # None = no department restriction listed
    courses: list[str] | None = None  # None = no course restriction listed
    college: str | None = None
    college_note: str | None = None
    other_eligibility: str | None = None
    portal_questions: str | None = None
    additional_form: str | None = None
    deadline_text: str | None = None
    results_date: str | None = None
    last_updated: str | None = None
    about: str | None = None
    # Copied from the listing card (not on the detail page in structured form)
    closes_text: str | None = None
    closed: bool = False
    eligibility_tags: list[str] = field(default_factory=list)
    requires_additional_application: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _strip_session(href: str) -> str:
    return href.split(";", 1)[0].split("?", 1)[0]


def extract_nonce(html: str) -> str:
    m = re.search(r"PrimeFaces\.csp\.init\('([^']+)'\)", html)
    if not m:
        raise ValueError("PrimeFaces CSP nonce not found")
    return m.group(1)


def extract_row_count(html: str) -> int | None:
    m = re.search(r"rowCount:(\d+)", html)
    return int(m.group(1)) if m else None


def _form_state(html: str) -> tuple[str, str, str]:
    """Return (form action, ViewState, nonce) for the main ``rf`` form."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="rf")
    if form is None:
        raise ValueError("form#rf not found")
    vs = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    if vs is None:
        raise ValueError("javax.faces.ViewState not found")
    return form["action"], vs["value"], extract_nonce(html)


def unwrap_partial(xml: str) -> str:
    """Concatenate the CDATA payloads of a JSF partial-response."""
    return "".join(re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml, flags=re.S))


def award_year_options(html: str) -> dict[str, str]:
    """Map award-year labels to form values, e.g. {'2027/28': 'NEXT_YEAR'}."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for inp in soup.find_all("input", attrs={"name": "rf:awardYear"}):
        lab = soup.find("label", attrs={"for": inp.get("id")})
        m = re.search(r"(\d{4}/\d{2})", lab.get_text() if lab else "")
        if m:
            out[m.group(1)] = inp["value"]
    return out


# ----------------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------------


def parse_cards(html: str) -> list[Card]:
    """Parse result cards from a full page or an unwrapped AJAX fragment."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for c in soup.select(".result-card"):
        a = c.select_one("a[href^='/fund/']")
        if a is None:
            continue
        vals = [_clean(v.get_text(" ", strip=True)) for v in c.select(".rc-val")]
        closes = next((v for v in vals if v.startswith(("Closes:", "Closed:"))), "")
        tags = []
        for v in c.select(".rc-val"):
            if _clean(v.get_text()).startswith("Eligibility:"):
                tags = [_clean(t.get_text()) for t in v.select(".pgf-tag")]
        value = c.select_one(".rc-value")
        cards.append(
            Card(
                slug=_strip_session(a["href"])[len("/fund/"):],
                funder=_clean(c.select_one(".rc-funder").get_text()),
                title=_clean(c.select_one(".rc-fund").get_text()),
                value_text=_clean(value.get_text(" ")) if value else "",
                closes_text=re.sub(r"^Close[sd]:\s*", "", closes),
                closed=closes.startswith("Closed:"),
                eligibility_tags=tags,
                requires_additional_application=any(
                    "Require additional application" in v for v in vals
                ),
            )
        )
    return cards


def search_params(award_year_value: str, qualification: str = "PHD", **extra) -> dict:
    return {
        "awardYear": award_year_value,
        "student": "APPLICANT",
        "qualification": qualification,
        **extra,
    }


def fetch_all_cards(session, award_year: str = "2027/28", qualification: str = "PHD") -> list[Card]:
    """Enumerate every result card for prospective applicants in ``award_year``.

    Raises
    ------
    RuntimeError
        If the number of unique cards differs from the server-reported rowCount.
    """
    r = session.get(BASE + "/")
    r.raise_for_status()
    years = award_year_options(r.text)
    if award_year not in years:
        raise RuntimeError(f"award year {award_year} not offered; site offers {sorted(years)}")
    r = session.get(BASE + "/", params=search_params(years[award_year], qualification))
    r.raise_for_status()
    total = extract_row_count(r.text)
    cards = parse_cards(r.text)
    if total is None:  # <= 10 results: no paginator
        return cards
    action, viewstate, nonce = _form_state(r.text)
    cards = []
    for first in range(0, total, PAGE_ROWS):
        data = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "rf:searchResults",
            "javax.faces.partial.execute": "rf:searchResults",
            "javax.faces.partial.render": "rf:searchResults",
            "rf:searchResults": "rf:searchResults",
            "rf:searchResults_pagination": "true",
            "rf:searchResults_first": str(first),
            "rf:searchResults_rows": str(PAGE_ROWS),
            "rf:searchResults_encodeFeature": "true",
            "rf": "rf",
            "javax.faces.ViewState": viewstate,
            "primefaces.nonce": nonce,
        }
        p = session.post(BASE + action, data=data, headers=AJAX_HEADERS)
        p.raise_for_status()
        cards.extend(parse_cards(unwrap_partial(p.text)))
    unique = {c.slug: c for c in cards}
    if len(unique) != total:
        raise RuntimeError(f"pagination mismatch: got {len(unique)} unique cards, expected {total}")
    return list(unique.values())


# ----------------------------------------------------------------------------
# Detail page
# ----------------------------------------------------------------------------


def _val_items(val: Tag) -> list[str]:
    """Values of a facet row: tags if present, otherwise text fragments."""
    tags = [_clean(t.get_text()) for t in val.select(".pgf-tag")]
    if tags:
        return tags
    v = BeautifulSoup(str(val), "html.parser")
    for junk in v.select(".ui-dialog, .ui-tooltip, script"):
        junk.decompose()
    return [_clean(s) for s in v.get_text("\n").split("\n") if _clean(s)]


def _tooltip(val: Tag) -> str | None:
    t = val.select_one(".ui-tooltip-text")
    return _clean(t.get_text()) if t else None


def _money(s: str) -> list[int]:
    return [int(x.replace(",", "")) for x in re.findall(r"£\s*([\d,]+)", s)]


def parse_award(html: str, slug: str) -> tuple[Award, dict[str, str]]:
    """Parse a ``/fund/<slug>`` page.

    Returns
    -------
    award : Award
    dialogs : dict
        Facet label -> dialog element id, for rows whose full list is lazy-loaded
        ("See full list"), e.g. {'Department / School': 'rf:j_idt589:departmentsWvDlg'}.
    """
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    title = soup.select_one(".fdt-fund")
    funder = soup.select_one(".fdt-funder")
    if title is None:
        raise ValueError(f"{slug}: not an award page")
    website = next(
        (a["href"] for a in soup.find_all("a", href=True) if "Visit website" in a.get_text()),
        None,
    )
    aw = Award(
        slug=slug,
        url=f"{BASE}/fund/{slug}",
        funder=_clean(funder.get_text()) if funder else "",
        title=_clean(title.get_text()),
        website=website,
    )
    dialogs: dict[str, str] = {}
    about_parts = []
    for fac in soup.select(".facet"):
        ft = fac.select_one(".fct-title")
        ftitle = _clean(ft.get_text()) if ft else ""
        rows = fac.select(".lbl")
        if not rows:
            if ftitle in ("About", ""):
                about_parts.append(_clean(fac.get_text(" ")))
            continue
        for lbl in rows:
            key = _clean(lbl.get_text(" "))
            val = lbl.find_next_sibling(class_="val")
            if val is None:
                continue
            items = _val_items(val)
            text = "; ".join(items)
            if "See full list" in val.get_text():
                dlg = val.select_one(".ui-dialog[id]")
                if dlg is not None:
                    dialogs[key] = dlg["id"]
                items = [i.rstrip(",") for i in items if i not in ("See full list", key)]
                text = items[0].rstrip(",") if items else text
            elif items and items[-1] == key:  # dialog title echoed in text
                items = items[:-1]
                text = "; ".join(items)
            _assign(aw, ftitle, key, items, text, val)
    upd = soup.find(string=re.compile(r"Last updated:"))
    if upd:
        aw.last_updated = _clean(str(upd)).replace("Last updated:", "").strip()
    about = soup.select_one(".ab-content")
    if about is not None:
        about_parts = [_clean(about.get_text(" "))]
    aw.about = " ".join(p for p in about_parts if p)[:3000] or None
    return aw, dialogs


def _assign(aw: Award, facet: str, key: str, items: list[str], text: str, val: Tag) -> None:
    if key == "Award value":
        if facet != "Award details" and aw.value_text:
            return
        aw.value_text = " ".join(items)
        m = _money(aw.value_text)
        if m:
            aw.value_min, aw.value_max = min(m), max(m)
    elif key == "Award purpose" and facet == "Award details":
        aw.purpose = items
    elif key == "Award duration" and facet == "Award details":
        aw.duration = " ".join(items)
    elif key == "No. of awards":
        aw.n_awards = text
    elif key == "Award year":
        aw.award_year = text
    elif key == "Basis of award grant":
        aw.basis = text
    elif facet == "Eligibility":
        if key == "Who can apply?":
            aw.who_can_apply = items
        elif key == "Level of study":
            aw.level = items
        elif key == "Fee status":
            aw.fee_status = items
        elif key == "Nationality":
            aw.nationality = text
        elif key == "Country of residence":
            aw.residence = text
        elif key == "Department / School":
            aw.departments = items
        elif key == "Course":
            aw.courses = items
        elif key == "College":
            aw.college = items[0] if items else None
            aw.college_note = _tooltip(val)
            if aw.college_note and aw.college_note in (aw.college or ""):
                aw.college = aw.college.replace(aw.college_note, "").strip()
        elif key == "Other eligibility":
            aw.other_eligibility = text
    elif key.startswith("Additional questions"):
        aw.portal_questions = text
    elif key == "Additional form":
        aw.additional_form = text
    elif key == "Application deadline" and facet == "Key dates":
        aw.deadline_text = text
    elif key == "Application results date" and facet == "Key dates":
        aw.results_date = " ".join(items)


def parse_dialog_list(xml: str) -> list[str]:
    """Items of a lazily loaded "See full list" dialog."""
    soup = BeautifulSoup(unwrap_partial(xml), "html.parser")
    return [_clean(li.get_text()) for li in soup.select("li") if _clean(li.get_text())]


def fetch_award(session, slug: str, expand: Iterable[str] = ("Department / School", "Course")) -> Award:
    """Fetch and parse one award page, expanding lazy lists named in ``expand``."""
    r = session.get(f"{BASE}/fund/{slug}")
    r.raise_for_status()
    aw, dialogs = parse_award(r.text, slug)
    wanted = {k: v for k, v in dialogs.items() if k in expand}
    if wanted:
        action, viewstate, nonce = _form_state(r.text)
        for key, dlg in wanted.items():
            data = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": dlg,
                "javax.faces.partial.execute": dlg,
                "javax.faces.partial.render": dlg,
                dlg + "_contentLoad": "true",
                "rf": "rf",
                "javax.faces.ViewState": viewstate,
                "primefaces.nonce": nonce,
            }
            p = session.post(BASE + action, data=data, headers=AJAX_HEADERS)
            p.raise_for_status()
            items = parse_dialog_list(p.text)
            if not items:
                raise RuntimeError(f"{slug}: empty '{key}' dialog")
            if key == "Department / School":
                aw.departments = items
            elif key == "Course":
                aw.courses = items
    return aw


def merge_card(aw: Award, card: Card) -> Award:
    aw.closes_text = card.closes_text
    aw.closed = card.closed
    aw.eligibility_tags = card.eligibility_tags
    aw.requires_additional_application = card.requires_additional_application
    if not aw.value_text and card.value_text:
        aw.value_text = card.value_text
    return aw
