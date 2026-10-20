"""Course-level deadlines from the Postgraduate Course Directory.

Each course page (e.g. https://www.postgraduate.study.cam.ac.uk/courses/directory/pcaspdpay)
lists, per intake term, "Application deadline <Mon. D, YYYY>" and a "Course Funding
Deadline <Mon. D, YYYY>". Awards whose deadline is "Same as funding deadline for the
course" inherit the latter.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup

COURSE_URL = "https://www.postgraduate.study.cam.ac.uk/courses/directory/{code}"

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(text: str) -> dt.date | None:
    """Parse the first date in ``text``.

    Accepts '8 Dec 2026', '08 December 2026', 'Dec. 8, 2026', 'May. 13, 2027',
    'December 8 2026'. Returns None if no date is found.
    """
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", text)
    if m and m.group(2)[:3].lower() in _MONTHS:
        d, mon, y = m.groups()
    else:
        m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text)
        if not m or m.group(1)[:3].lower() not in _MONTHS:
            return None
        mon, d, y = m.groups()
    try:
        return dt.date(int(y), _MONTHS[mon[:3].lower()], int(d))
    except ValueError:
        return None


@dataclass
class CourseDates:
    name: str
    code: str
    url: str
    intake: str  # e.g. "Michaelmas 2027"
    application_deadline: str | None  # ISO date
    funding_deadline: str | None  # ISO date

    def to_dict(self) -> dict:
        return asdict(self)


def intake_for(award_year: str) -> str:
    """'2027/28' -> 'Michaelmas 2027'."""
    return f"Michaelmas {award_year[:4]}"


def parse_course_page(html: str, name: str, code: str, intake: str) -> CourseDates:
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    app = fund = None
    i = text.find(intake + " ")
    if i >= 0:
        seg = text[i: i + 300]
        m = re.search(r"Application deadline\s+(.{6,20}?\d{4})", seg)
        if m:
            app = parse_date(m.group(1))
    m = re.search(r"Course Funding Deadline\s+(.{6,20}?\d{4})", text)
    if m:
        fund = parse_date(m.group(1))
    return CourseDates(
        name=name,
        code=code,
        url=COURSE_URL.format(code=code),
        intake=intake,
        application_deadline=app.isoformat() if app else None,
        funding_deadline=fund.isoformat() if fund else None,
    )


def fetch_courses(session, courses: dict[str, str], award_year: str) -> dict[str, CourseDates]:
    """Fetch deadlines for ``{course name: directory code}``."""
    out = {}
    intake = intake_for(award_year)
    for name, code in courses.items():
        r = session.get(COURSE_URL.format(code=code))
        r.raise_for_status()
        out[name] = parse_course_page(r.text, name, code, intake)
    return out
