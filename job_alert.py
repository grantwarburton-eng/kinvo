"""Checks job sites for new Project Manager / Business Operations postings. Matches go to the
AI agent (apply_agent.py), which scores fit and drafts a tailored resume and cover letter for
good ones. Everything is emailed to you for review.

Settings live in config.toml and profile/profile.toml. Secrets come from environment variables:
  ADZUNA_APP_ID, ADZUNA_APP_KEY   (required for Adzuna)
  JOOBLE_KEY                      (optional)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD
  ALERT_TO                        (optional, defaults to GMAIL_ADDRESS)
  ANTHROPIC_API_KEY               (for the AI step; without it you get plain alerts)

Usage:
  python job_alert.py              normal run (reviews + emails new matches, remembers them)
  python job_alert.py --dry-run    print matches only; no AI, no email, nothing remembered
  python job_alert.py --force      check every source now, ignoring every_minutes
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state" / "seen.json"
FORGET_AFTER_DAYS = 45
USER_AGENT = "personal-job-alert/1.0"

REMOTE_WORDS = re.compile(r"\b(remote|work from home|wfh|telecommut\w*|virtual)\b", re.I)
HYBRID_WORDS = re.compile(r"\b(hybrid|in[- ]office|on[- ]site|onsite)\b", re.I)
CANADA_OK = re.compile(r"canada|worldwide|anywhere|global|north america|americas", re.I)


# ---------- helpers ----------

def now():
    return datetime.now(timezone.utc)


def strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def http_json(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def phrase_regex(phrases):
    parts = [r"\b" + re.escape(p.lower()) + r"\b" for p in phrases]
    return re.compile("|".join(parts), re.I) if parts else None


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def parse_salary_text(text, cfg):
    """Return the top of a salary range as yearly CAD, or None if not listed/unparseable."""
    if not text:
        return None
    t = text.lower()
    numbers = []
    for m in re.finditer(r"(\d[\d,\.]*)\s*(k\b)?", t):
        raw, is_k = m.group(1).rstrip(".,"), bool(m.group(2))
        if is_k and re.fullmatch(r"\d+,\d{1,2}", raw):  # European decimal, e.g. "35,3k"
            raw = raw.replace(",", ".")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        numbers.append(value * 1000 if is_k else value)
    numbers = [n for n in numbers if n >= 10]  # ignore stray small numbers
    if not numbers:
        return None
    top = max(numbers)
    if re.search(r"hour|/hr|\bhr\b|hourly", t) or top < 300:
        top *= cfg["hours_per_year"]
    elif re.search(r"month", t):
        top *= 12
    elif re.search(r"week", t):
        top *= 52
    if "usd" in t or "us$" in t:
        top *= cfg["usd_to_cad"]
    return top


def money(n):
    return f"${n:,.0f}"


# ---------- sources ----------
# Each returns a list of dicts: id, title, company, location, salary_top, salary_text,
# url, description, remote_hint (True if the source itself says it's remote)

def adzuna(cfg, remote):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "what_or": cfg["keywords"],
        "max_days_old": 3,
        "sort_by": "date",
    }
    if remote:
        params["what_and"] = "remote"
    else:
        params["where"] = cfg["where"]
        params["distance"] = cfg["distance_km"]
    url = "https://api.adzuna.com/v1/api/jobs/ca/search/1?" + urllib.parse.urlencode(params)
    jobs = []
    for r in http_json(url).get("results", []):
        loc = r.get("location") or {}
        location = ", ".join(dict.fromkeys([loc.get("display_name", "")] + list(reversed(loc.get("area", [])))))
        predicted = str(r.get("salary_is_predicted", "0")) == "1"
        top = None if predicted else (r.get("salary_max") or r.get("salary_min"))
        low = None if predicted else r.get("salary_min")
        if top:
            text = money(low) + " – " + money(top) if low and low != top else money(top)
        else:
            text = ""
        jobs.append({
            "id": f"adzuna:{r.get('id')}",
            "title": strip_html(r.get("title")),
            "company": (r.get("company") or {}).get("display_name", ""),
            "location": location.strip(", "),
            "salary_top": float(top) if top else None,
            "salary_text": text,
            "url": r.get("redirect_url", ""),
            "description": strip_html(r.get("description")),
            "remote_hint": False,
        })
    return jobs


def jooble(cfg, salary_cfg):
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        return None  # optional source
    host = cfg.get("host", "ca.jooble.org")
    since = (now() - timedelta(days=3)).strftime("%Y-%m-%d")
    searches = [(q, cfg["where"], cfg["radius_km"]) for q in cfg["queries"]]
    searches += [(q, "Canada", "0") for q in cfg.get("remote_queries", [])]
    jobs = []
    for keywords, where, radius in searches:
        body = {"keywords": keywords, "location": where, "radius": radius,
                "page": "1", "ResultOnPage": "50", "datecreatedfrom": since}
        for r in http_json(f"https://{host}/api/{key}", body).get("jobs", []):
            salary_text = r.get("salary") or ""
            jobs.append({
                "id": f"jooble:{r.get('id')}",
                "title": strip_html(r.get("title")),
                "company": r.get("company") or "",
                "location": r.get("location") or "",
                "salary_top": parse_salary_text(salary_text, salary_cfg),
                "salary_text": salary_text,
                "url": r.get("link", ""),
                "description": strip_html(r.get("snippet")),
                "remote_hint": False,
            })
    return jobs


def remotive(cfg, salary_cfg):
    jobs = []
    for cat in cfg["categories"]:
        url = "https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"category": cat})
        for r in http_json(url).get("jobs", []):
            where = r.get("candidate_required_location") or ""
            if not CANADA_OK.search(where):
                continue
            salary_text = r.get("salary") or ""
            # Remotive salaries are almost always USD.
            usd_text = salary_text if re.search(r"usd|us\$|€|£", salary_text, re.I) else salary_text + " USD"
            jobs.append({
                "id": f"remotive:{r.get('id')}",
                "title": strip_html(r.get("title")),
                "company": r.get("company_name") or "",
                "location": f"Remote ({where})",
                "salary_top": parse_salary_text(usd_text, salary_cfg) if salary_text else None,
                "salary_text": salary_text,
                "url": r.get("url", ""),
                "description": "",
                "remote_hint": True,
            })
    return jobs


# ---------- filtering ----------

def check_job(job, config, include_re, exclude_re, city_re, not_local_re=None):
    """Return (ok, reason_or_label)."""
    title = job["title"]
    if not include_re.search(title):
        return False, "title"
    if exclude_re and exclude_re.search(title):
        return False, "excluded title"

    floor = config["salary"]["minimum"]
    if job["salary_top"] is not None and job["salary_top"] < floor:
        return False, "salary"

    text = " ".join([title, job["location"], job["description"]])
    in_area = bool(city_re.search(job["location"]))
    if in_area and not_local_re and not_local_re.search(job["location"]):
        in_area = False
    is_remote = job["remote_hint"] or bool(REMOTE_WORDS.search(text))
    is_hybrid = bool(HYBRID_WORDS.search(text))

    if in_area:
        return True, "Hybrid" if is_hybrid and is_remote else "In person"
    if is_remote and not is_hybrid and config["location"]["remote_ok"]:
        return True, "Remote"
    return False, "location"


# ---------- state ----------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": {}, "last_checked": {}}


def save_state(state):
    cutoff = (now() - timedelta(days=FORGET_AFTER_DAYS)).strftime("%Y-%m-%d")
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def is_due(state, name, every_minutes, force):
    if force:
        return True
    last = state["last_checked"].get(name)
    if not last:
        return True
    # 5 minutes of slack so GitHub's slightly-late schedules don't skip a check.
    return now() - datetime.fromisoformat(last) >= timedelta(minutes=every_minutes - 5)


# ---------- email ----------

def _esc(v):
    return html.escape(str(v or ""))


def _job_header_html(j):
    salary = j.get("salary_text") or "Salary not listed"
    return f"""
  <div style="font-size:17px;font-weight:600"><a href="{_esc(j['url'])}" style="color:#1a56db;text-decoration:none">{_esc(j['title'])}</a></div>
  <div style="color:#333;margin-top:4px">{_esc(j.get('company')) or 'Company not listed'}</div>
  <div style="color:#555;margin-top:4px">{_esc(j.get('work_type'))} · {_esc(j.get('location')) or 'Location not listed'}</div>
  <div style="color:#555;margin-top:4px">{_esc(salary)}</div>"""


def _list_html(label, items, color):
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return f'<div style="margin-top:8px;color:{color}"><b>{label}</b><ul style="margin:4px 0 0 18px;padding:0">{lis}</ul></div>'


def build_email(result, skipped_digest):
    drafted, plain = result["drafted"], result["plain"]
    total = len(drafted) + len(plain)
    if drafted:
        j = drafted[0][0]
        n = len(drafted)
        subject = (f"{n} application{'s' if n > 1 else ''} ready to review: {j['title']} at "
                   f"{j.get('company') or 'unknown'}" + (f" + {n - 1} more" if n > 1 else ""))
        if plain:
            subject += f" (and {len(plain)} other match{'es' if len(plain) > 1 else ''})"
    else:
        j = plain[0][0]
        subject = (f"New job: {j['title']} at {j.get('company') or 'unknown company'}" if total == 1 else
                   f"{total} new jobs: {j['title']} at {j.get('company') or 'unknown'} + {total - 1} more")

    sections, lines, attachments = [], [], []

    if drafted:
        import documents   # only needed when the agent drafted something
        cards = []
        for j, fit, folder in drafted:
            score_color = "#0a7d32" if fit.score >= 85 else "#b26b00"
            record = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            primary = documents.primary_files(record.get("files", []))
            ext = primary["resume"].rsplit(".", 1)[1]
            fmt_label = "Word" if ext == "docx" else ext.upper()
            cards.append(f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:0 0 12px">
  <div style="display:inline-block;background:{score_color};color:#fff;border-radius:12px;padding:2px 10px;font-size:13px;margin-bottom:8px">Fit {fit.score}/100</div>
  {_job_header_html(j)}
  <div style="margin-top:10px;color:#222">{_esc(fit.one_line)}</div>
  {_list_html("Why you fit", fit.strengths, "#0a5d26")}
  {_list_html("Gaps", fit.gaps, "#8a4b00")}
  <div style="margin-top:8px;color:#555"><b>Pay:</b> {_esc(fit.compensation)}</div>
  {_list_html("What was tailored", record.get("what_was_tailored", []), "#444")}
  <div style="margin-top:8px;color:#555;font-size:13px">Attached: resume and cover letter ({_esc(fmt_label)}). Also saved in <code>{_esc(folder.relative_to(ROOT).as_posix())}</code>.</div>
  <div style="margin-top:10px"><a href="{_esc(j['url'])}" style="background:#1a56db;color:#fff;padding:7px 14px;border-radius:6px;text-decoration:none">View posting</a></div>
</div>""")
            lines.append(f"[Fit {fit.score}] {j['title']} — {j.get('company')}\n{fit.one_line}\n{j['url']}\n")
            company = re.sub(r'[\\/:*?"<>|]+', "", j.get("company") or "Job").strip()[:40] or "Job"
            for which, label in (("resume", "Resume"), ("cover", "Cover Letter")):
                if (folder / primary[which]).exists():
                    attachments.append((f"{company} - {label}.{ext}", (folder / primary[which]).read_bytes()))
        sections.append(f"""<h2 style="font-size:18px;margin:0 0 10px">Ready to review ({len(drafted)})</h2>
<p style="color:#555;margin:0 0 12px">To apply: on your PC, double-click <b>prefill.bat</b>, pick the job, check the
pre-filled form and answer anything left blank, then click Submit yourself.</p>
{''.join(cards)}""")

    if plain:
        cards = []
        for j, note in plain:
            note_html = f'<div style="color:#8a4b00;margin-top:6px;font-size:13px">{_esc(note)}</div>' if note else ""
            cards.append(f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:0 0 12px">{_job_header_html(j)}{note_html}
  <div style="margin-top:10px"><a href="{_esc(j['url'])}" style="background:#1a56db;color:#fff;padding:7px 14px;border-radius:6px;text-decoration:none">View &amp; apply</a></div>
</div>""")
            lines.append(f"{j['title']} — {j.get('company')}\n{j.get('work_type')} · {j.get('location')}\n"
                         f"{j.get('salary_text') or 'Salary not listed'}\n{j['url']}\n")
        heading = "Other new matches" if drafted else f"Found {len(plain)} new matching job{'s' if len(plain) > 1 else ''}"
        sections.append(f'<h2 style="font-size:18px;margin:18px 0 10px">{heading}</h2>{"".join(cards)}')

    if skipped_digest:
        rows = "".join(
            f'<li style="margin-bottom:6px"><a href="{_esc(s["url"])}">{_esc(s["title"])}</a> — {_esc(s["company"])}'
            f' <span style="color:#888">(fit {s["score"]})</span><br><span style="color:#666">{_esc(s["reason"])}</span></li>'
            for s in skipped_digest)
        sections.append(f"""<h2 style="font-size:16px;margin:18px 0 6px;color:#666">Skipped by the agent ({len(skipped_digest)})</h2>
<p style="color:#888;font-size:13px;margin:0 0 6px">Passed your filters but scored below your bar. Shown so you can check its judgment
(turn off with <code>show_skipped</code> in config.toml).</p><ul style="padding-left:18px;font-size:14px">{rows}</ul>""")

    body_html = f"""<div style="font-family:Arial,sans-serif;max-width:640px">
{''.join(sections)}
<p style="color:#999;font-size:12px">Sent by your job search agent. Edit config.toml and profile/profile.toml to change what's matched.</p>
</div>"""
    return subject, "\n".join(lines), body_html, attachments


ATTACHMENT_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "pdf": "application/pdf"}


def send_email(subject, text, body_html, attachments=()):
    sender = os.environ["GMAIL_ADDRESS"]
    to = os.environ.get("ALERT_TO") or sender
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Job Alerts <{sender}>", to
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    for filename, data in attachments:
        mime = ATTACHMENT_TYPES.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


# ---------- AI review ----------

def review_with_agent(jobs, agent_cfg):
    """Run apply_agent on the matches. If it can't load at all, fall back to plain alerts."""
    empty = {"drafted": [], "skipped": [], "plain": [], "deferred": []}
    if not jobs:
        return empty
    try:
        import apply_agent
    except ImportError as exc:
        print(f"[agent] couldn't load ({exc}); sending plain alerts")
        return dict(empty, plain=[(j, "") for j in jobs])
    return apply_agent.run(jobs, agent_cfg)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    include_re = phrase_regex(config["titles"]["include"])
    exclude_re = phrase_regex(config["titles"].get("exclude", []))
    city_re = phrase_regex(config["location"]["cities"])
    not_local_re = phrase_regex(config["location"].get("exclude_places", []))
    src, sal = config["sources"], config["salary"]

    sources = {
        "adzuna_local": lambda: adzuna(src["adzuna_local"], remote=False),
        "adzuna_remote": lambda: adzuna(src["adzuna_remote"], remote=True),
        "jooble": lambda: jooble(src["jooble"], sal),
        "remotive": lambda: remotive(src["remotive"], sal),
    }

    state = load_state()
    today = now().strftime("%Y-%m-%d")
    found, attempted, failed = [], 0, []
    for name, fetch in sources.items():
        if not is_due(state, name, src[name]["every_minutes"], args.force):
            continue
        attempted += 1
        try:
            jobs = fetch()
        except Exception as exc:  # one bad source shouldn't stop the others
            failed.append(name)
            print(f"[{name}] ERROR: {exc}")
            continue
        if jobs is None:
            attempted -= 1
            print(f"[{name}] skipped (no API key set)")
            continue
        state["last_checked"][name] = now().isoformat()
        print(f"[{name}] {len(jobs)} postings fetched")
        found.extend(jobs)

    matches, rejected = [], {}
    for job in found:
        dup_key = "tc:" + normalize(job["title"]) + "|" + normalize(job["company"])
        if job["id"] in state["seen"] or dup_key in state["seen"]:
            continue
        ok, label = check_job(job, config, include_re, exclude_re, city_re, not_local_re)
        state["seen"][job["id"]] = today
        if not ok:
            rejected[label] = rejected.get(label, 0) + 1
            continue
        state["seen"][dup_key] = today
        job["work_type"] = label
        matches.append(job)

    print(f"New matches: {len(matches)}. Skipped new postings by reason: {rejected or 'none'}")
    for j in matches:
        print(f"  + {j['title']} | {j['company']} | {j['work_type']} | {j['location']} | "
              f"{j['salary_text'] or 'salary not listed'}\n    {j['url']}")

    if args.dry_run:
        print("(dry run: no AI review, no email sent, nothing saved)")
        return

    # Jobs left over from earlier runs (over the per-run draft limit) go first.
    week_ago = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
    queue = [j for j in state.get("queue", []) if j.get("queued", today) >= week_ago]
    to_review = queue + matches
    result = review_with_agent(to_review, config.get("agent", {}))
    state["queue"] = [dict(j, queued=j.get("queued", today)) for j in result["deferred"]]

    digest = state.get("skipped_digest", [])
    if config.get("agent", {}).get("show_skipped", True):
        for j, fit in result["skipped"]:
            reason = fit.one_line
            if fit.dealbreakers_hit:
                reason = "Dealbreaker: " + "; ".join(fit.dealbreakers_hit)
            digest.append({"title": j["title"], "company": j.get("company") or "", "url": j["url"],
                           "score": fit.score, "reason": reason})
    digest = digest[-50:]

    if result["drafted"] or result["plain"]:
        send_email(*build_email(result, digest))
        print("Email sent.")
        digest = []
    state["skipped_digest"] = digest  # skipped-only runs don't email; they ride along with the next one
    save_state(state)

    if attempted and len(failed) == attempted:
        sys.exit("Every source failed this run; see errors above.")


if __name__ == "__main__":
    main()
