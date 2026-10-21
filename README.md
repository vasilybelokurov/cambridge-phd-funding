# camfund — Cambridge PhD funding monitor

Daily monitor of Cambridge college and departmental PhD funding for a configurable
profile (default: incoming PhD, 2027/28, Astronomy / Physics / DAMTP, Home and Overseas).
Output: a static dashboard (`docs/index.html`), a CSV (`data/awards.csv`), GitHub Issues
(one per relevant award or source page) and an email digest.

## Sources

| Source | What | How |
|---|---|---|
| University Postgraduate Funding Search, <https://www.student-funding.cam.ac.uk/> | every award listed for prospective PhD applicants (206 on 2026-10-20) | full listing via the site's PrimeFaces AJAX paginator, then each `/fund/<slug>` page, incl. lazy-loaded department and course lists |
| Postgraduate Course Directory | application and **course funding deadlines** (used for awards that say "Same as funding deadline for the course") | `config/profile.toml` → `code` |
| 31 college + IoA/Physics/DAMTP funding pages | awards missing from the database | `config/sources.json` (53 URLs, checked 2026-10-20); funding-relevant lines are hashed and compared |
| Brave Search API (optional) | newly indexed `cam.ac.uk` pages | only when `BRAVE_API_KEY` is set |

## Classification (per fee status)

* **confirmed**: the database explicitly lists a target department/course, PhD and prospective applicants are allowed, the fee status is allowed, and no nationality, residence, other condition or college-membership restriction is recorded.
* **possible**: hard criteria are met, but something needs a human check (listed in the dashboard and the issue). Awards open to all subjects are never "confirmed". Open awards whose title or conditions point to another research area are flagged and hidden by default.
* **no**: wrong subject, level, applicant type or fee status, or closed.

Action (application route): `automatic` (considered through the course application with "Yes – I wish to apply for funding" ticked), `portal` (select the award and answer its questions in the Applicant Portal), `separate` (funder's form, departmental nomination, or a form sent after an offer). Database text such as "No additional application form … required" does not count as separate; a date in a separate-application instruction overrides the course deadline.

College condition (`college_rule`, from the database tooltip, independent of the route):
`preference` (must name the college as 1st/2nd choice), `accepted` (considered only if accepted by the college),
`any` ("NOT required to specify … must be willing to transfer"), `member` (existing members only), or none.

## Dashboard

<https://vasilybelokurov.github.io/cambridge-phd-funding/> (GitHub Pages from `main:/docs`, regenerated each run).

* **Top:** headline counts; a "How students apply" summary with four clickable boxes (Automatic, Portal questions, Separate application, College choice matters). Clicking a box sets the matching filter on the award list and scrolls to it.
* **Award cards:** verdict per fee status; one combined "how to apply" label (route + college condition, e.g. "Automatic — if accepted by Christ's College"); deadline and days left; a collapsible list of conditions and actions with links to the funder page and the GitHub issue.
* **Filters:** fee status, verdict, action, college condition, college, subject scope, free-text search, and "include awards aimed at other research areas" (off by default).
* **Bottom:** course deadlines, recent events, and status of the monitored college/department pages.

Events: `NEW`, `CHANGED` (field-level diffs of tracked terms), `DEADLINE_SOON` (30/14/7/2 days, each fires once), `MISSING` (absent from the database for 2 runs), `PAGE_CHANGED`, `SOURCE_ERROR` (2 consecutive failures), `DISCOVERED`. Undelivered events stay in an outbox and are retried on the next run.

## Run locally

```bash
source ~/Work/venvs/.venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                       # offline tests on saved real pages
python -m camfund run --dry-run           # live scrape (~4 min), prints digest, no issues/email
open docs/index.html
```

Useful flags: `--limit 20` (fetch only 20 award pages), `--skip-pages`, `--today 2026-11-24` (simulate a date).

## Deploy on GitHub Actions

1. Push this directory to a GitHub repository. `.github/workflows/monitor.yml` runs daily at 08:17 Europe/London and on demand.
2. Add the repository secrets `SMTP_HOST`, `SMTP_PORT` (465 or 587), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`, and optionally `BRAVE_API_KEY`. `GITHUB_TOKEN` is provided automatically.
3. Optional: enable GitHub Pages from `main` / `docs`, and set the repository variable `DASHBOARD_URL` so emails link to it.
4. Actions → *Cambridge PhD Funding Monitor* → *Run workflow*. The first run imports everything as `NEW`: one issue per relevant award and one digest email.

## Changing the profile

Edit `config/profile.toml`: `award_year`, `fee_statuses`, and `[[programmes]]` (course name exactly as in the database, Course Directory code, department and parent units).

## Status (2026-10-21)

* Repo, Pages site and repository variable `DASHBOARD_URL` are set up. The data in `data/` comes from a local live run (206 awards, 104 relevant).
* **Not yet done:** SMTP secrets are not set, and the workflow has not been triggered. Its first run delivers the queued outbox: about 105 issues (one per relevant award plus one source-error issue) and one digest email.
* Possible improvements to the summary boxes: reset other filters on click, highlight the active box, clear the filter on a second click.

## Compared with the official Postgraduate Funding Search

Checked 2026-10-21 against <https://www.student-funding.cam.ac.uk/> (public page only, not anything behind a login).

The official site filters by award year, student status, study mode, qualification, nationality (up to 2), country of residence, department/school and college. It sorts by award value, closing date or last update. Its public page offers no saved searches, alerts or export.

| | Official site | camfund |
|---|---|---|
| Change tracking | none; re-search by hand | daily diff: `NEW`, `CHANGED`, `MISSING`, `DEADLINE_SOON` (30/14/7/2 days), sent as issues and email |
| "Same as funding deadline for the course" | text only | replaced with the real date from the Course Directory |
| How to apply (automatic / portal / separate) | in free text | classified for each award and filterable |
| College condition (preference / accepted / any / member) | in a tooltip | extracted and filterable |
| Verdict per fee status | no | confirmed / possible / no for Home and Overseas |
| Course-level match | department filter | listed courses and departments, plus flags for awards aimed at other research areas |
| College and IoA/Physics/DAMTP pages | not covered | 53 pages watched for changes |
| Export | no | CSV |

The award facts come from the same database, so camfund adds no new data about the awards themselves. The only new sources are the college and department pages. Most of its value is in the alerts, the real course deadlines and the coverage of college pages. Without anyone acting on the alerts, the official site plus one careful read-through covers most of what is needed. The scraper depends on the site's PrimeFaces paginator, and the alert and email path has not run in production yet.

## Limits

* Verdicts come from database fields only. Always confirm terms on the funder page.
* College pages are monitored for changes, not parsed into individual awards. A `PAGE_CHANGED` event shows added and removed lines and any new dates.
* Murray Edwards' main postgraduate funding page returned HTTP 403 on 2026-10-20; its general scholarships page is monitored instead.
