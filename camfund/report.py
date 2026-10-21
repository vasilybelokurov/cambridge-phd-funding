"""Static dashboard (docs/index.html) and CSV export from the registry."""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path


def rows(reg: dict, include_irrelevant: bool = False) -> list[dict]:
    out = []
    for slug, e in reg["awards"].items():
        r, a = e["record"], e["assessment"]
        if not include_irrelevant and not a["relevant"]:
            continue
        out.append({
            "slug": slug, "title": r["title"], "funder": r["funder"], "college": r.get("college") or "",
            "value": r.get("value_text") or "", "value_max": r.get("value_max"),
            "fee_status": r.get("fee_status") or [], "verdict": a["verdict"], "action": a["action"],
            "deadline": a["deadline"], "deadline_source": a["deadline_source"],
            "deadline_text": r.get("deadline_text") or r.get("closes_text") or "",
            "programmes": a["programmes"], "scope": a["subject_scope"], "hint": a.get("subject_hint"), "crule": a.get("college_rule"), "reasons": a["reasons"],
            "notes": a["action_notes"], "url": r["url"], "website": r.get("website") or "",
            "status": e["status"], "first_seen": e["first_seen"], "issue": e.get("issue"),
        })
    out.sort(key=lambda x: (x["deadline"] or "9999", x["title"]))
    return out


def write_csv(reg: dict, path: str | Path) -> None:
    rs = rows(reg)
    cols = ["title", "funder", "college", "value", "fee_status", "verdict", "action", "deadline",
            "deadline_source", "programmes", "scope", "reasons", "notes", "url", "website", "status", "first_seen"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rs:
            r = dict(r)
            for k in ("fee_status", "programmes", "reasons", "notes"):
                r[k] = "; ".join(r[k])
            r["verdict"] = "; ".join(f"{k}: {v}" for k, v in r["verdict"].items())
            w.writerow(r)


def write_dashboard(reg: dict, path: str | Path, repo: str | None = None) -> None:
    pages = [{"name": p["name"], "url": u, "last_changed": p.get("last_changed"),
              "error": p.get("last_error"), "n_lines": len((p.get("snapshot") or {}).get("lines", [])),
              "deadlines": (p.get("snapshot") or {}).get("deadlines", [])}
             for u, p in sorted(reg["pages"].items(), key=lambda kv: kv[1]["name"])]
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "award_year": reg["meta"].get("award_year"),
        "n_total": len(reg["awards"]),
        "awards": rows(reg),
        "courses": list(reg.get("courses", {}).values()),
        "pages": pages,
        "history": reg.get("history", [])[-150:][::-1],
        "repo": repo,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(TEMPLATE.replace("/*DATA*/null", payload))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cambridge PhD Funding</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e2dc;--acc:#0b5c8a;
--ok:#1e7a3c;--ok-bg:#e3f3e8;--maybe:#8a5a00;--maybe-bg:#fbf0d9;--no:#8f2a2a;--no-bg:#f6e3e3;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--card:#1f1f1d;--fg:#ececea;
--muted:#a3a39d;--line:#34342f;--acc:#6db5e0;--ok:#7fd49a;--ok-bg:#1d3325;--maybe:#e8c26e;--maybe-bg:#3a2f17;--no:#ee9a9a;--no-bg:#3b2222;}}
:root[data-theme="dark"]{--bg:#151514;--card:#1f1f1d;--fg:#ececea;--muted:#a3a39d;--line:#34342f;--acc:#6db5e0;
--ok:#7fd49a;--ok-bg:#1d3325;--maybe:#e8c26e;--maybe-bg:#3a2f17;--no:#ee9a9a;--no-bg:#3b2222;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.muted{color:var(--muted)}a{color:var(--acc)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:13px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
select,input{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--fg)}
input[type=search]{flex:1;min-width:180px}
.list{display:flex;flex-direction:column;gap:8px}
.aw{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:grid;grid-template-columns:1fr auto;gap:6px 16px}
.aw h3{margin:0;font-size:15.5px}.aw .meta{color:var(--muted);font-size:13px}
.right{text-align:right;white-space:nowrap}.dl{font-weight:600}.dl.soon{color:var(--no)}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;margin:2px 4px 0 0;border:1px solid var(--line)}
.confirmed{background:var(--ok-bg);color:var(--ok);border-color:transparent}.possible{background:var(--maybe-bg);color:var(--maybe);border-color:transparent}
.act-automatic{border-color:var(--ok)}.act-portal{border-color:var(--acc)}.act-separate{border-color:var(--no);font-weight:600}
.no{background:var(--no-bg);color:var(--no);border-color:transparent}
details{grid-column:1/-1;font-size:13.5px}summary{cursor:pointer;color:var(--muted)}ul{margin:6px 0;padding-left:18px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.scroll{overflow-x:auto}.err{color:var(--no)}
@media (max-width:640px){.aw{grid-template-columns:1fr}.right{text-align:left}}
</style></head><body><main>
<h1>Cambridge PhD funding monitor</h1>
<div class="muted" id="sub"></div>
<div class="kpis" id="kpis"></div>
<h2>Awards</h2>
<div class="filters">
<label>Fee status <select id="fs"></select></label>
<label>Verdict <select id="vd"><option value="any">confirmed + possible</option><option value="confirmed">confirmed only</option></select></label>
<label>Action <select id="ac"><option value="">any</option><option value="automatic">automatic</option><option value="portal">Applicant Portal questions</option><option value="separate">separate application</option></select></label>
<label>College condition <select id="cr"><option value="">any</option><option value="preference">must name college as 1st/2nd choice</option>
<option value="accepted">only if accepted by the college</option><option value="any">any college (must transfer)</option>
<option value="member">existing members only</option><option value="none">no college condition</option></select></label>
<label>College <select id="co"></select></label>
<label>Scope <select id="sc"><option value="">any</option><option value="explicit">lists our department/course</option><option value="open">open to all subjects</option></select></label>
<label><input type="checkbox" id="oth"> include awards aimed at other research areas</label>
<input type="search" id="q" placeholder="Search title, funder, college…">
</div>
<div class="muted" id="count"></div>
<div class="list" id="list"></div>
<h2>Course deadlines</h2><div class="scroll"><table id="courses"></table></div>
<h2>Recent events</h2><div class="scroll"><table id="hist"></table></div>
<h2>College &amp; department pages</h2><div class="scroll"><table id="pages"></table></div>
<p class="muted">Verdicts use University funding-database fields only. “Possible” means a condition needs a human check;
absence of a restriction is never treated as proof. Always confirm terms on the funder page.</p>
</main>
<script>
const D=/*DATA*/null;
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const today=new Date(new Date().toISOString().slice(0,10));
const days=d=>d?Math.round((new Date(d)-today)/864e5):null;
const FS=[...new Set(D.awards.flatMap(a=>Object.keys(a.verdict)))];
$('#fs').innerHTML='<option value="">any</option>'+FS.map(f=>`<option>${esc(f)}</option>`).join('');
$('#sub').textContent=`Award year ${D.award_year} · ${D.awards.length} relevant of ${D.n_total} PhD awards in the database · updated ${D.generated}`;
const soon=D.awards.filter(a=>a.deadline&&days(a.deadline)>=0&&days(a.deadline)<=30).length;
const conf=D.awards.filter(a=>Object.values(a.verdict).includes('confirmed')).length;
const sep=D.awards.filter(a=>a.action==='separate').length;
const next=D.courses.map(c=>c.funding_deadline).filter(Boolean).sort()[0];
$('#kpis').innerHTML=[[D.awards.length,'relevant awards'],[conf,'confirmed for ≥1 fee status'],[sep,'need a separate application'],
[soon,'deadlines within 30 days'],[next?days(next)+' d':'—','to first course funding deadline'+(next?` (${next})`:'')]]
.map(([b,s])=>`<div class="kpi"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');
$('#co').innerHTML='<option value="">any</option>'+[...new Set(D.awards.map(a=>a.college).filter(Boolean))].sort().map(c=>`<option>${esc(c)}</option>`).join('');
const CR={preference:['Name as 1st/2nd college choice','possible'],accepted:['Only if accepted by college','possible'],
 member:['Existing members only','no'],any:['Any college (must transfer)','']};
const crPill=a=>a.crule&&CR[a.crule]?`<span class="pill ${CR[a.crule][1]}" title="${esc(a.college)}">${esc(a.college)}: ${esc(CR[a.crule][0])}</span>`:'';
const ACT={automatic:'automatic (course application)',portal:'Applicant Portal questions',separate:'separate application'};
function render(){
 const cr=$('#cr').value,co=$('#co').value,oth=$('#oth').checked,fs=$('#fs').value,vd=$('#vd').value,ac=$('#ac').value,sc=$('#sc').value,q=$('#q').value.toLowerCase();
 const xs=D.awards.filter(a=>{
  const vs=fs?[a.verdict[fs]]:Object.values(a.verdict);
  if(!vs.some(v=>vd==='confirmed'?v==='confirmed':v!=='no'))return false;
  if(!oth&&a.hint==='other')return false;
  if(cr&&(a.crule||'none')!==cr)return false; if(co&&a.college!==co)return false;
  if(ac&&a.action!==ac)return false; if(sc&&a.scope!==sc)return false;
  return !q||(a.title+' '+a.funder+' '+a.college).toLowerCase().includes(q);});
 $('#count').textContent=`${xs.length} shown`;
 $('#list').innerHTML=xs.map(a=>{const n=days(a.deadline);
  return `<div class="aw"><div><h3><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></h3>
  <div class="meta">${esc(a.funder)}${a.college&&a.college!==a.funder?' · '+esc(a.college):''} · ${esc(a.value||'value not stated')}</div>
  <div>${Object.entries(a.verdict).map(([k,v])=>`<span class="pill ${v}">${esc(k)}: ${v}</span>`).join('')}
  <span class="pill act-${a.action}">${esc(ACT[a.action])}</span>${crPill(a)}${a.scope==='open'?'<span class="pill">all subjects</span>':''}${a.hint==='other'?'<span class="pill no">other research area?</span>':''}
  ${a.status==='missing'?'<span class="pill no">possibly withdrawn</span>':''}</div></div>
  <div class="right"><div class="dl ${n!==null&&n<=14?'soon':''}">${a.deadline?esc(a.deadline):'—'}</div>
  <div class="meta">${n===null?esc(a.deadline_text).slice(0,60):(n<0?'passed':n+' days')}${a.deadline_source==='course funding deadline'?' · course deadline':''}</div></div>
  <details><summary>Conditions &amp; actions</summary><ul>${[...a.reasons.map(r=>'Check: '+r),...a.notes,
   'Programmes: '+a.programmes.join(', ')].map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
   ${a.website?`<a href="${esc(a.website)}" target="_blank" rel="noopener">Funder page</a>`:''}
   ${a.issue&&D.repo?` · <a href="https://github.com/${esc(D.repo)}/issues/${a.issue}" target="_blank" rel="noopener">Issue #${a.issue}</a>`:''}</details></div>`}).join('');
}
['#fs','#vd','#ac','#sc','#q','#oth','#cr','#co'].forEach(s=>$(s).addEventListener('input',render));render();
$('#courses').innerHTML='<tr><th>Course</th><th>Intake</th><th>Application deadline</th><th>Funding deadline</th></tr>'+
 D.courses.map(c=>`<tr><td><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)}</a></td><td>${esc(c.intake)}</td>
 <td>${esc(c.application_deadline||'—')}</td><td><b>${esc(c.funding_deadline||'—')}</b>${c.funding_deadline?` (${days(c.funding_deadline)} d)`:''}</td></tr>`).join('');
$('#hist').innerHTML='<tr><th>Date</th><th>Event</th><th>Item</th><th>Summary</th></tr>'+(D.history.length?D.history.map(e=>
 `<tr><td>${esc(e.date)}</td><td>${esc(e.type)}</td><td><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a></td><td>${esc(e.summary)}</td></tr>`).join(''):'<tr><td colspan="4" class="muted">No events yet</td></tr>');
$('#pages').innerHTML='<tr><th>Source</th><th>Status</th><th>Last change</th><th>Dates found</th></tr>'+D.pages.map(p=>
 `<tr><td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a></td>
 <td>${p.error?`<span class="err">${esc(p.error)}</span>`:esc(p.n_lines+' funding lines')}</td><td>${esc(p.last_changed||'—')}</td>
 <td>${p.deadlines.slice(0,3).map(d=>`<span title="${esc(d.context)}">${esc(d.date)}</span>`).join(', ')||'—'}</td></tr>`).join('');
</script></body></html>
"""
