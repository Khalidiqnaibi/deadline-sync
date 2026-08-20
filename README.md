<div align="center">

# deadline-sync

**Your Google Classroom assignments and Jira tickets, on one calendar, with a reminder an hour before every deadline.**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-lightgrey.svg)]()

</div>

---

## The problem

Deadlines live in too many places. Assignments are in Google Classroom. Work tickets are in Jira. The calendar you actually check is neither of them. So you either keep three tabs open forever, or you copy due dates across by hand and eventually forget one.

`deadline-sync` reads both sources and writes real calendar events — with a popup reminder 60 minutes before each deadline — into the calendar you already look at. Run it on a schedule and forget it exists.

```
┌──────────────────┐
│ Google Classroom │──┐
│  (school acct)   │  │
└──────────────────┘  │      ┌───────────────┐      ┌──────────────────┐
                      ├─────▶│ deadline-sync │─────▶│ Google Calendar  │
┌──────────────────┐  │      └───────────────┘      │ (personal acct)  │
│      Jira        │──┘         dedupe · update      │  + 1h reminder   │
│   (via JQL)      │            delete when done     └──────────────────┘
└──────────────────┘
```

---

## Features

| | |
|---|---|
| 🎓 **Classroom** | Pulls coursework from all active courses. Automatically skips anything you've already turned in or that's been returned. |
| 🎫 **Jira** | Pulls issues matching a JQL query you control. Defaults to *assigned to me, has a due date, not Done*. |
| ⏰ **Reminders** | Every event gets a popup notification 60 minutes before the deadline. Configurable. |
| 🔁 **Idempotent** | Run it a hundred times, get one event per deadline. Re-runs update times and titles if they changed upstream. |
| 🧹 **Self-cleaning** | Submit an assignment or close a ticket, and the next run removes the stale event. |
| 🌍 **Timezone-correct** | Classroom returns due times in UTC. They're converted to your local zone, not pasted in raw. |
| 🔒 **Non-destructive** | Only ever touches events it created itself, tagged via private extended properties. Your own events are invisible to it. |
| 🧪 **Dry-run mode** | See exactly what would change before anything is written. |

---

## Quick start

```bash
git clone https://github.com/yourname/deadline-sync.git
cd deadline-sync
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
```

Then follow **[Connecting the APIs](#connecting-the-apis)** below to get your `credentials.json`, and run:

```bash
python src/deadline_sync.py --dry-run --verbose
```

> [!IMPORTANT]
> **Always dry-run first.** It prints every event it *would* create without writing anything. Check the dates and times against what Classroom and Jira actually show you before you let it write to your calendar.

When the output looks right, drop the flag:

```bash
python src/deadline_sync.py
```

---

## Connecting the APIs

You need two things: a Google OAuth client, and a Jira API token. Budget about ten minutes.

### Part 1 — Google (Classroom + Calendar)

Classroom and Calendar are both Google APIs, so **one OAuth client covers both** — even if they're on different Google accounts. You create the client once, then log in twice: once as the account that has your Classroom, once as the account whose calendar you want to write to.

<details open>
<summary><b>Step 1 · Create a project and enable the APIs</b></summary>

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)**.
2. Top bar → **Select a project → New Project**. Name it anything (`deadline-sync` works). Create.
3. Left menu → **APIs & Services → Library**.
4. Search **"Google Classroom API"** → **Enable**.
5. Search **"Google Calendar API"** → **Enable**.

> Confirm both now read *"API Enabled"* rather than still showing an **Enable** button.

</details>

<details open>
<summary><b>Step 2 · Configure the OAuth consent screen</b></summary>

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** → Create.
3. Fill in App name and the two required email fields. Save and continue.
4. On the **Scopes** / **Data access** step → **Add or remove scopes**, and add:

   ```
   .../auth/classroom.courses.readonly
   .../auth/classroom.student-submissions.me.readonly
   .../auth/calendar.events
   ```

5. On the **Test users** step → **Add users** → add **every account you'll log in with**:
   - your school account (the one with Classroom)
   - your personal account (the one with the calendar)

> [!WARNING]
> Missing test users is the single most common setup failure. An account that isn't on this list will be refused at the login screen, and the error message won't tell you why.

</details>

<details open>
<summary><b>Step 3 · Create the OAuth client</b></summary>

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app** → Create.
3. Click the download icon on the client you just made.
4. Rename the downloaded file to **`credentials.json`** and place it next to `deadline_sync.py`.

</details>

<details open>
<summary><b>Step 4 · Authorize both accounts</b></summary>

Run the script. Two browser windows will open, **in this order**:

| Popup | Log in as | Grants | Saves to |
|---|---|---|---|
| 1st | your **school** account | Classroom read access | `classroom_token.json` |
| 2nd | your **personal** account | Calendar write access | `calendar_token.json` |

> [!CAUTION]
> On the second popup, check which account you're signed in as. Google will silently use whatever account is already active in that browser — and if it picks the wrong one, events go to the wrong calendar with **no error at all**.

After this once, both tokens refresh themselves silently. That's what lets the scheduled runs work unattended.

</details>

### Part 2 — Jira

<details open>
<summary><b>Generate an API token</b></summary>

1. Go to **[id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)**.
2. **Create API token** → name it → **Copy**.

> [!NOTE]
> The token is shown exactly once. Copy it before closing the dialog.

3. Add it to `.env`:

```ini
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=paste_your_token_here
```

`JIRA_BASE_URL` is whatever appears in your browser's address bar when you're logged into Jira.

</details>

> **Only want Classroom?** Leave the Jira variables blank and the script skips Jira entirely. Or pass `--skip-jira`.

---

## Configuration

All settings live in `.env`.

| Variable | Default | What it does |
|---|---|---|
| `TIMEZONE` | `Asia/Hebron` | **Set this first.** Any [IANA zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). Wrong values shift every deadline silently. |
| `CALENDAR_ID` | `primary` | `primary` = main calendar of the account you authorized. Or paste a specific calendar's ID. |
| `REMINDER_MINUTES` | `60` | Minutes before the deadline to fire the popup. |
| `EVENT_LENGTH_MINUTES` | `30` | Visual length of the block on your calendar. |
| `DATE_ONLY_DUE_TIME` | `23:59` | Jira due dates carry no time. This is the local time they're assumed due at. |
| `LOOKAHEAD_DAYS` | `120` | Ignore deadlines further out than this. |
| `DELETE_COMPLETED` | `true` | Remove future events whose source item was submitted or closed. |
| `JIRA_JQL` | *see `.env.example`* | Any valid JQL. Narrow it to a project with `AND project = ABC`. |

### Using a dedicated calendar

To keep deadlines off your main calendar, create a new one in Google Calendar first, then: **Settings → [your calendar] → Integrate calendar → Calendar ID**. Paste it into `CALENDAR_ID`.

---

## Usage

```bash
python src/deadline_sync.py [OPTIONS]
```

| Flag | Effect |
|---|---|
| `--dry-run` | Print planned changes, write nothing. |
| `--verbose`, `-v` | Debug logging — shows every API call and every skipped item with its reason. |
| `--skip-classroom` | Sync Jira only. |
| `--skip-jira` | Sync Classroom only. |

**Sample output**

```
12:04:11  INFO    Classroom: 2 active course(s)
12:04:12  DEBUG   Already submitted: MT Exam
12:04:12  INFO    Jira: 3 issue(s) with due dates
12:04:12  INFO    Collected 5 deadline(s) total
12:04:13  INFO    Calendar: 4 event(s) previously created by this tool
12:04:13  INFO    CREATE  🎫 ENG-42: Fix login redirect      ->  2026-08-24 23:59
12:04:13  INFO    UPDATE  📚 Literature Review (ENGL201)     ->  2026-08-27 23:59
12:04:13  INFO    DELETE  📚 Problem Set 3 (no longer due / completed)
12:04:13  INFO    Done. created=1 updated=1 unchanged=3 deleted=1
```

---

## Running on a schedule

The point of the tool is to never think about it. Set it to run twice a day.

<details>
<summary><b>macOS / Linux — cron</b></summary>

```bash
crontab -e
```

```cron
# 7am and 7pm daily
0 7,19 * * * cd /full/path/to/deadline-sync && /usr/bin/python3 src/deadline_sync.py >> sync.log 2>&1
```

Use **absolute paths** — cron runs with almost no environment. Find yours with `which python3`.

</details>

<details>
<summary><b>Windows — Task Scheduler</b></summary>

1. **Task Scheduler → Create Basic Task** → name it → **Daily**.
2. Action → **Start a program**:
   - **Program:** `C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe`
   - **Arguments:** `src\deadline_sync.py`
   - **Start in:** `C:\Path\To\deadline-sync`  ← **required**, or `.env` and the token files won't be found.
3. Task properties → tick **Run whether user is logged on or not**.

</details>

---

## Testing

```bash
python src/test_logic.py
```

Runs offline — no credentials, no network. Covers timezone conversion, date-only handling, title truncation, change detection, and Jira field mapping.

---

## Troubleshooting

<details>
<summary><b><code>Warning: Scope has changed from ... to ...</code></b></summary>

Google returns a **deduplicated** scope set when the scopes you requested overlap, and `oauthlib` treats any mismatch as fatal.

The script sets `OAUTHLIB_RELAX_TOKEN_SCOPE=1` to tolerate this. If you hit it anyway, you've likely added `classroom.coursework.me.readonly` back into `CLASSROOM_SCOPES` — remove it. `classroom.courses.readonly` already covers listing coursework.

</details>

<details>
<summary><b><code>Error 400: admin_policy_enforced</code> or <code>access_denied</code></b></summary>

Your school's Google Workspace admin blocks third-party OAuth apps for student accounts. This is enforced server-side and there's no client-side workaround — you'd need IT to allowlist the app.

</details>

<details>
<summary><b>Events appear at the wrong time</b></summary>

`TIMEZONE` in `.env` is wrong. This fails **silently** — no error, just events a few hours off.

Classroom returns due times in **UTC**. An 11:59 PM local deadline arrives from the API as a time on the *following* UTC day, so a wrong timezone can shift events onto the wrong date entirely, not just the wrong hour.

</details>

<details>
<summary><b>Jira returns 0 issues but no error</b></summary>

The connection works; your JQL matched nothing. Paste the same query into Jira's own issue search to confirm. Common causes: no due dates set, or issues assigned to someone else.

</details>

<details>
<summary><b>Duplicate events</b></summary>

Shouldn't happen — events are tagged and matched on re-run. If you see duplicates, you likely have events created by an older version. Delete them manually once; the current version won't repeat it.

</details>

<details>
<summary><b>Everything logs "Already passed, skipping"</b></summary>

Expected at the end of a term. The script won't create reminders for deadlines in the past. Run with `-v` to see each skipped item and its parsed date.

</details>

---

## How it works

**Deduplication.** Every event carries two private extended properties: `deadline_sync=1` marks it as ours, and `deadline_sync_id` (e.g. `jira:ENG-42`) identifies the source item. On each run, the calendar is queried for events with the marker, then matched by ID.

> The marker exists because Calendar's `privateExtendedProperty` filter is **exact-match only** — there's no wildcard, so a fixed known value is needed to query "everything this tool created."

**Two tokens, one client.** OAuth tokens are bound to a single Google account. Classroom and Calendar therefore get separate token files, from separate logins, using the same OAuth client.

**Date handling.** Classroom gives `dueDate` plus an optional `dueTime` in UTC → converted to `TIMEZONE`. Jira gives a date with no time → assigned `DATE_ONLY_DUE_TIME` local. Everything past `now` and inside `LOOKAHEAD_DAYS` gets an event.

---

## Project structure

```
deadline-sync/
├── src/
│   ├── deadline_sync.py     # the tool
│   └── test_logic.py        # offline tests
├── .env.example             # config template
├── .env                     # your config (gitignored)
├── credentials.json         # OAuth client (gitignored)
├── classroom_token.json     # auto-generated (gitignored)
├── calendar_token.json      # auto-generated (gitignored)
├── requirements.txt
└── README.md
```

> [!CAUTION]
> **Never commit** `.env`, `credentials.json`, or either `*_token.json`. They contain live credentials to your Google and Jira accounts. Add them to `.gitignore` before your first commit.

```gitignore
.env
credentials.json
*_token.json
sync.log
```

---

<div align="center">
<sub>MIT · Built to stop forgetting deadlines.</sub>
</div>