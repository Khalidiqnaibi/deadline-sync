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

A browser opens once for consent; after that `token.json` refreshes itself silently (which is what makes unattended cron runs work).

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

---

## Things to watch (verify these against your own data)

These are the parts most likely to be subtly wrong for *your* setup:

- **Classroom `dueTime` is UTC.** The script converts to your `TIMEZONE`. If an assignment shows on the wrong day by a few hours, `TIMEZONE` in `.env` is wrong. An 11:59 PM EDT deadline arrives from the API as 03:59 UTC *the next day* — easy to get backwards.
- **Jira `duedate` has no time component.** Everything from Jira lands at `DATE_ONLY_DUE_TIME` (default 23:59). If your team treats due dates as start-of-day, change it to `09:00`.
- **`DELETE_COMPLETED=true`** removes future events for items that fall out of your JQL or get submitted. If you edit an event by hand, the next run will overwrite or delete it. Set to `false` if that bothers you.
- **The Jira search endpoint.** Jira Cloud moved from `/rest/api/3/search` to `/rest/api/3/search/jql`. The script tries the new one and falls back on 404/410. If your instance does something else, run with `--verbose` and check.
