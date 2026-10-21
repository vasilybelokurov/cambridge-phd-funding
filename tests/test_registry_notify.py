"""Change detection, reminders, page diffs, outbox delivery and dashboard output."""

import copy
import datetime as dt
import json

import pytest

from camfund import notify, pages, registry, report

D0 = dt.date(2026, 10, 20)


def rec(slug="a-2026", **kw):
    r = {"slug": slug, "url": f"https://x/fund/{slug}", "title": f"Award {slug}", "funder": "Some College",
         "value_text": "£20,000", "fee_status": None, "deadline_text": "Same as funding deadline for the course",
         "closes_text": "Same as funding deadline for the course", "closed": False}
    r.update(kw)
    return r


def ass(relevant=True, deadline="2026-12-08", **kw):
    a = {"programmes": ["PhD in Astronomy"], "subject_scope": "explicit",
         "verdict": {"Home": "confirmed" if relevant else "no", "Overseas": "confirmed" if relevant else "no"},
         "subject_hint": None, "college_rule": None, "reasons": [], "blockers": [], "action": "automatic", "action_notes": [], "deadline": deadline,
         "deadline_source": "course funding deadline", "relevant": relevant}
    a.update(kw)
    return a


def run(reg, records, assessments, today):
    return registry.update_awards(reg, records, assessments, today, [30, 14, 7, 2])


def test_new_then_idempotent_then_changed():
    reg = registry.load("/nonexistent/registry.json")
    r1, r2 = rec("a-2026"), rec("b-2026")
    ev = run(reg, [r1, r2], {"a-2026": ass(), "b-2026": ass(relevant=False)}, D0)
    assert [e["type"] for e in ev] == ["NEW"]  # irrelevant award is stored but not announced
    assert set(reg["awards"]) == {"a-2026", "b-2026"}

    ev = run(reg, [r1, r2], {"a-2026": ass(), "b-2026": ass(relevant=False)}, D0 + dt.timedelta(1))
    assert ev == []  # unchanged second run produces nothing

    r1b = dict(r1, value_text="£25,000")
    ev = run(reg, [r1b, r2], {"a-2026": ass(), "b-2026": ass(relevant=False)}, D0 + dt.timedelta(2))
    assert [e["type"] for e in ev] == ["CHANGED"]
    assert "£20,000 → £25,000" in ev[0]["details"][0]

    # irrelevant award changing stays silent; becoming relevant is announced as NEW
    ev = run(reg, [r1b, dict(r2, value_text="x")], {"a-2026": ass(), "b-2026": ass(relevant=False)}, D0 + dt.timedelta(3))
    assert ev == []
    ev = run(reg, [r1b, r2], {"a-2026": ass(), "b-2026": ass()}, D0 + dt.timedelta(4))
    assert [e["type"] for e in ev] == ["NEW"]


def test_missing_needs_two_runs():
    reg = registry.load("/nonexistent")
    run(reg, [rec()], {"a-2026": ass()}, D0)
    assert run(reg, [], {}, D0 + dt.timedelta(1)) == []
    ev = run(reg, [], {}, D0 + dt.timedelta(2))
    assert [e["type"] for e in ev] == ["MISSING"]
    assert run(reg, [], {}, D0 + dt.timedelta(3)) == []  # fires once
    ev = run(reg, [rec()], {"a-2026": ass()}, D0 + dt.timedelta(4))
    assert ev[0]["type"] == "CHANGED" and "Reappeared" in ev[0]["details"][0]


def test_deadline_reminders_fire_once_per_threshold():
    reg = registry.load("/nonexistent")
    run(reg, [rec()], {"a-2026": ass()}, D0)  # 49 days out; initial import: no reminders
    fired = []
    for k in range(0, 50):
        day = D0 + dt.timedelta(k)
        ev = run(reg, [rec()], {"a-2026": ass()}, day)
        fired += [((dt.date(2026, 12, 8) - day).days) for e in ev if e["type"] == "DEADLINE_SOON"]
    assert fired == [30, 14, 7, 2]


def test_initial_import_inside_window_does_not_spam():
    reg = registry.load("/nonexistent")
    ev = run(reg, [rec()], {"a-2026": ass(deadline="2026-10-25")}, D0)  # 5 days out at import
    assert [e["type"] for e in ev] == ["NEW"]
    ev = run(reg, [rec()], {"a-2026": ass(deadline="2026-10-25")}, D0 + dt.timedelta(3))  # 2 days
    assert [e["type"] for e in ev] == ["DEADLINE_SOON"]


HTML_V1 = """<html><head><title>Graduate funding</title></head><body><nav>Home Funding</nav><main>
<h2>PhD Studentships</h2><p>The Smith Studentship covers fees and a stipend of £20,000 for PhD students.</p>
<p>Applications close on 15 January 2027.</p><p>Our gardens are lovely in spring.</p>
<footer>cookie policy</footer></main></body></html>"""


def test_page_snapshot_and_change():
    s1 = pages.snapshot(HTML_V1, "https://c/funding")
    assert len(s1.lines) == 3 and "gardens" not in " ".join(s1.lines)
    assert s1.deadlines == [{"date": "2027-01-15", "context": "Applications close on 15 January 2027."}]
    # irrelevant edit -> same digest
    s1b = pages.snapshot(HTML_V1.replace("lovely", "beautiful"), "https://c/funding")
    assert s1b.digest == s1.digest
    s2 = pages.snapshot(HTML_V1.replace("15 January 2027", "29 January 2027"), "https://c/funding")
    reg = registry.load("/nonexistent")
    assert registry.update_pages(reg, [("X College", s1.to_dict())], D0) == []
    assert registry.update_pages(reg, [("X College", s1b.to_dict())], D0) == []
    ev = registry.update_pages(reg, [("X College", s2.to_dict())], D0)
    assert ev[0]["type"] == "PAGE_CHANGED" and "2027-01-29" in ev[0]["summary"]


def test_source_error_after_two_failures():
    reg = registry.load("/nonexistent")
    bad = pages.PageSnapshot(url="https://c/x", status=403, final_url="https://c/x", title="", error="HTTP 403").to_dict()
    assert registry.update_pages(reg, [("C", bad)], D0) == []
    ev = registry.update_pages(reg, [("C", bad)], D0)
    assert [e["type"] for e in ev] == ["SOURCE_ERROR"]


class FakeGH:
    def __init__(self, fail=False):
        self.issues, self.comments, self.labels, self.fail = {}, [], {}, fail

    def ensure_labels(self, labels):
        pass

    def index_issues(self):
        return {body[body.find("<!--"):]: n for n, (t, body, l) in self.issues.items()}

    def create(self, title, body, labels):
        if self.fail:
            raise RuntimeError("boom")
        n = len(self.issues) + 1
        self.issues[n] = (title, body, labels)
        return n

    def comment(self, n, body):
        self.comments.append((n, body))

    def add_labels(self, n, labels):
        self.labels.setdefault(n, []).extend(labels)

    def edit(self, n, **f):
        t, b, l = self.issues[n]
        self.issues[n] = (t, f.get("body", b), l)


def test_issue_delivery_dedup_and_retry():
    reg = registry.load("/nonexistent")
    reg["outbox"] += run(reg, [rec()], {"a-2026": ass()}, D0)
    gh_bad = FakeGH(fail=True)
    assert notify.deliver_issues(reg, gh_bad) == 0
    assert reg["outbox"][0]["sent_issue"] is False  # kept for retry
    gh = FakeGH()
    assert notify.deliver_issues(reg, gh) == 1 and len(gh.issues) == 1
    assert reg["awards"]["a-2026"]["issue"] == 1
    # a change -> comment on the same issue, not a new issue
    reg["outbox"] += run(reg, [rec(value_text="£30,000")], {"a-2026": ass()}, D0 + dt.timedelta(1))
    notify.deliver_issues(reg, gh)
    assert len(gh.issues) == 1 and len(gh.comments) == 1 and "£30,000" in gh.comments[0][1]
    assert "£30,000" in gh.issues[1][1]  # body refreshed
    notify.prune_outbox(reg, D0 + dt.timedelta(1), issues_enabled=True, email_enabled=False)
    assert reg["outbox"] == []


def test_email_failure_keeps_outbox(monkeypatch):
    reg = registry.load("/nonexistent")
    reg["outbox"] += run(reg, [rec()], {"a-2026": ass()}, D0)

    def boom(*a, **k):
        raise OSError("smtp down")

    monkeypatch.setattr(notify, "send_email", boom)
    with pytest.raises(OSError):
        notify.deliver_email(reg, {"x": 1})
    notify.prune_outbox(reg, D0, issues_enabled=False, email_enabled=True)
    assert len(reg["outbox"]) == 1 and reg["outbox"][0]["sent_email"] is False

    sent = []
    monkeypatch.setattr(notify, "send_email", lambda cfg, s, t, h: sent.append(s))
    assert notify.deliver_email(reg, {"x": 1}) == 1
    notify.prune_outbox(reg, D0, issues_enabled=False, email_enabled=True)
    assert reg["outbox"] == [] and sent[0].startswith("Cambridge PhD funding: 1 new award")


def test_dashboard_and_csv(tmp_path):
    reg = registry.load("/nonexistent")
    run(reg, [rec(title="A </script> award")], {"a-2026": ass()}, D0)
    report.write_dashboard(reg, tmp_path / "index.html")
    html = (tmp_path / "index.html").read_text()
    assert "const D=null" not in html and "<\\/script>" in html
    payload = html.split("const D=", 1)[1].split(";\nconst $", 1)[0]
    assert json.loads(payload.replace("<\\/", "</"))["awards"][0]["title"] == "A </script> award"
    report.write_csv(reg, tmp_path / "a.csv")
    assert "Home: confirmed" in (tmp_path / "a.csv").read_text()


def test_save_roundtrip(tmp_path):
    reg = registry.load("/nonexistent")
    run(reg, [rec()], {"a-2026": ass()}, D0)
    registry.save(reg, tmp_path / "r.json")
    assert registry.load(tmp_path / "r.json") == json.loads(json.dumps(copy.deepcopy(reg)))
