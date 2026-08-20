#!/usr/bin/env python3
"""
deadline_sync.py

Pulls due dates from Google Classroom and Jira, and creates/updates events on a
Google Calendar with a popup reminder 1 hour before the deadline.

Safe to run repeatedly (idempotent): each event is tagged with a private
extended property so re-runs update existing events instead of duplicating them.

Usage:
    python deadline_sync.py                 # sync
    python deadline_sync.py --dry-run       # show what would change, touch nothing
    python deadline_sync.py --verbose
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from dataclasses import dataclass
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

load_dotenv()

# Google sometimes returns a *deduplicated* scope set that doesn't literally
# match what we asked for (overlapping Classroom scopes collapse into each
# other). oauthlib treats any mismatch as fatal, which kills the login even
# though the granted scopes are sufficient. This relaxes that check.
# The real safety net is the 403 handling below: if a scope we actually need
# is genuinely missing, the API call fails loudly and tells you so.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

CLASSROOM_SCOPES = [
    # NOTE: do NOT add classroom.coursework.me.readonly here. Google silently
    # drops it from the granted set (it overlaps with the two below), and
    # oauthlib then aborts the whole flow with "Scope has changed from ...".
    # courses.readonly is already sufficient to call courses.courseWork.list().
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

TAG_KEY = "deadline_sync_id"          # per-item unique id, e.g. "jira:ENG-42"
MARKER_KEY = "deadline_sync"          # fixed value on every event we own
MARKER_VALUE = "1"
# NOTE: Calendar's privateExtendedProperty filter is exact-match only (no
# wildcards), so we need MARKER_KEY to query "everything this tool created"
# and TAG_KEY to tell those events apart.

# Two Google accounts, two tokens: Classroom is read as your school account,
# Calendar is written to your personal account. Same OAuth *client* (same
# credentials.json) can authorize both — you just log in as a different
# person each time the browser opens.
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
CLASSROOM_TOKEN_FILE = os.getenv("CLASSROOM_TOKEN_FILE", "classroom_token.json")
CALENDAR_TOKEN_FILE = os.getenv("CALENDAR_TOKEN_FILE", "calendar_token.json")
CALENDAR_ID = os.getenv("CALENDAR_ID", "primary")
LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
REMINDER_MINUTES = int(os.getenv("REMINDER_MINUTES", "60"))
EVENT_LENGTH_MINUTES = int(os.getenv("EVENT_LENGTH_MINUTES", "30"))
# Jira due dates are date-only. This is the local time we assume they're due at.
DATE_ONLY_DUE_TIME = os.getenv("DATE_ONLY_DUE_TIME", "23:59")
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", "120"))
DELETE_COMPLETED = os.getenv("DELETE_COMPLETED", "true").lower() == "true"

JIRA_BASE_URL = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_JQL = os.getenv(
    "JIRA_JQL",
    "assignee = currentUser() AND duedate IS NOT EMPTY AND statusCategory != Done",
)

log = logging.getLogger("deadline_sync")


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class Deadline:
    uid: str            # stable unique id, e.g. "classroom:12345" / "jira:ENG-42"
    title: str
    due: dt.datetime    # timezone-aware
    source: str         # "classroom" | "jira"
    url: str | None = None
    description: str = ""

    @property
    def summary(self) -> str:
        icon = "📚" if self.source == "classroom" else "🎫"
        return f"{icon} {self.title}"


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


def _date_only_due(d: dt.date) -> dt.datetime:
    """Turn a date-only deadline into a local datetime at the configured time."""
    hh, mm = _parse_hhmm(DATE_ONLY_DUE_TIME)
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=LOCAL_TZ)


# --------------------------------------------------------------------------
# Google auth
# --------------------------------------------------------------------------


def google_credentials(token_file: str, scopes: list[str], label: str) -> Credentials:
    """Get (or refresh, or newly obtain) credentials for one Google account.

    `label` is just for the log line / prompt, e.g. "Classroom (school account)"
    or "Calendar (personal account)" — helps you remember which login to use
    when the browser window pops up.
    """
    creds: Credentials | None = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, scopes)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.debug("Refreshing expired token: %s", token_file)
        creds.refresh(Request())
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            sys.exit(
                f"Missing {CREDENTIALS_FILE}. Download an OAuth client "
                "(Desktop app) from Google Cloud Console and save it there."
            )
        log.info(
            "No valid token for %s — opening browser. "
            "Log in with the correct account for this one.",
            label,
        )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
        creds = flow.run_local_server(port=0)

    with open(token_file, "w") as fh:
        fh.write(creds.to_json())
    return creds


# --------------------------------------------------------------------------
# Google Classroom
# --------------------------------------------------------------------------


def fetch_classroom_deadlines(creds: Credentials) -> list[Deadline]:
    service = build("classroom", "v1", credentials=creds, cache_discovery=False)
    deadlines: list[Deadline] = []

    courses: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.courses()
            .list(courseStates=["ACTIVE"], pageSize=100, pageToken=page_token)
            .execute()
        )
        courses.extend(resp.get("courses", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("Classroom: %d active course(s)", len(courses))

    for course in courses:
        course_id = course["id"]
        course_name = course.get("name", "Course")

        # Which assignments have I already handed in?
        submitted: set[str] = set()
        try:
            sub_token = None
            while True:
                subs = (
                    service.courses()
                    .courseWork()
                    .studentSubmissions()
                    .list(
                        courseId=course_id,
                        courseWorkId="-",
                        userId="me",
                        pageToken=sub_token,
                    )
                    .execute()
                )
                for s in subs.get("studentSubmissions", []):
                    if s.get("state") in ("TURNED_IN", "RETURNED"):
                        submitted.add(s["courseWorkId"])
                sub_token = subs.get("nextPageToken")
                if not sub_token:
                    break
        except HttpError as e:
            log.warning("Couldn't read submissions for %s: %s", course_name, e)

        work_token = None
        while True:
            try:
                resp = (
                    service.courses()
                    .courseWork()
                    .list(courseId=course_id, pageSize=100, pageToken=work_token)
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 403:
                    sys.exit(
                        f"\n403 from Classroom while listing coursework for "
                        f"'{course_name}'.\n"
                        "This usually means a required scope wasn't granted. Fix:\n"
                        "  1. Cloud Console -> APIs & Services -> OAuth consent "
                        "screen -> Data access\n"
                        "  2. Make sure the Classroom scopes are listed there\n"
                        f"  3. Delete {CLASSROOM_TOKEN_FILE} and re-run\n"
                        f"\nRaw error: {e}"
                    )
                log.warning("Skipping course %s: %s", course_name, e)
                break

            for work in resp.get("courseWork", []):
                due_date = work.get("dueDate")
                if not due_date:
                    continue  # no deadline, nothing to put on a calendar
                if work["id"] in submitted:
                    log.debug("Already submitted: %s", work.get("title"))
                    continue

                due_time = work.get("dueTime") or {}
                if due_time:
                    # Classroom returns dueTime in UTC.
                    due = dt.datetime(
                        due_date["year"],
                        due_date["month"],
                        due_date["day"],
                        due_time.get("hours", 0),
                        due_time.get("minutes", 0),
                        tzinfo=dt.timezone.utc,
                    ).astimezone(LOCAL_TZ)
                else:
                    due = _date_only_due(
                        dt.date(due_date["year"], due_date["month"], due_date["day"])
                    )

                deadlines.append(
                    Deadline(
                        uid=f"classroom:{work['id']}",
                        title=f"{work.get('title', 'Assignment')} ({course_name})",
                        due=due,
                        source="classroom",
                        url=work.get("alternateLink"),
                        description=(work.get("description") or "")[:800],
                    )
                )

            work_token = resp.get("nextPageToken")
            if not work_token:
                break

    return deadlines


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------


def fetch_jira_deadlines() -> list[Deadline]:
    if not (JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN):
        log.info("Jira not configured — skipping.")
        return []

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json"}
    fields = "summary,duedate,status,priority"
    deadlines: list[Deadline] = []

    # Newer Jira Cloud uses /search/jql with token pagination; older instances
    # (and Jira Server/DC) use /search with startAt. Try the new one, fall back.
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    next_token = None
    use_legacy = False

    while True:
        params = {"jql": JIRA_JQL, "fields": fields, "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        r = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
        if r.status_code in (404, 410):
            use_legacy = True
            break
        r.raise_for_status()
        data = r.json()
        deadlines.extend(_jira_issues_to_deadlines(data.get("issues", [])))
        next_token = data.get("nextPageToken")
        if not next_token or data.get("isLast"):
            break

    if use_legacy:
        log.debug("Falling back to legacy /rest/api/3/search endpoint")
        start_at = 0
        while True:
            params = {
                "jql": JIRA_JQL,
                "fields": fields,
                "maxResults": 100,
                "startAt": start_at,
            }
            r = requests.get(
                f"{JIRA_BASE_URL}/rest/api/3/search",
                headers=headers,
                auth=auth,
                params=params,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            issues = data.get("issues", [])
            deadlines.extend(_jira_issues_to_deadlines(issues))
            start_at += len(issues)
            if not issues or start_at >= data.get("total", 0):
                break

    log.info("Jira: %d issue(s) with due dates", len(deadlines))
    return deadlines


def _jira_issues_to_deadlines(issues: Iterable[dict]) -> list[Deadline]:
    out: list[Deadline] = []
    for issue in issues:
        f = issue.get("fields", {})
        raw_due = f.get("duedate")
        if not raw_due:
            continue
        # Jira `duedate` is date-only: "2026-09-14"
        due = _date_only_due(dt.date.fromisoformat(raw_due))
        key = issue["key"]
        status = (f.get("status") or {}).get("name", "")
        out.append(
            Deadline(
                uid=f"jira:{key}",
                title=f"{key}: {f.get('summary', 'Issue')}",
                due=due,
                source="jira",
                url=f"{JIRA_BASE_URL}/browse/{key}",
                description=f"Status: {status}",
            )
        )
    return out


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def existing_events(cal) -> dict[str, dict]:
    """Map uid -> event, for events this script previously created."""
    found: dict[str, dict] = {}
    page_token = None
    while True:
        resp = (
            cal.events()
            .list(
                calendarId=CALENDAR_ID,
                privateExtendedProperty=f"{MARKER_KEY}={MARKER_VALUE}",
                showDeleted=False,
                singleEvents=True,
                maxResults=250,
                pageToken=page_token,
            )
            .execute()
        )
        for ev in resp.get("items", []):
            uid = (ev.get("extendedProperties", {}).get("private", {})).get(TAG_KEY)
            if uid:
                found[uid] = ev
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return found


def build_event_body(d: Deadline) -> dict:
    start = d.due
    end = d.due + dt.timedelta(minutes=EVENT_LENGTH_MINUTES)
    desc = d.description
    if d.url:
        desc = f"{desc}\n\n{d.url}".strip()
    return {
        "summary": d.summary,
        "description": desc,
        "start": {"dateTime": start.isoformat(), "timeZone": str(LOCAL_TZ)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(LOCAL_TZ)},
        "source": {"title": d.source, "url": d.url} if d.url else None,
        "extendedProperties": {
            "private": {TAG_KEY: d.uid, MARKER_KEY: MARKER_VALUE}
        },
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": REMINDER_MINUTES}],
        },
    }


def needs_update(existing: dict, body: dict) -> bool:
    if existing.get("summary") != body["summary"]:
        return True
    old_start = (existing.get("start") or {}).get("dateTime")
    if not old_start:
        return True
    new_start = dt.datetime.fromisoformat(body["start"]["dateTime"])
    return dt.datetime.fromisoformat(old_start) != new_start


def sync(deadlines: list[Deadline], creds: Credentials, dry_run: bool) -> None:
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    existing = existing_events(cal)
    log.info("Calendar: %d event(s) previously created by this tool", len(existing))

    now = dt.datetime.now(LOCAL_TZ)
    horizon = now + dt.timedelta(days=LOOKAHEAD_DAYS)

    created = updated = skipped = deleted = 0
    seen: set[str] = set()

    for d in sorted(deadlines, key=lambda x: x.due):
        if d.due > horizon:
            log.debug("Beyond horizon, skipping: %s", d.title)
            continue
        seen.add(d.uid)
        body = {k: v for k, v in build_event_body(d).items() if v is not None}

        if d.uid in existing:
            if needs_update(existing[d.uid], body):
                log.info("UPDATE  %s  ->  %s", d.title, d.due.strftime("%Y-%m-%d %H:%M"))
                if not dry_run:
                    cal.events().patch(
                        calendarId=CALENDAR_ID,
                        eventId=existing[d.uid]["id"],
                        body=body,
                    ).execute()
                updated += 1
            else:
                skipped += 1
        else:
            log.info("CREATE  %s  ->  %s", d.title, d.due.strftime("%Y-%m-%d %H:%M"))
            if not dry_run:
                cal.events().insert(calendarId=CALENDAR_ID, body=body).execute()
            created += 1

    if DELETE_COMPLETED:
        for uid, ev in existing.items():
            if uid in seen:
                continue
            start = (ev.get("start") or {}).get("dateTime")
            # Only clean up things still in the future; leave history alone.
            if start and dt.datetime.fromisoformat(start) < now:
                continue
            log.info("DELETE  %s (no longer due / completed)", ev.get("summary"))
            if not dry_run:
                cal.events().delete(calendarId=CALENDAR_ID, eventId=ev["id"]).execute()
            deleted += 1

    log.info(
        "Done. created=%d updated=%d unchanged=%d deleted=%d%s",
        created, updated, skipped, deleted, "  (DRY RUN)" if dry_run else "",
    )


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--skip-classroom", action="store_true")
    ap.add_argument("--skip-jira", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    deadlines: list[Deadline] = []
    if not args.skip_classroom:
        classroom_creds = google_credentials(
            CLASSROOM_TOKEN_FILE, CLASSROOM_SCOPES, "Classroom (school account)"
        )
        deadlines += fetch_classroom_deadlines(classroom_creds)
    if not args.skip_jira:
        deadlines += fetch_jira_deadlines()

    log.info("Collected %d deadline(s) total", len(deadlines))

    calendar_creds = google_credentials(
        CALENDAR_TOKEN_FILE, CALENDAR_SCOPES, "Calendar (personal account)"
    )
    sync(deadlines, calendar_creds, dry_run=args.dry_run)


if __name__ == "__main__":
    main()