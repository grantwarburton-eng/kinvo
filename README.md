# Job Search Tool

Every 30 minutes, GitHub checks job sites for new **Project Manager / Business Operations** postings
that are remote (open to Canada) or within about 45 minutes of Dundas. Jobs are skipped only if they list
a salary topping out under $85K. Matches are emailed to you, and each job is only ever sent once.

| File | What it is |
|---|---|
| `config.toml` | **Your settings**: job titles, cities, salary floor. Edit this to change what you get. |
| `job_alert.py` | The checker itself. |
| `.github/workflows/job-alert.yml` | Tells GitHub to run the checker every 30 minutes. |
| `state/seen.json` | Created automatically. A list of jobs already sent, so you don't get repeats. |

Sources: **Adzuna** (main source, key required), **Jooble** (optional, key required), **Remotive** (remote jobs, no key).

---

## One-time setup (about 15 minutes)

### 1. Get your keys
- **Adzuna:** at https://developer.adzuna.com, sign up and copy your **App ID** and **App Key**.
- **Gmail app password:** turn on 2-Step Verification for your Google account if it isn't already, then go to
  https://myaccount.google.com/apppasswords, create one named "Job alerts", and copy the 16-character password.
  (This is a separate password just for this tool. Your normal password is never used.)
- **Jooble (optional):** request a key at https://jooble.org/api/about.

### 2. Create a private GitHub repository
1. On GitHub, click **+ → New repository**.
2. Name it `job-search-tool`, choose **Private**, and click **Create repository**.

### 3. Upload the files
1. On the new repo page, click **uploading an existing file**.
2. Open this folder in File Explorer, select **everything inside it** (including the `.github` folder),
   and drag it onto the page. If `.github` is hidden, turn on View → Show → Hidden items.
3. Click **Commit changes**.
4. Check that the repo now shows `.github/workflows/job-alert.yml`. If the `.github` folder didn't upload, click
   **Add file → Create new file**, type `.github/workflows/job-alert.yml` as the name, and paste in that file's contents.

### 4. Add your secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add each of these:

| Name | Value |
|---|---|
| `ADZUNA_APP_ID` | your Adzuna App ID |
| `ADZUNA_APP_KEY` | your Adzuna App Key |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password (spaces are fine) |
| `ALERT_TO` | *(optional)* a different address to send alerts to |
| `JOOBLE_KEY` | *(optional)* your Jooble key |

Secrets are encrypted. Nobody, including you, can view them after saving.

### 5. Turn it on and test
1. Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Job alerts → Run workflow → Run workflow**.
3. After about a minute, click the run to see its log. It lists what was found and why other postings were skipped.
   If there are matches from the last few days, you'll get your first email right away.

From then on it runs on its own every 30 minutes. If a run fails, GitHub emails you.

---

## Changing what you get
Edit `config.toml` on GitHub (click the file, then the pencil icon), and commit. For example:
- **Add a title:** add a line to `include`, e.g. `"implementation manager",`
- **Drop a field:** add it to `exclude`, e.g. `"construction",`
- **Add a city:** remove the `#` in front of `"Kitchener",` or add a new one.
- **Change the salary floor:** edit `minimum`.

## Notes
- Private repos get 2,000 free GitHub Actions minutes a month. A 30-minute schedule uses about 1,500.
- Adzuna's free key has daily request limits, so the schedule in `config.toml` keeps usage low.
  If you see "429" errors in the log, increase the `every_minutes` values.
- Salary and remote detection rely on what each posting says, so the occasional miss gets through. Postings with
  no salary listed are always sent, marked "Salary not listed."
