"""Eligibility/action/deadline classification on real award records."""

import datetime as dt
import tomllib
from pathlib import Path

import pytest

from camfund import central, classify, courses

ROOT = Path(__file__).parent.parent
FX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 10, 20)


@pytest.fixture(scope="module")
def profile():
    return classify.Profile.from_dict(tomllib.loads((ROOT / "config/profile.toml").read_text()))


@pytest.fixture(scope="module")
def cds():
    return {
        "PhD in Astronomy": courses.CourseDates("PhD in Astronomy", "pcaspdpay", "", "Michaelmas 2027", "2026-12-08", "2026-12-08"),
        "PhD in Physics": courses.CourseDates("PhD in Physics", "pcphpdphy", "", "Michaelmas 2027", "2027-05-13", "2026-12-08"),
        "PhD in Applied Mathematics and Theoretical Physics": courses.CourseDates(
            "PhD in Applied Mathematics and Theoretical Physics", "maampdmtp", "", "Michaelmas 2027", "2027-05-13", "2027-01-06"),
    }


def load(slug, **override):
    aw, _ = central.parse_award((FX / f"fund_{slug}.html").read_text(), slug)
    for k, v in override.items():
        setattr(aw, k, v)
    return aw


def test_stfc_confirmed_automatic(profile, cds):
    a = classify.assess(load("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026"), profile, cds, TODAY)
    assert a.verdict == {"Home": "confirmed", "Overseas": "confirmed"}
    assert a.programmes == ["PhD in Astronomy"]
    assert a.action == "automatic"
    assert any("automatically" in n for n in a.action_notes)
    assert (a.deadline, a.deadline_source) == ("2026-12-08", "course funding deadline")


def test_other_department_not_eligible(profile, cds):
    a = classify.assess(load("churchill-college-frank-hahn-studentship-2026"), profile, cds, TODAY)
    assert a.verdict == {"Home": "no", "Overseas": "no"}
    assert not a.relevant and a.subject_scope == "none"


def test_fee_status_split_and_other_condition(profile, cds):
    depts = central.parse_dialog_list((FX / "dialog_departmentsWvDlg_newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026.xml").read_text())
    a = classify.assess(load("newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026", departments=depts),
                        profile, cds, TODAY)
    assert a.verdict == {"Home": "no", "Overseas": "possible"}
    assert any("female" in r for r in a.reasons)
    assert a.action == "portal"
    # the 65-unit list includes IoA, Physics and DAMTP
    assert len(a.programmes) == 3
    assert a.deadline == "2026-12-08"  # earliest of the covered courses' funding deadlines


def test_department_subset(profile, cds):
    aw = load("newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026",
              departments=["Department of Applied Mathematics and Theoretical Physics", "Department of Chemistry"])
    a = classify.assess(aw, profile, cds, TODAY)
    assert a.programmes == ["PhD in Applied Mathematics and Theoretical Physics"]
    assert a.deadline == "2027-01-06"
    aw.departments = ["Faculty of Physics and Chemistry"]  # parent unit covers IoA and Physics
    assert classify.assess(aw, profile, cds, TODAY).programmes == ["PhD in Astronomy", "PhD in Physics"]


def test_course_list_and_nationality(profile, cds):
    full = central.parse_dialog_list((FX / "dialog_coursesWvDlg_gates-cambridge-scholarship-2026.xml").read_text())
    a = classify.assess(load("gates-cambridge-scholarship-2026", courses=full), profile, cds, TODAY)
    assert set(a.programmes) == {"PhD in Astronomy", "PhD in Physics", "PhD in Applied Mathematics and Theoretical Physics"}
    assert a.verdict == {"Home": "possible", "Overseas": "possible"}
    assert any("Nationality" in r for r in a.reasons)
    assert a.deadline == "2026-12-08"  # earliest course funding deadline


def test_unexpanded_list_is_never_confirmed(profile, cds):
    a = classify.assess(load("gates-cambridge-scholarship-2026"), profile, cds, TODAY)  # '279 courses'
    assert "confirmed" not in a.verdict.values()
    assert any("not expanded" in r for r in a.reasons)


def test_members_only_and_separate(profile, cds):
    a = classify.assess(load("trinity-internal-graduate-studentship-2026"), profile, cds, TODAY)
    assert a.subject_scope == "open"
    assert a.verdict == {"Home": "possible", "Overseas": "possible"}
    assert any("Existing members" in r for r in a.reasons)
    assert a.action == "separate"
    assert a.deadline is None and a.deadline_source == "unparsed"


def test_college_preference_note(profile, cds):
    a = classify.assess(load("tunku-abdul-rahman-scholarship-2026"), profile, cds, TODAY)
    assert any("college preference" in n for n in a.action_notes)


def test_closed_and_explicit_date(profile, cds):
    aw = load("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026",
              closes_text="16 Oct 2026", closed=True)
    a = classify.assess(aw, profile, cds, TODAY)
    assert a.verdict == {"Home": "no", "Overseas": "no"}
    assert (a.deadline, a.deadline_source) == ("2026-10-16", "award")


def test_parent_unit_ignored_when_specific_department_listed(profile, cds):
    aw = load("newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026",
              departments=["Department of Physics", "Department of Chemistry", "Faculty of Physics and Chemistry"])
    assert classify.assess(aw, profile, cds, TODAY).programmes == ["PhD in Physics"]  # not Astronomy


@pytest.mark.parametrize("title,other,hint", [
    ("Geoffrey Fisk PhD Studentship in Surgery at Darwin College", None, "other"),
    ("BHF studentships in cardiovascular sciences", "Proposed research projects must be in an area of cardiovascular science", "other"),
    ("Leverhulme Centre for Life in the Universe PhD Studentship", "The research proposal must fall under the remit of the Centre", None),
    ("The Dorothy and Joseph Needham Scholarship", "For students in the sciences.", None),
    ("Christ's College Hamied Postgraduate Studentship (in any subject)", None, None),
    ("Trinity External Research Studentship", None, None),
])
def test_subject_hint(profile, cds, title, other, hint):
    aw = load("trinity-internal-graduate-studentship-2026", title=title, other_eligibility=other, college_note=None)
    a = classify.assess(aw, profile, cds, TODAY)
    assert a.subject_scope == "open" and a.subject_hint == hint


def test_no_additional_form_text_is_not_separate(profile, cds):
    aw = load("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026",
              additional_form="No additional application form or information is required.")
    a = classify.assess(aw, profile, cds, TODAY)
    assert a.action == "automatic" and a.deadline == "2026-12-08"


def test_separate_application_date_overrides_course_deadline(profile, cds):
    aw = load("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026",
              additional_form="Applicants should apply directly to Martingale Foundation by the 18th October 2026",
              requires_additional_application=True)
    a = classify.assess(aw, profile, cds, TODAY)
    assert a.action == "separate"
    assert (a.deadline, a.deadline_source) == ("2026-10-18", "award")


@pytest.mark.parametrize("slug,rule,note_part", [
    ("tunku-abdul-rahman-scholarship-2026", "preference", "1st or 2nd college preference"),
    ("churchill-college-frank-hahn-studentship-2026", "any", "Any college preference"),
    ("newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026", "any", "willing to transfer"),
    ("trinity-internal-graduate-studentship-2026", "member", "existing members"),
    ("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026", None, None),
])
def test_college_rule_from_real_tooltips(profile, cds, slug, rule, note_part):
    a = classify.assess(load(slug), profile, cds, TODAY)
    assert a.college_rule == rule
    if note_part:
        assert any(note_part in n for n in a.action_notes)
    # "NOT required to specify" must never produce a preference instruction
    if rule == "any":
        assert not any("1st or 2nd" in n for n in a.action_notes)


def test_college_rule_accepted():
    assert classify.college_rule("Applicant must have been accepted by this College") == "accepted"
    assert classify.college_rule(None) is None
