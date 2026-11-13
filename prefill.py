"""Review a drafted application and open its form pre-filled. You click Submit yourself.

Run on your PC (double-click prefill.bat, or: python prefill.py).

1. Lists the applications the agent has drafted (read from your private GitHub repo).
2. You pick one; its resume and cover letter download and open so you can check them.
3. A browser opens at the job. Get to the application form, press Enter here, and it fills
   in your name, email, phone, LinkedIn, city, resume and cover letter.
4. You answer anything left, click Submit, and tell it whether you applied. That status is
   saved back to the repo so the list stays tidy.

It never clicks Submit, and it leaves questions, checkboxes and dropdowns to you.

Settings (your repo name and a GitHub token) are kept in %USERPROFILE%\\.job-agent\\settings.toml.
Use --local to read applications/ from this folder instead of GitHub.
"""

import argparse
import base64
import getpass
import json
import os
import re
import shutil
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
HOME = Path.home() / ".job-agent"
SETTINGS = HOME / "settings.toml"
CACHE = HOME / "applications"
BROWSER_PROFILE = HOME / "browser"   # keeps you signed in to job sites between runs


# ---------- where applications live ----------

class GitHubStore:
    def __init__(self, repo, token):
        self.repo, self.token = repo, token

    def _req(self, path, method="GET", body=None, raw=False):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}/contents/{path}",
            data=json.dumps(body).encode() if body else None, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "job-agent-prefill"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        return data if raw else json.loads(data)

    def list(self):
        try:
            entries = self._req("applications")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        apps = []
        for e in entries:
            if e["type"] != "dir":
                continue
            meta = self._req(f"applications/{e['name']}/job.json")
            record = json.loads(base64.b64decode(meta["content"]))
            apps.append({"name": e["name"], "record": record, "sha": meta["sha"]})
        return apps

    def download(self, app, filename, dest):
        dest.write_bytes(self._req(f"applications/{app['name']}/{filename}", raw=True))

    def save(self, app):
        content = json.dumps(app["record"], indent=2, ensure_ascii=False).encode("utf-8")
        result = self._req(f"applications/{app['name']}/job.json", method="PUT", body={
            "message": f"Mark {app['name']} as {app['record']['status']}",
            "content": base64.b64encode(content).decode(), "sha": app["sha"]})
        app["sha"] = result["content"]["sha"]


class LocalStore:
    def __init__(self, folder):
        self.folder = folder

    def list(self):
        apps = []
        for job_file in sorted(self.folder.glob("*/job.json")):
            apps.append({"name": job_file.parent.name,
                         "record": json.loads(job_file.read_text(encoding="utf-8"))})
        return apps

    def download(self, app, filename, dest):
        shutil.copyfile(self.folder / app["name"] / filename, dest)

    def save(self, app):
        (self.folder / app["name"] / "job.json").write_text(
            json.dumps(app["record"], indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings():
    if SETTINGS.exists():
        return tomllib.loads(SETTINGS.read_text(encoding="utf-8"))
    print("First-time setup. This connects to the private GitHub repo the agent saves drafts to.\n")
    repo = input("Your repo, as owner/name (e.g. janedoe/job-search-tool): ").strip()
    print("\nCreate a token at https://github.com/settings/personal-access-tokens/new")
    print("  Repository access: Only select repositories -> your job search repo")
    print("  Permissions: Contents -> Read and write. Nothing else.")
    token = getpass.getpass("Paste the token (it won't show as you paste): ").strip()
    HOME.mkdir(exist_ok=True)
    SETTINGS.write_text(f'repo = "{repo}"\ntoken = "{token}"\n', encoding="utf-8")
    print(f"Saved to {SETTINGS}\n")
    return {"repo": repo, "token": token}


# ---------- filling the form ----------

FIND_FIELDS_JS = r"""
() => {
  const skip = ['hidden','submit','button','image','reset','checkbox','radio','password','search','range','color'];
  const out = [];
  let i = 0;
  const textOf = n => (n && (n.innerText || n.textContent) || '').trim();
  for (const el of document.querySelectorAll('input, textarea')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (skip.includes(type) || el.disabled || el.readOnly) continue;
    const r = el.getBoundingClientRect();
    if (type !== 'file' && (r.width === 0 || r.height === 0)) continue;
    const parts = [];
    if (el.labels) for (const l of el.labels) parts.push(textOf(l));
    for (const a of ['aria-label', 'placeholder', 'name', 'id', 'autocomplete', 'data-qa'])
      parts.push(el.getAttribute(a) || '');
    const lb = el.getAttribute('aria-labelledby');
    if (lb) for (const id of lb.split(/\s+/)) parts.push(textOf(document.getElementById(id)));
    // Text around the field, for unlabelled inputs (e.g. hidden file inputs behind an "Attach" button)
    let ctx = '', node = el.parentElement;
    for (let up = 0; up < 4 && node && ctx.length < 3; up++, node = node.parentElement) ctx = textOf(node).slice(0, 160);
    const key = 'ja' + (i++);
    el.setAttribute('data-jobagent', key);
    out.push({key, type, tag: el.tagName.toLowerCase(), label: parts.join(' | ').toLowerCase(),
              context: ctx.toLowerCase(), filled: !!el.value});
  }
  return out;
}
"""

TEXT_RULES = [  # (what, regex on the field's label), first match wins
    ("email", r"e-?mail"),
    ("linkedin", r"linked\s*in"),
    ("first_name", r"first.?name|given.?name|\bfname\b|preferred first"),
    ("last_name", r"last.?name|surname|family.?name|\blname\b"),
    ("phone", r"phone|mobile|\btel\b|cell"),
    ("website", r"website|portfolio|personal.?(site|url)"),
    ("city", r"\bcity\b|current location|^location|\| location"),
    ("full_name", r"full.?name|^name\b|\|\s*name\b|your name|legal name"),
]
NOT_ABOUT_YOU = re.compile(r"company|employer|school|university|reference|manager|referr|emergency", re.I)


def fill_page(page, contact, files, cover_text):
    values = {
        "email": contact.get("email"), "linkedin": contact.get("linkedin"),
        "first_name": contact.get("first_name"), "last_name": contact.get("last_name"),
        "full_name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
        "phone": contact.get("phone"), "website": contact.get("website"), "city": contact.get("city"),
    }
    done, used_files = [], set()
    for frame in page.frames:
        try:
            fields = frame.evaluate(FIND_FIELDS_JS)
        except Exception:
            continue   # cross-origin frame that isn't ready, etc.
        file_fields = [f for f in fields if f["type"] == "file"]
        for f in fields:
            loc = frame.locator(f'[data-jobagent="{f["key"]}"]')
            try:
                if f["type"] == "file":
                    text = f["label"] + " " + f["context"]
                    if re.search(r"cover", text):
                        which = "cover"
                    elif re.search(r"resume|résumé|\bcv\b|curriculum", text) or len(file_fields) == 1:
                        which = "resume"
                    else:
                        continue
                    if which in used_files:
                        continue
                    ext = Path(files[which]).suffix.lower()
                    accept = (loc.get_attribute("accept", timeout=5000) or "").lower()
                    if accept and ext not in accept and "*" not in accept and not (
                            ext == ".docx" and "wordprocessingml" in accept):
                        print(f"    This form's {which} upload only takes {accept}. In Word, use File > Save As "
                              f"> PDF, then upload that file by hand.")
                        continue
                    loc.set_input_files(str(files[which]), timeout=5000)
                    used_files.add(which)
                    done.append(f"{'Resume' if which == 'resume' else 'Cover letter'} (file)")
                    continue
                if f["filled"]:
                    continue   # never overwrite something already there
                if f["tag"] == "textarea":
                    if re.search(r"cover", f["label"]) and cover_text:
                        loc.fill(cover_text, timeout=5000)
                        done.append("Cover letter (text box)")
                    continue
                if NOT_ABOUT_YOU.search(f["label"]):
                    continue
                what = "email" if f["type"] == "email" else "phone" if f["type"] == "tel" else None
                if not what:
                    what = next((w for w, rx in TEXT_RULES if re.search(rx, f["label"])), None)
                if what and values.get(what):
                    loc.fill(values[what], timeout=5000)
                    done.append(what.replace("_", " ").capitalize())
            except Exception as exc:
                print(f"    couldn't fill one field ({type(exc).__name__}); do that one by hand")
    return done


# ---------- the session ----------

def pick(apps):
    drafted = [a for a in apps if a["record"].get("status") == "drafted"]
    drafted.sort(key=lambda a: -a["record"]["fit"]["score"])
    if not drafted:
        print("No drafted applications waiting. New ones arrive by email when the agent finds good fits.")
        return None
    print(f"\n{len(drafted)} application(s) waiting for you:\n")
    for i, a in enumerate(drafted, 1):
        r = a["record"]
        print(f"  {i:>2}. [fit {r['fit']['score']}] {r['job']['title']} - {r['job'].get('company') or '?'}"
              f"   ({r['drafted_at'][:10]})")
    while True:
        choice = input("\nPick a number (or q to quit): ").strip().lower()
        if choice in ("q", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(drafted):
            return drafted[int(choice) - 1]


def prepare_files(store, app, contact):
    """Download into a local folder with nice upload names. Never overwrites, so your edits stick."""
    folder = CACHE / app["name"]
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip() or "Resume"
    files = app["record"].get("files", [])
    ext = "docx" if "resume.docx" in files else "pdf"   # Word unless the draft was made as PDF only
    sources = {"resume": f"resume.{ext}", "cover": f"cover_letter.{ext}"}
    targets = {"resume": f"{name} - Resume.{ext}", "cover": f"{name} - Cover Letter.{ext}"}
    for which, target in targets.items():
        if not (folder / target).exists():
            store.download(app, sources[which], folder / target)
    return folder, {k: folder / v for k, v in targets.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="read applications/ from this folder")
    args = ap.parse_args()

    profile = tomllib.loads((ROOT / "profile" / "profile.toml").read_text(encoding="utf-8"))
    contact = profile["contact"]
    if args.local:
        store = LocalStore(ROOT / "applications")
    else:
        s = load_settings()
        store = GitHubStore(s["repo"], s["token"])

    try:
        apps = store.list()
    except urllib.error.HTTPError as exc:
        sys.exit(f"GitHub said {exc.code}. Check the repo name and token in {SETTINGS} "
                 "(delete that file to set them up again).")
    app = pick(apps)
    if not app:
        return
    r = app["record"]
    print(f"\n{r['job']['title']} - {r['job'].get('company')}\nFit {r['fit']['score']}: {r['fit']['one_line']}")
    for note in r.get("what_was_tailored", []):
        print(f"  - {note}")

    folder, files = prepare_files(store, app, contact)
    print(f"\nYour documents are in {folder}")
    print("Opening them now. If you edit one, save it (same name) and close it before continuing.")
    for f in files.values():
        os.startfile(f)   # Windows: opens in Word (or your PDF viewer)
    if input("\nReady to open the application? [Enter = yes, s = skip this job]: ").strip().lower() == "s":
        r["status"] = "skipped"
        store.save(app)
        print("Marked as skipped.")
        return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(str(BROWSER_PROFILE), headless=False,
                                                        no_viewport=True, args=["--start-maximized"])
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(r["apply_url"], wait_until="domcontentloaded")
        print("\nIn the browser: click the job's Apply button if needed (sign in or create an account")
        print("if the site asks) until you can see the application form.")
        while True:
            cmd = input("\n[Enter] fill the form on screen   [d] I'm done: ").strip().lower()
            if cmd == "d":
                break
            page = browser.pages[-1]   # the newest tab, in case Apply opened one
            page.wait_for_load_state("domcontentloaded")
            filled = fill_page(page, contact, files, r.get("cover_letter_text", ""))
            if filled:
                print("Filled: " + ", ".join(dict.fromkeys(filled)))
            else:
                print("Found nothing to fill on this page. Is the form showing?")
            print("Check every field, answer the remaining questions, then click Submit yourself.")
            print("Multi-page form? Go to the next page and press Enter again.")
        answer = input("\nDid you submit it? [y] yes   [l] not yet, keep it in the list   [s] skip this job: ")
        browser.close()

    answer = answer.strip().lower()
    if answer in ("y", "s"):
        from datetime import datetime, timezone
        r["status"] = "applied" if answer == "y" else "skipped"
        r[f"{r['status']}_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.save(app)
        print(f"Marked as {r['status']}.")
    else:
        print("Kept in the list for later.")


if __name__ == "__main__":
    main()
