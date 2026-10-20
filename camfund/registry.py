"""Persistent JSON registry and change detection.

Layout of ``data/registry.json``::

    {
      "meta":   {"last_run": iso, "award_year": "2027/28", "runs": int},
      "awards": {slug: {"record": {...Award...}, "assessment": {...},
                        "first_seen": iso, "last_seen": iso, "missing_runs": int,
                        "status": "active"|"missing", "alerts_sent": [int], "issue": int|null}},
      "courses": {name: {...CourseDates...}},
      "pages":  {url: {"name": str, "snapshot": {...}, "fail_runs": int, "last_changed": iso}},
      "discovered": {url: {"first_seen": iso, "title": str}},
      "outbox": [event, ...]          # events not yet delivered to every channel
    }

An *event* is a dict with ``id`` (stable, for dedup), ``type``, ``key`` (award slug or
URL), ``title``, ``summary``, ``details`` (list of str), ``url``, ``date`` and the
per-channel delivery flags ``sent_issue`` / ``sent_email``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

# Fields whose change is reported to the user (others, e.g. last_updated, are ignored).
TRACKED_RECORD = [
    "title", "value_text", "fee_status", "nationality", "residence", "departments",
    "courses", "college", "college_note", "other_eligibility", "portal_questions",
    "additional_form", "deadline_text", "closes_text", "closed",
    "requires_additional_application", "n_awards", "duration",
]
TRACKED_ASSESSMENT = ["verdict", "action", "deadline", "programmes"]
MISSING_RUNS_FOR_ALERT = 2  # listing absences before a "possibly withdrawn" alert
FAIL_RUNS_FOR_ALERT = 2


def load(path: str | Path) -> dict:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return {"meta": {"runs": 0}, "awards": {}, "courses": {}, "pages": {}, "discovered": {}, "outbox": []}


def save(reg: dict, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False, sort_keys=True))
    os.replace(tmp, p)


def _short(v, n: int = 160) -> str:
    if isinstance(v, list):
        s = "; ".join(map(str, v)) if len(v) <= 6 else f"{len(v)} items"
    elif isinstance(v, dict):
        s = ", ".join(f"{k}: {x}" for k, x in v.items())
    else:
        s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def event(etype: str, key: str, title: str, summary: str, details: list[str], url: str,
          today: dt.date, salt: str = "") -> dict:
    eid = hashlib.sha1(f"{etype}|{key}|{today.isoformat()}|{salt}".encode()).hexdigest()[:12]
    return {"id": eid, "type": etype, "key": key, "title": title, "summary": summary,
            "details": details, "url": url, "date": today.isoformat(),
            "sent_issue": False, "sent_email": False}


def verdict_label(v: dict) -> str:
    return ", ".join(f"{fs}: {x}" for fs, x in v.items())


def update_awards(reg: dict, awards: list[dict], assessments: dict[str, dict],
                  today: dt.date, alert_days: list[int]) -> list[dict]:
    """Merge one run's awards into the registry; return new events.

    Parameters
    ----------
    awards : list of Award dicts from this run (complete listing).
    assessments : slug -> Assessment dict.
    """
    events = []
    seen = set()
    first_run = not reg["awards"]
    for rec in awards:
        slug = rec["slug"]
        seen.add(slug)
        a = assessments[slug]
        entry = reg["awards"].get(slug)
        if entry is None:
            reg["awards"][slug] = {
                "record": rec, "assessment": a, "first_seen": today.isoformat(),
                "last_seen": today.isoformat(), "missing_runs": 0, "status": "active",
                "alerts_sent": [], "issue": None,
            }
            if a["relevant"]:
                events.append(event(
                    "NEW", slug, rec["title"],
                    f"{rec['funder']} · {verdict_label(a['verdict'])} · action: {a['action']}"
                    + (f" · deadline {a['deadline']}" if a["deadline"] else ""),
                    ([] if not first_run else ["(initial import)"]) + a["reasons"] + a["action_notes"],
                    rec["url"], today))
            continue
        old_rec, old_a = entry["record"], entry["assessment"]
        changes = [f"{f}: {_short(old_rec.get(f))} → {_short(rec.get(f))}"
                   for f in TRACKED_RECORD if old_rec.get(f) != rec.get(f)]
        changes += [f"{f}: {_short(old_a.get(f))} → {_short(a.get(f))}"
                    for f in TRACKED_ASSESSMENT if old_a.get(f) != a.get(f)]
        was_relevant = old_a.get("relevant", False)
        if entry["status"] == "missing":
            changes.insert(0, "Reappeared in the funding database")
        if changes and (a["relevant"] or was_relevant):
            etype = "NEW" if a["relevant"] and not was_relevant else "CHANGED"
            events.append(event(etype, slug, rec["title"],
                                f"{rec['funder']} · {verdict_label(a['verdict'])}",
                                changes, rec["url"], today, salt="|".join(changes)))
        if old_a.get("deadline") != a.get("deadline"):
            entry["alerts_sent"] = []  # re-arm reminders for a moved deadline
        entry.update(record=rec, assessment=a, last_seen=today.isoformat(),
                     missing_runs=0, status="active")

    for slug, entry in reg["awards"].items():
        if slug in seen:
            continue
        entry["missing_runs"] += 1
        if entry["missing_runs"] == MISSING_RUNS_FOR_ALERT and entry["assessment"].get("relevant"):
            entry["status"] = "missing"
            rec = entry["record"]
            events.append(event("MISSING", slug, rec["title"],
                                "No longer listed in the University funding database: possibly closed or withdrawn",
                                [f"Last seen {entry['last_seen']}"], rec["url"], today))

    # Deadline reminders: the smallest threshold not yet sent that `days` has crossed.
    for slug, entry in reg["awards"].items():
        a = entry["assessment"]
        if entry["status"] != "active" or not a.get("relevant") or not a.get("deadline"):
            continue
        days = (dt.date.fromisoformat(a["deadline"]) - today).days
        if days < 0:
            continue
        crossed = [d for d in alert_days if days <= d and d not in entry["alerts_sent"]]
        if crossed and not first_run:
            entry["alerts_sent"].extend(crossed)
            rec = entry["record"]
            events.append(event("DEADLINE_SOON", slug, rec["title"],
                                f"Deadline {a['deadline']} ({days} days) · {a['deadline_source']}",
                                a["action_notes"], rec["url"], today, salt=str(min(crossed))))
        elif crossed:  # do not fire stale reminders on the initial import
            entry["alerts_sent"].extend(crossed)
    return events


def update_pages(reg: dict, snaps: list[tuple[str, dict]], today: dt.date) -> list[dict]:
    """Merge page snapshots ``[(source name, snapshot dict)]``; return events."""
    from .pages import diff_lines

    events = []
    for name, snap in snaps:
        url = snap["url"]
        entry = reg["pages"].get(url)
        if snap.get("error"):
            if entry is None:
                entry = reg["pages"][url] = {"name": name, "snapshot": None, "fail_runs": 0,
                                             "last_changed": None}
            entry["fail_runs"] += 1
            entry["last_error"] = snap["error"]
            if entry["fail_runs"] == FAIL_RUNS_FOR_ALERT:
                events.append(event("SOURCE_ERROR", url, f"{name}: page unavailable",
                                    snap["error"], ["Check whether the page moved; update config/sources.json"],
                                    url, today))
            continue
        if entry is None or entry.get("snapshot") is None:
            reg["pages"][url] = {"name": name, "snapshot": snap, "fail_runs": 0,
                                 "last_changed": today.isoformat()}
            continue
        entry["fail_runs"] = 0
        entry.pop("last_error", None)
        old = entry["snapshot"]
        if old["digest"] != snap["digest"]:
            added, removed = diff_lines(old["lines"], snap["lines"])
            details = [f"+ {s}" for s in added] + [f"− {s}" for s in removed]
            old_dl = {d["date"] for d in old.get("deadlines", [])}
            new_dl = [d for d in snap.get("deadlines", []) if d["date"] not in old_dl]
            summary = "Funding-related text changed"
            if new_dl:
                summary += "; new dates: " + ", ".join(d["date"] for d in new_dl)
            events.append(event("PAGE_CHANGED", url, f"{name}: funding page changed", summary,
                                details, url, today, salt=snap["digest"]))
            entry["last_changed"] = today.isoformat()
        entry["snapshot"] = snap
        entry["name"] = name
    return events


def update_discovered(reg: dict, hits: list[dict], known_urls: set[str], today: dt.date) -> list[dict]:
    events = []
    for h in hits:
        url = h["url"]
        if url in reg["discovered"] or url in known_urls:
            continue
        reg["discovered"][url] = {"first_seen": today.isoformat(), "title": h.get("title", "")}
        events.append(event("DISCOVERED", url, h.get("title") or url,
                            "New page found by web search (unverified)",
                            [h.get("description", "")[:300]], url, today))
    return events
