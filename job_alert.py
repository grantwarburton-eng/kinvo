"""Checks job sites for new Project Manager / Business Operations postings and emails matches.

Settings live in config.toml. Secrets come from environment variables:
  ADZUNA_APP_ID, ADZUNA_APP_KEY   (required for Adzuna)
  JOOBLE_KEY                      (optional)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD
  ALERT_TO                        (optional, defaults to GMAIL_ADDRESS)

Usage:
  python job_alert.py              normal run (emails new matches, remembers them)
  python job_alert.py --dry-run    print matches only; no email, nothing remembered
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

def build_email(matches):
    count = len(matches)
    first = matches[0]
    if count == 1:
        subject = f"New job: {first['title']} at {first['company'] or 'unknown company'}"
    else:
        subject = f"{count} new jobs: {first['title']} at {first['company'] or 'unknown'} + {count - 1} more"

    cards, lines = [], []
    for j in matches:
        salary = j["salary_text"] or "Salary not listed"
        e = {k: html.escape(str(v)) for k, v in j.items()}
        cards.append(f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:0 0 12px">
  <div style="font-size:17px;font-weight:600"><a href="{e['url']}" style="color:#1a56db;text-decoration:none">{e['title']}</a></div>
  <div style="color:#333;margin-top:4px">{e['company'] or 'Company not listed'}</div>
  <div style="color:#555;margin-top:4px">{e['work_type']} · {e['location'] or 'Location not listed'}</div>
  <div style="color:#555;margin-top:4px">{html.escape(salary)}</div>
  <div style="margin-top:10px"><a href="{e['url']}" style="background:#1a56db;color:#fff;padding:7px 14px;border-radius:6px;text-decoration:none">View &amp; apply</a></div>
</div>""")
        lines.append(f"{j['title']} — {j['company']}\n{j['work_type']} · {j['location']}\n{salary}\n{j['url']}\n")

    body_html = f"""<div style="font-family:Arial,sans-serif;max-width:640px">
<p style="color:#555">Found {count} new matching job{'s' if count > 1 else ''}:</p>
{''.join(cards)}
<p style="color:#999;font-size:12px">Sent by your job alert tool. Edit config.toml to change what's matched.</p>
</div>"""
    return subject, "\n".join(lines), body_html


def send_email(subject, text, body_html):
    sender = os.environ["GMAIL_ADDRESS"]
    to = os.environ.get("ALERT_TO") or sender
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Job Alerts <{sender}>", to
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


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
        print("(dry run: no email sent, nothing saved)")
        return

    if matches:
        send_email(*build_email(matches))
        print("Email sent.")
    save_state(state)

    if attempted and len(failed) == attempted:
        sys.exit("Every source failed this run; see errors above.")


if __name__ == "__main__":
    main()
