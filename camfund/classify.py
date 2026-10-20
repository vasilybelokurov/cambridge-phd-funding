"""Eligibility, required action and deadline for one award against the search profile.

Verdicts (per fee status):

* ``confirmed`` - the database explicitly lists a target department/course (or a
  parent unit), the level and applicant type match, the fee status is allowed, and
  no further restriction (nationality, residence, other conditions, college
  membership) is recorded.
* ``possible`` - the hard criteria are met, but at least one condition needs a human
  check (listed in ``reasons``), OR the award is open to all subjects (no department
  or course restriction listed): absence of a restriction is not treated as proof.
* ``no`` - a hard criterion fails (subject, level, applicant type, fee status) or the
  award is marked closed.

Every verdict is derived from database fields only; terms must still be checked
on the funder's page before acting.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from .central import Award
from .courses import CourseDates, parse_date

SAME_AS_COURSE = re.compile(r"same as (the )?funding deadline", re.I)
AUTO_HINT = re.compile(r"automatically considered|no separate (funding )?application", re.I)
# Heuristics for awards with no formal subject restriction (scope "open"):
SUBJECT_OK = re.compile(
    r"physic|astro|cosmo|mathemat|universe|planet|\bthe sciences|natural sciences|physical sciences|"
    r"exact sciences|\bstem|any (subject|discipline)|all (subjects|disciplines)", re.I)
TITLE_FIELD = re.compile(r"\b(in|of) (?!the University|Cambridge|[A-Z][a-z]+ College)[A-Z][a-z]+")
AREA_CUE = re.compile(r"research (proposal|projects?|area)|undertaking research (in|within)|related to|"
                      r"in the (area|field)s? of|in an area of", re.I)


@dataclass
class Programme:
    course: str
    code: str
    department: str
    parents: list[str] = field(default_factory=list)


@dataclass
class Profile:
    award_year: str
    qualification: str
    fee_statuses: list[str]
    programmes: list[Programme]
    alert_days: list[int] = field(default_factory=lambda: [30, 14, 7, 2])

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(
            award_year=d["award_year"],
            qualification=d.get("qualification", "PHD"),
            fee_statuses=list(d["fee_statuses"]),
            programmes=[Programme(**p) for p in d["programmes"]],
            alert_days=sorted(d.get("alert_days", [30, 14, 7, 2]), reverse=True),
        )


@dataclass
class Assessment:
    programmes: list[str]  # target courses covered
    subject_scope: str  # "explicit" | "open" | "none"
    subject_hint: str | None  # "other" if an open award looks aimed at another field
    verdict: dict[str, str]  # fee status -> confirmed|possible|no
    reasons: list[str]  # why "possible"
    blockers: list[str]  # why "no"
    action: str  # automatic | portal | separate
    action_notes: list[str]
    deadline: str | None  # ISO date, earliest applicable
    deadline_source: str  # "award" | "course funding deadline" | "unparsed" | "none"
    relevant: bool  # any fee status not "no"

    def to_dict(self) -> dict:
        return asdict(self)


_LEVEL = {"PHD": "PhD", "MST": "Masters"}


def _covers(aw: Award, p: Programme) -> bool:
    """True if the award's department/course restrictions admit programme ``p``.

    A parent unit (School/Faculty) counts only when the award lists no specific
    department: the database tags e.g. "Department of Physics" together with its
    parent "Faculty of Physics and Chemistry", which must not admit Astronomy.
    """
    if aw.departments is not None:
        units = set(aw.departments)
        specific = [u for u in units if not u.startswith(("School of", "Faculty of"))]
        if p.department not in units and (specific or not units.intersection(p.parents)):
            return False
    if aw.courses is not None and p.course not in aw.courses:
        return False
    return True


def _unexpanded(items: list[str] | None) -> bool:
    """True if a lazy list was not expanded (we only have '46 departments/schools,')."""
    return bool(items) and len(items) == 1 and re.match(r"^\d+ \w", items[0]) is not None


def assess(aw: Award, profile: Profile, courses: dict[str, CourseDates],
           today: dt.date | None = None) -> Assessment:
    today = today or dt.date.today()
    reasons: list[str] = []
    blockers: list[str] = []

    # --- subject -----------------------------------------------------------
    if _unexpanded(aw.departments) or _unexpanded(aw.courses):
        progs = [p.course for p in profile.programmes]
        scope = "explicit"
        reasons.append("Department/course list not expanded; verify subject coverage")
    else:
        progs = [p.course for p in profile.programmes if _covers(aw, p)]
        scope = "open" if aw.departments is None and aw.courses is None else "explicit"
    if not progs:
        scope = "none"
        blockers.append("Subject: restricted to other departments/courses")
    elif scope == "open":
        reasons.append("Open to all subjects (no department/course restriction listed)")
    subject_hint = None
    if scope == "open":
        text = f"{aw.title} {aw.other_eligibility or ''}"
        if not SUBJECT_OK.search(text) and (TITLE_FIELD.search(aw.title) or AREA_CUE.search(aw.other_eligibility or "")):
            subject_hint = "other"
            reasons.append("Title/conditions suggest a different research area")

    # --- level and applicant type ------------------------------------------
    want_level = _LEVEL.get(profile.qualification, profile.qualification)
    if aw.level and want_level not in aw.level:
        blockers.append(f"Level: {', '.join(aw.level)} only")
    if aw.who_can_apply and "Prospective applicant" not in aw.who_can_apply:
        blockers.append("Current students only")
    if aw.award_year and aw.award_year != profile.award_year:
        blockers.append(f"Award year {aw.award_year}")
    if aw.closed:
        blockers.append(f"Closed ({aw.closes_text})")

    # --- soft restrictions --------------------------------------------------
    if aw.nationality:
        reasons.append(f"Nationality restriction: {aw.nationality}")
    if aw.residence:
        reasons.append(f"Residence restriction: {aw.residence}")
    if aw.other_eligibility:
        reasons.append(f"Other condition: {aw.other_eligibility}")
    elif "Other e.g. research area" in aw.eligibility_tags:
        reasons.append("Database flags an additional restriction (e.g. research area); see funder page")
    note = (aw.college_note or "").lower()
    if "already be a member" in note:
        reasons.append(f"Existing members of {aw.college} only")

    # --- verdict per fee status --------------------------------------------
    verdict = {}
    for fs in profile.fee_statuses:
        if blockers:
            verdict[fs] = "no"
        elif aw.fee_status is not None and fs not in aw.fee_status:
            verdict[fs] = "no"
        elif reasons:
            verdict[fs] = "possible"
        else:
            verdict[fs] = "confirmed"

    # --- action ---------------------------------------------------------------
    notes = []
    if aw.requires_additional_application or aw.additional_form:
        action = "separate"
        if aw.additional_form:
            notes.append(aw.additional_form)
    elif (aw.portal_questions or "").lower() == "yes":
        action = "portal"
        notes.append("Answer the award's questions in the Applicant Portal")
    else:
        action = "automatic"
        if aw.about and AUTO_HINT.search(aw.about):
            notes.append("Funder states eligible applicants are considered automatically")
    if "first or second preference" in note or "required to specify" in note:
        notes.append(f"Name {aw.college} as college preference ({aw.college_note})")
    elif "willing to transfer" in note:
        notes.append(f"Must be willing to transfer to {aw.college}")

    # --- deadline -------------------------------------------------------------
    deadline, source = None, "none"
    texts = [t for t in (aw.closes_text, aw.deadline_text) if t]
    for t in texts:
        d = parse_date(t)
        if d:
            deadline, source = d, "award"
            break
    if deadline is None and any(SAME_AS_COURSE.search(t) for t in texts):
        ds = [dt.date.fromisoformat(courses[c].funding_deadline)
              for c in progs if c in courses and courses[c].funding_deadline]
        if ds:
            deadline, source = min(ds), "course funding deadline"
    elif deadline is None and texts:
        source = "unparsed"

    return Assessment(
        programmes=progs,
        subject_scope=scope,
        subject_hint=subject_hint,
        verdict=verdict,
        reasons=reasons,
        blockers=blockers,
        action=action,
        action_notes=notes,
        deadline=deadline.isoformat() if deadline else None,
        deadline_source=source,
        relevant=any(v != "no" for v in verdict.values()),
    )
