# Job Search Agent

Every 30 minutes, GitHub checks job sites for new **Project Manager / Business Operations** postings
that are remote (open to Canada) or within about 45 minutes of Dundas, and that don't list a salary under $85K.
Then an AI agent (Claude) takes over:

1. **Reads the full posting** and scores how well it fits your resume, salary target and dealbreakers (0–100).
2. **Skips poor fits.** For good ones, it **writes a tailored resume and cover letter** as Word documents,
   using only experience that's really on your resume or in `profile/about_me.md`. It never invents anything.
3. **Emails you** each drafted application with the fit score, why you fit, any gaps, and the Word files attached.
4. **You approve:** double-click `prefill.bat` on your PC, pick the job, and it opens the application form with
   your details, resume and cover letter already filled in. You check it and click **Submit** yourself.

Nothing is ever submitted without you.

| File | What it is |
|---|---|
| `profile/<your resume>.docx` | **Your master resume** (you add this). Everything tailored comes from here. |
| `profile/profile.toml` | **About you**: contact details, salary target, what you want, dealbreakers. |
| `profile/about_me.md` | **Your stories, in your own words.** Read for every application, so letters sound like you. |
| `config.toml` | **Search settings**: job titles, cities, salary floor, and the `[agent]` settings. |
| `job_alert.py` | Finds and filters jobs, then hands matches to the agent and emails you. |
| `apply_agent.py` | The AI agent: scores fit and writes the tailored documents. |
| `documents.py` | Lays out the resume and cover letter as Word files (or PDF, if you choose). |
| `prefill.bat` / `prefill.py` | Runs on your PC: opens an application form pre-filled. |
| `applications/` | Created automatically. One folder per drafted application. |
| `state/seen.json` | Created automatically. Jobs already handled, so you don't get repeats. |

Sources: **Adzuna** (main source, key required), **Jooble** (optional, key required), **Remotive** (remote jobs, no key).

---

## Setup

If the plain job alerts are already running, skip to step 3.

### 1. Get your keys
- **Adzuna:** at https://developer.adzuna.com, sign up and copy your **App ID** and **App Key**.
- **Gmail app password:** turn on 2-Step Verification for your Google account if it isn't already, then go to
  https://myaccount.google.com/apppasswords, create one named "Job alerts", and copy the 16-character password.
- **Jooble (optional):** request a key at https://jooble.org/api/about.

### 2. Create a private GitHub repository
1. On GitHub, click **+ → New repository**.
2. Name it `job-search-tool`, choose **Private** (important: it will hold your resume), and click **Create repository**.

### 3. Add your resume and profile
1. Save your current resume into the `profile` folder as a `.docx` (best) or `.pdf`. Any file name works.
   Make it complete. The agent can trim and reorder, but it can only use what's there.
2. Open `profile/profile.toml` and fill in everything marked `FILL IN`, plus your salary target,
   what you want and your dealbreakers.

### 4. Get a Claude API key
1. Go to https://console.anthropic.com, sign up, and add a payment method under **Billing**.
2. Under **Limits**, set a monthly spend limit (e.g. $20) so costs can't surprise you.
3. Under **API keys**, create a key named "Job agent" and copy it.

### 5. Upload the files
1. On your repo page, click **Add file → Upload files**.
2. Open this folder in File Explorer, select **everything inside it** (including the `.github` and `profile`
   folders), and drag it onto the page. If `.github` is hidden, turn on View → Show → Hidden items.
3. Click **Commit changes**. Check the repo shows `.github/workflows/job-alert.yml` and your resume inside `profile`.

### 6. Add your secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add each of these:

| Name | Value |
|---|---|
| `ADZUNA_APP_ID` | your Adzuna App ID |
| `ADZUNA_APP_KEY` | your Adzuna App Key |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password (spaces are fine) |
| `ANTHROPIC_API_KEY` | your Claude API key |
| `ALERT_TO` | *(optional)* a different address to send alerts to |
| `JOOBLE_KEY` | *(optional)* your Jooble key |

### 7. Turn it on and test
1. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Job alerts → Run workflow → Run workflow**.
3. After a few minutes, click the run to see its log. Each job shows its fit score, and drafted ones show where
   their files were saved.

### 8. Set up pre-fill on your PC (once)
1. Double-click **`prefill.bat`**. The first time, it installs a browser helper (a minute or two).
2. It asks for your repo name (e.g. `yourname/job-search-tool`) and a GitHub token. To make the token, go to
   https://github.com/settings/personal-access-tokens/new, choose **Only select repositories → job-search-tool**,
   set **Permissions → Contents** to **Read and write**, and generate it. It's saved on this PC only.

---

## Applying to a job
1. When an "applications ready to review" email arrives, read the fit notes and open the attached Word files.
2. Double-click **`prefill.bat`** and pick the job. Its documents open in Word so you can check them. If you
   edit one, save it under the same name and close it before continuing. Your edits are what gets uploaded.
3. A browser opens at the posting. Click Apply (sign in if the site asks) until you see the form, then go back
   to the black window and press **Enter**. It fills your name, email, phone, LinkedIn, city, resume and cover letter.
4. Answer the remaining questions (screening questions, checkboxes, dropdowns are left to you), then click **Submit**.
5. Tell the window whether you submitted. That status is saved so the job leaves your list.

The browser remembers your sign-ins, so sites like Workday stay signed in next time.

## Tuning
In `config.toml` under `[agent]`:
- **`min_fit_score`** (default 70): raise it for fewer, stronger drafts; lower it for more.
- **`max_drafts_per_run`** (default 5): extras wait for the next run.
- **`formats`** (default `["docx"]`): Word files. Use `["pdf"]` for PDF, or `["docx", "pdf"]` for both
  (the first one listed is emailed and uploaded). If a form only accepts PDF, pre-fill tells you; in Word, use
  **File → Save As → PDF** and upload that one by hand.
- **`show_skipped`**: each email ends with the jobs the agent passed on, and why. Check them for the first week
  or two. If it's skipping jobs you'd want, adjust `profile.toml` (often the dealbreakers or notes).

In `profile/profile.toml`: your target salary, what you want, dealbreakers, and notes such as a career change
or industries to avoid. Better notes mean better scoring and better letters.

To try the agent on a single posting from your PC, set `ANTHROPIC_API_KEY` in your terminal and run
`python apply_agent.py --url <posting link>`.

## Costs and notes
- **Claude:** about $0.03–0.06 to score each match and $0.10–0.20 to draft a good one. At 10–20 matches a day
  that's roughly $15–40 a month. The spend limit from step 4 caps it, and tightening the titles in
  `config.toml` (fewer matches to score) is the easiest way to lower it.
- **GitHub:** private repos get 2,000 free Actions minutes a month. Every 30 minutes is about 1,500, and
  drafting adds a little. If GitHub warns you're near the limit, change `*/30` to `0 *` in
  `.github/workflows/job-alert.yml` to run hourly.
- **Privacy:** your resume and drafts live in your private repo. Your resume and each posting are sent to
  Anthropic's API to do the scoring and writing.
- If the Claude key is missing or a step fails, you still get the plain job alert, so nothing is lost.
- Salary and remote detection rely on what each posting says. Postings with no salary listed get a salary
  judgment from the agent instead.
- Pre-fill handles most standard forms (Greenhouse, Lever, Ashby and similar). On unusual sites it may fill
  little or nothing, so fill those by hand using the downloaded files.
