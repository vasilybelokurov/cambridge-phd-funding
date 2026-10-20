"""Delivery of events: GitHub Issues (permanent record) and an email digest.

Events sit in ``reg["outbox"]`` until every enabled channel has delivered them, so a
failing SMTP server or GitHub API call is retried on the next run instead of losing
the notification.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)

AWARD_EVENTS = {"NEW", "CHANGED", "DEADLINE_SOON", "MISSING"}
TYPE_LABEL = {
    "NEW": "New award", "CHANGED": "Changed terms", "DEADLINE_SOON": "Deadline approaching",
    "MISSING": "Possibly closed", "PAGE_CHANGED": "College/department page changed",
    "SOURCE_ERROR": "Source unavailable", "DISCOVERED": "Discovered by search",
}
OUTBOX_MAX_AGE_DAYS = 30


def marker(key: str) -> str:
    return f"<!-- camfund-key: {hashlib.sha1(key.encode()).hexdigest()[:16]} -->"


# ----------------------------------------------------------------------------
# GitHub
# ----------------------------------------------------------------------------


class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com") -> None:
        self.repo, self.api = repo, api
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{self.api}/repos/{self.repo}{path}", timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path}: {r.status_code} {r.text[:300]}")
        time.sleep(0.8)  # stay well below the secondary rate limit for content creation
        return r.json() if r.text else None

    def ensure_labels(self, labels: dict[str, str]) -> None:
        existing = {l["name"] for l in self._req("GET", "/labels", params={"per_page": 100})}
        for name, color in labels.items():
            if name not in existing:
                self._req("POST", "/labels", json={"name": name, "color": color})

    def index_issues(self) -> dict[str, int]:
        """Map marker -> issue number for all issues labelled ``camfund``."""
        out, page = {}, 1
        while True:
            batch = self._req("GET", "/issues", params={"labels": "camfund", "state": "all",
                                                        "per_page": 100, "page": page})
            for it in batch:
                body = it.get("body") or ""
                i = body.find("<!-- camfund-key:")
                if i >= 0:
                    out[body[i: body.find("-->", i) + 3]] = it["number"]
            if len(batch) < 100:
                return out
            page += 1

    def create(self, title: str, body: str, labels: list[str]) -> int:
        return self._req("POST", "/issues", json={"title": title, "body": body, "labels": labels})["number"]

    def comment(self, number: int, body: str) -> None:
        self._req("POST", f"/issues/{number}/comments", json={"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._req("POST", f"/issues/{number}/labels", json={"labels": labels})

    def edit(self, number: int, **fields) -> None:
        self._req("PATCH", f"/issues/{number}", json=fields)


LABELS = {
    "camfund": "5319e7", "funding-award": "0e8a16", "source-page": "1d76db",
    "verdict:confirmed": "0e8a16", "verdict:possible": "fbca04",
    "action:separate": "d93f0b", "action:portal": "c5def5", "action:automatic": "bfdadc",
    "possibly-closed": "b60205", "deadline-soon": "e99695",
}


def _verdict_tag(assessment: dict) -> str:
    vs = set(assessment.get("verdict", {}).values())
    return "verdict:confirmed" if "confirmed" in vs else "verdict:possible"


def award_issue_body(entry: dict) -> str:
    r, a = entry["record"], entry["assessment"]
    rows = [
        ("Funder", r["funder"]),
        ("Value", r.get("value_text") or "not stated"),
        ("Verdict", ", ".join(f"**{k}**: {v}" for k, v in a["verdict"].items())),
        ("Programmes covered", ", ".join(a["programmes"]) or "—"),
        ("Subject scope", a["subject_scope"]),
        ("Fee status", ", ".join(r["fee_status"]) if r.get("fee_status") else "no restriction listed"),
        ("Action", a["action"]),
        ("Deadline", f"{a['deadline'] or r.get('deadline_text') or '—'} ({a['deadline_source']})"),
        ("College", (r.get("college") or "—") + (f" — {r['college_note']}" if r.get("college_note") else "")),
        ("Funder website", r.get("website") or "—"),
        ("Database entry", r["url"]),
        ("Last updated (DB)", r.get("last_updated") or "—"),
    ]
    md = [f"### {r['title']}", "", "| Field | Value |", "|---|---|"]
    md += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in rows]
    if a["reasons"]:
        md += ["", "**Check before relying on this:**"] + [f"- {x}" for x in a["reasons"]]
    if a["action_notes"]:
        md += ["", "**What to do:**"] + [f"- {x}" for x in a["action_notes"]]
    if r.get("about"):
        md += ["", "<details><summary>About (from database)</summary>", "", r["about"], "", "</details>"]
    md += ["", "_Classification uses University funding-database fields only; confirm terms on the funder page._",
           "", marker(r["slug"])]
    return "\n".join(md)


def event_comment(ev: dict) -> str:
    lines = [f"**{TYPE_LABEL.get(ev['type'], ev['type'])}** ({ev['date']}): {ev['summary']}"]
    lines += [f"- {d}" for d in ev["details"] if d]
    return "\n".join(lines)


def deliver_issues(reg: dict, gh: GitHub) -> int:
    pending = [e for e in reg["outbox"] if not e["sent_issue"]]
    if not pending:
        return 0
    gh.ensure_labels(LABELS)
    known = gh.index_issues()
    n = 0
    for ev in pending:
        key = ev["key"]
        mk = marker(key)
        number = known.get(mk)
        try:
            if ev["type"] in AWARD_EVENTS:
                entry = reg["awards"].get(key)
                if number is None:
                    a = entry["assessment"]
                    labels = ["camfund", "funding-award", _verdict_tag(a), f"action:{a['action']}"]
                    number = gh.create(f"[Funding] {entry['record']['title']} — {entry['record']['funder']}",
                                       award_issue_body(entry), labels)
                    known[mk] = number
                    if ev["type"] != "NEW":
                        gh.comment(number, event_comment(ev))
                else:
                    gh.comment(number, event_comment(ev))
                    if ev["type"] == "CHANGED":
                        gh.edit(number, body=award_issue_body(entry))
                if ev["type"] == "MISSING":
                    gh.add_labels(number, ["possibly-closed"])
                elif ev["type"] == "DEADLINE_SOON":
                    gh.add_labels(number, ["deadline-soon"])
                entry["issue"] = number
            else:
                if number is None:
                    title = (f"[Source] {ev['title']}" if ev["type"] != "DISCOVERED"
                             else f"[Discovered] {ev['title'][:80]}")
                    body = f"Monitored URL: {ev['url']}\n\n{event_comment(ev)}\n\n{mk}"
                    number = gh.create(title, body, ["camfund", "source-page"])
                    known[mk] = number
                else:
                    gh.comment(number, event_comment(ev))
            ev["sent_issue"] = True
            n += 1
        except Exception as e:  # keep the event for the next run
            log.error("issue delivery failed for %s: %s", key, e)
    return n


# ----------------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------------


def digest(events: list[dict], dashboard_url: str | None = None) -> tuple[str, str, str]:
    """Return (subject, plain text, html) for a list of events."""
    order = ["DEADLINE_SOON", "NEW", "CHANGED", "MISSING", "PAGE_CHANGED", "DISCOVERED", "SOURCE_ERROR"]
    events = sorted(events, key=lambda e: (order.index(e["type"]) if e["type"] in order else 99, e["title"]))
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    subject = "Cambridge PhD funding: " + ", ".join(
        f"{n} {TYPE_LABEL.get(t, t).lower()}" for t, n in sorted(counts.items(), key=lambda x: order.index(x[0])))
    txt, h = [], ["<html><body style='font-family:sans-serif'>"]
    if dashboard_url:
        txt.append(f"Dashboard: {dashboard_url}\n")
        h.append(f"<p><a href='{html.escape(dashboard_url)}'>Open dashboard</a></p>")
    for e in events:
        tag = TYPE_LABEL.get(e["type"], e["type"]).upper()
        txt.append(f"{tag} — {e['title']}\n  {e['summary']}\n  {e['url']}")
        txt += [f"    · {d}" for d in e["details"][:8] if d]
        txt.append("")
        h.append(f"<p><b>{html.escape(tag)}</b> — <a href='{html.escape(e['url'])}'>"
                 f"{html.escape(e['title'])}</a><br>{html.escape(e['summary'])}")
        if e["details"]:
            h.append("<ul>" + "".join(f"<li>{html.escape(d)}</li>" for d in e["details"][:8] if d) + "</ul>")
        h.append("</p>")
    txt.append("Classification is from University funding-database fields; confirm on the funder page.")
    h.append("<p style='color:#666'>Classification is from University funding-database fields; "
             "confirm on the funder page.</p></body></html>")
    return subject, "\n".join(txt), "".join(h)


def smtp_config() -> dict | None:
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
    cfg = {k: os.environ.get(k, "").strip() for k in keys}
    return cfg if all(cfg.values()) else None


def send_email(cfg: dict, subject: str, text: str, html_body: str, attempts: int = 3) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.get("EMAIL_FROM") or cfg["SMTP_USER"], cfg["EMAIL_TO"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    port = int(cfg["SMTP_PORT"])
    for i in range(attempts):
        try:
            if port == 465:
                with smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, context=ssl.create_default_context(), timeout=30) as s:
                    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
                    s.send_message(msg)
            else:
                with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
                    s.send_message(msg)
            return
        except Exception as e:
            log.warning("SMTP attempt %d failed: %s", i + 1, e)
            if i == attempts - 1:
                raise
            time.sleep(5 * (i + 1))


def deliver_email(reg: dict, cfg: dict, dashboard_url: str | None = None) -> int:
    pending = [e for e in reg["outbox"] if not e["sent_email"]]
    if not pending:
        return 0
    subject, text, html_body = digest(pending, dashboard_url)
    send_email(cfg, subject, text, html_body)
    for e in pending:
        e["sent_email"] = True
    return len(pending)


def prune_outbox(reg: dict, today: dt.date, issues_enabled: bool, email_enabled: bool) -> None:
    keep = []
    for e in reg["outbox"]:
        if not issues_enabled:
            e["sent_issue"] = True
        if not email_enabled:
            e["sent_email"] = True
        age = (today - dt.date.fromisoformat(e["date"])).days
        if not (e["sent_issue"] and e["sent_email"]) and age <= OUTBOX_MAX_AGE_DAYS:
            keep.append(e)
    reg["outbox"] = keep
