# deadline-sync

Pulls due dates from **Google Classroom** and **Jira** into **Google Calendar**, each with a popup reminder 1 hour before the deadline. Idempotent — run it as often as you like.

---

## What it does

| | |
|---|---|
| Classroom | Active courses → coursework with a `dueDate`. Skips anything already `TURNED_IN` or `RETURNED`. |
| Jira | Issues matching your JQL (default: assigned to you, has a due date, not Done). |
| Calendar | Creates a 30-min event at the deadline with a popup reminder 60 min before. |
| Re-runs | Updates the event if the due date or title changed. Deletes future events for items that disappeared (submitted / closed). |

Every event it creates is tagged with private extended properties, so it never touches events you made yourself, and never duplicates its own.

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
cp .env.example .env      # then edit .env
```

### 2. Google credentials (one time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project.
2. **APIs & Services → Library** → enable **Google Calendar API** and **Google Classroom API**.
3. **APIs & Services → OAuth consent screen** → External → add yourself under *Test users*.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → download JSON → save as `credentials.json` next to the script.

### 3. Jira API token

[id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) → Create token → paste into `JIRA_API_TOKEN` in `.env`. Set `JIRA_BASE_URL` to your site, e.g. `https://acme.atlassian.net`.

Leave the Jira vars blank if you only want Classroom.

### 4. First run — always dry-run first

```bash
python deadline_sync.py --dry-run --verbose
```

**If your Classroom is on a school/university account and you want events on a different personal Google Calendar**, the script needs two separate logins — one OAuth *client* (`credentials.json`), used twice:

- First browser popup: log in with your **school account** → grants Classroom read access → saved to `classroom_token.json`.
- Second browser popup: log in with your **personal Gmail** → grants Calendar write access → saved to `calendar_token.json`.

You'll see two separate "opening browser" log lines telling you which account to use for each. After that first run, both tokens refresh themselves silently — this is what makes unattended cron runs work without you touching a browser again.

If Classroom and Calendar are the *same* Google account, just log in the same way both times — no extra setup needed.

Read the `CREATE` lines. **Check the times against what Classroom/Jira actually show you** before you let it write anything. When they look right:

```bash
python deadline_sync.py
```

---

## Running it on a schedule

### macOS / Linux (cron)

```bash
crontab -e
```

```cron
# every day at 7am and 7pm
0 7,19 * * * cd /full/path/to/deadline-sync && /usr/bin/python3 deadline_sync.py >> sync.log 2>&1
```

Use absolute paths — cron has almost no environment. Confirm your python path with `which python3`.

### Windows (Task Scheduler)

1. Task Scheduler → **Create Basic Task** → Daily.
2. Action: *Start a program*
   - Program: `C:\Path\To\python.exe`
   - Arguments: `deadline_sync.py`
   - **Start in:** `C:\Path\To\deadline-sync`  ← required, or `.env` and `token.json` won't be found.
3. In task properties tick *Run whether user is logged on or not*.

---

## Flags

```
--dry-run          print what would change, write nothing
--verbose / -v     debug logging
--skip-classroom
--skip-jira
```

## Testing

```bash
python test_logic.py
```

Covers timezone conversion, date-only handling, the change-detection diff, and Jira field mapping — no network, no credentials needed.
