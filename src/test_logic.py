"""Exercises deadline_sync's pure logic without needing network or Google libs.

Stubs the API client modules so the import at the top of deadline_sync.py
succeeds; only the date-math and diff functions are actually tested.
"""
import datetime as dt
import sys
import types

for name in [
    "requests", "dotenv",
    "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2",
    "google.oauth2.credentials", "google_auth_oauthlib",
    "google_auth_oauthlib.flow", "googleapiclient",
    "googleapiclient.discovery", "googleapiclient.errors",
]:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)

sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["google.auth.transport.requests"].Request = object
sys.modules["google.oauth2.credentials"].Credentials = object
sys.modules["google_auth_oauthlib.flow"].InstalledAppFlow = object
sys.modules["googleapiclient.discovery"].build = lambda *a, **k: None
sys.modules["googleapiclient.errors"].HttpError = Exception

import os
os.environ.setdefault("TIMEZONE", "America/New_York")

import deadline_sync as ds

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n        got={got!r}\n       want={want!r}")
    if not ok:
        fails.append(label)


# 1. Jira date-only -> local 23:59
check(
    "jira date-only due time",
    ds._date_only_due(dt.date(2026, 9, 14)).isoformat(),
    "2026-09-14T23:59:00-04:00",
)

# 2. Classroom dueTime is UTC and must convert to local
d = ds.Deadline(
    uid="classroom:1",
    title="Essay",
    due=dt.datetime(2026, 9, 14, 3, 59, tzinfo=dt.timezone.utc).astimezone(ds.LOCAL_TZ),
    source="classroom",
)
check("classroom UTC->local conversion", d.due.isoformat(), "2026-09-13T23:59:00-04:00")

# 3. Event body: reminder is 60 min before START
body = ds.build_event_body(d)
check("reminder minutes", body["reminders"]["overrides"][0]["minutes"], 60)
check("reminder not default", body["reminders"]["useDefault"], False)
check("event tagged with uid", body["extendedProperties"]["private"]["deadline_sync_id"], "classroom:1")
check("start == due", body["start"]["dateTime"], d.due.isoformat())

# 4. needs_update: identical event -> False
existing = {"summary": body["summary"], "start": {"dateTime": body["start"]["dateTime"]}}
check("unchanged event -> no update", ds.needs_update(existing, body), False)

# 5. needs_update: due date moved -> True
moved = dict(existing)
moved["start"] = {"dateTime": "2026-09-20T23:59:00-04:00"}
check("moved due date -> update", ds.needs_update(moved, body), True)

# 6. needs_update: all-day event (no dateTime) -> True, not a crash
check("all-day existing -> update", ds.needs_update({"summary": body["summary"], "start": {"date": "2026-09-14"}}, body), True)

# 7. Jira issue mapping
issues = [{"key": "ENG-42", "fields": {"summary": "Fix login", "duedate": "2026-09-14", "status": {"name": "In Progress"}}},
          {"key": "ENG-43", "fields": {"summary": "No due date", "duedate": None}}]
out = ds._jira_issues_to_deadlines(issues)
check("jira: skips issues with no duedate", len(out), 1)
check("jira uid", out[0].uid, "jira:ENG-42")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)
