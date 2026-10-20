"""Parser tests against real pages saved on 2026-10-20 (tests/fixtures)."""

import datetime as dt
from pathlib import Path

import pytest

from camfund import central, courses

FX = Path(__file__).parent / "fixtures"


def read(name):
    return (FX / name).read_text()


def award(slug):
    aw, dialogs = central.parse_award(read(f"fund_{slug}.html"), slug)
    return aw, dialogs


def test_listing_first_page():
    html = read("search_page1.html")
    cards = central.parse_cards(html)
    assert len(cards) == 10
    assert central.extract_row_count(html) == 206
    assert central.award_year_options(html) == {"2026/27": "CURRENT_YEAR", "2027/28": "NEXT_YEAR"}
    assert central.extract_nonce(html)
    assert all(not c.slug.count(";") for c in cards)


def test_listing_ajax_tail_page():
    cards = central.parse_cards(central.unwrap_partial(read("search_ajax_tail.xml")))
    assert len(cards) == 6  # 206 = 4*50 + 6


def test_card_flags():
    cards = {c.slug: c for c in central.parse_cards(read("search_pcay.html"))}
    croucher = cards["croucher-cambridge-international-scholarship-2026"]
    assert croucher.requires_additional_application
    assert "Nationality" in croucher.eligibility_tags
    lisa = cards["the-lisa-hall-scholarship-2026"]
    assert lisa.closes_text == "30 Apr 2027" and not lisa.closed


def test_stfc_detail():
    aw, dialogs = award("stfc-doctoral-training-partnership-award-institute-of-astronomy-2026")
    assert aw.departments == ["Institute of Astronomy"]
    assert aw.courses == ["PhD in Astronomy"]
    assert aw.fee_status is None and aw.nationality is None
    assert aw.portal_questions == "No"
    assert aw.deadline_text == "Same as funding deadline for the course"
    assert "automatically considered" in aw.about
    assert dialogs == {}


def test_fee_status_and_college_note():
    aw, _ = award("churchill-college-frank-hahn-studentship-2026")
    assert aw.fee_status == ["Home"]
    assert aw.departments == ["Faculty of Economics"]
    assert aw.college == "Churchill College"
    assert "willing to transfer" in aw.college_note
    assert (aw.value_min, aw.value_max) == (1, 100000)


def test_lazy_lists_detected_and_parsed():
    aw, dialogs = award("gates-cambridge-scholarship-2026")
    assert "Course" in dialogs and dialogs["Course"].endswith("coursesWvDlg")
    assert aw.courses == ["279 courses"]
    full = central.parse_dialog_list(read("dialog_coursesWvDlg_gates-cambridge-scholarship-2026.xml"))
    assert len(full) == 279
    assert "PhD in Astronomy" in full and "PhD in Physics" in full

    depts = central.parse_dialog_list(read(
        "dialog_departmentsWvDlg_newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026.xml"))
    assert len(depts) == 65
    assert "Department of Applied Mathematics and Theoretical Physics" in depts


def test_other_eligibility_and_additional_form():
    aw, _ = award("newnham-phd-stem-scholarship-hs-chau-foundation-scholarship-2026")
    assert aw.other_eligibility == "Newnham College is for female students only."
    assert aw.fee_status == ["Overseas"]
    t, _ = award("trinity-internal-graduate-studentship-2026")
    assert t.additional_form.startswith("The IGS application process")
    assert "already be a member" in t.college_note


@pytest.mark.parametrize("text,expected", [
    ("16 Oct 2026", dt.date(2026, 10, 16)),
    ("Closes: 09 Apr 2027", dt.date(2027, 4, 9)),
    ("Dec. 8, 2026", dt.date(2026, 12, 8)),
    ("May. 13, 2027", dt.date(2027, 5, 13)),
    ("deadline is 8th December 2026", dt.date(2026, 12, 8)),
    ("Same as funding deadline for the course", None),
    ("31 Feb 2027", None),
])
def test_parse_date(text, expected):
    assert courses.parse_date(text) == expected


def test_course_pages():
    a = courses.parse_course_page(read("course_pcaspdpay.html"), "PhD in Astronomy", "pcaspdpay", "Michaelmas 2027")
    assert a.application_deadline == "2026-12-08"
    assert a.funding_deadline == "2026-12-08"
    p = courses.parse_course_page(read("course_pcphpdphy.html"), "PhD in Physics", "pcphpdphy", "Michaelmas 2027")
    assert p.application_deadline == "2027-05-13"
    assert p.funding_deadline == "2026-12-08"
    assert courses.intake_for("2027/28") == "Michaelmas 2027"
