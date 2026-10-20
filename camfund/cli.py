"""Command-line entry point: ``python -m camfund run``.

Pipeline per run:
  1. course deadlines (Course Directory)
  2. full listing of the University funding database + every award detail page
  3. classification against config/profile.toml
  4. college/department page snapshots (config/sources.json)
  5. optional Brave discovery
  6. registry diff -> events -> outbox -> GitHub Issues + email
  7. dashboard (docs/index.html) and CSV (data/awards.csv)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import tomllib
from pathlib import Path

from . import central, classify, courses, discovery, notify, pages, registry, report
from .http import PoliteSession

log = logging.getLogger("camfund")
ROOT = Path(__file__).resolve().parent.parent


def load_profile(path: Path) -> classify.Profile:
    return classify.Profile.from_dict(tomllib.loads(path.read_text()))


def collect_awards(session, profile, limit: int | None = None) -> tuple[list[dict], list[str]]:
    cards = central.fetch_all_cards(session, profile.award_year, profile.qualification)
    log.info("listing: %d awards", len(cards))
    out, errors = [], []
    for i, card in enumerate(cards[:limit] if limit else cards):
        try:
            aw = central.fetch_award(session, card.slug)
            out.append(central.merge_card(aw, card).to_dict())
        except Exception as e:
            errors.append(f"{card.slug}: {e}")
            log.warning("award %s failed: %s", card.slug, e)
        if (i + 1) % 25 == 0:
            log.info("  details %d/%d", i + 1, len(cards))
    return out, errors


def cmd_run(args) -> int:
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    profile = load_profile(Path(args.profile))
    sources = json.loads(Path(args.sources).read_text())
    reg = registry.load(args.registry)
    s = PoliteSession(delay=args.delay)

    # 1. course deadlines
    progs = {p.course: p.code for p in profile.programmes}
    try:
        cds = courses.fetch_courses(s, progs, profile.award_year)
        reg["courses"] = {k: v.to_dict() for k, v in cds.items()}
    except Exception as e:
        log.error("course deadlines failed, using previous values: %s", e)
        cds = {k: courses.CourseDates(**v) for k, v in reg.get("courses", {}).items()}

    events: list[dict] = []
    # 2-3. central database
    awards, errors = collect_awards(s, profile, args.limit)
    if awards and len(errors) <= max(5, len(awards) // 10):
        assessments = {a["slug"]: classify.assess(central.Award(**a), profile, cds, today).to_dict()
                       for a in awards}
        if args.limit:  # partial runs must not mark the rest as missing
            for slug in reg["awards"]:
                if slug not in assessments:
                    awards.append(reg["awards"][slug]["record"])
                    assessments[slug] = reg["awards"][slug]["assessment"]
        events += registry.update_awards(reg, awards, assessments, today, profile.alert_days)
    else:
        events.append(registry.event("SOURCE_ERROR", central.BASE, "University funding database",
                                     f"Scrape failed or incomplete ({len(errors)} errors); registry not updated",
                                     errors[:10], central.BASE, today))

    # 4. college / department pages
    if not args.skip_pages:
        snaps = []
        for src in sources:
            for u in src["urls"]:
                snaps.append((f"{src['name']} — {u.get('label') or 'funding'}", pages.fetch_page(s, u["url"]).to_dict()))
        events += registry.update_pages(reg, snaps, today)

    # 5. discovery
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if key:
        known = {u["url"].rstrip("/") for src in sources for u in src["urls"]}
        events += registry.update_discovered(reg, discovery.search(key), known, today)

    # 6. deliver
    reg["outbox"].extend(events)
    reg.setdefault("history", []).extend(
        {k: e[k] for k in ("date", "type", "title", "summary", "url")} for e in events)
    reg["history"] = reg["history"][-500:]
    reg["meta"].update(last_run=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       award_year=profile.award_year, runs=reg["meta"].get("runs", 0) + 1)
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    issues_on = bool(repo and token) and not args.dry_run
    smtp = None if args.dry_run else notify.smtp_config()
    exit_code = 0
    if issues_on:
        try:
            log.info("issues: %d delivered", notify.deliver_issues(reg, notify.GitHub(repo, token)))
        except Exception as e:
            log.error("GitHub delivery failed (will retry next run): %s", e)
            exit_code = 1
    if smtp:
        try:
            log.info("email: %d events sent", notify.deliver_email(reg, smtp, os.environ.get("DASHBOARD_URL")))
        except Exception as e:
            log.error("email failed (will retry next run): %s", e)
            exit_code = 1
    if args.dry_run and events:
        subject, text, _ = notify.digest(events)
        print(subject + "\n\n" + text)
    if not args.dry_run:
        notify.prune_outbox(reg, today, issues_on, bool(smtp))

    # 7. outputs
    registry.save(reg, args.registry)
    report.write_dashboard(reg, args.dashboard, repo)
    report.write_csv(reg, Path(args.registry).with_name("awards.csv"))
    rel = sum(e["assessment"]["relevant"] for e in reg["awards"].values())
    log.info("done: %d awards (%d relevant), %d events, %d detail errors", len(reg["awards"]), rel,
             len(events), len(errors))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="camfund", description="Cambridge PhD funding monitor")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="scrape, diff, notify, write dashboard")
    r.add_argument("--profile", default=str(ROOT / "config/profile.toml"))
    r.add_argument("--sources", default=str(ROOT / "config/sources.json"))
    r.add_argument("--registry", default=str(ROOT / "data/registry.json"))
    r.add_argument("--dashboard", default=str(ROOT / "docs/index.html"))
    r.add_argument("--dry-run", action="store_true", help="no issues/email; print the digest")
    r.add_argument("--skip-pages", action="store_true", help="skip college/department pages")
    r.add_argument("--limit", type=int, help="only fetch the first N award pages (testing)")
    r.add_argument("--delay", type=float, default=0.5, help="seconds between HTTP requests")
    r.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    r.set_defaults(func=cmd_run)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    return args.func(args)
