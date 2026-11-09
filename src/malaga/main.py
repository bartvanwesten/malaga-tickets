"""One check: read the ticket site, mail what's new, keep state. The workflow runs this every
5 minutes; when checks stop arriving that often, the next one mails STATUS_TO about the gap.

  python -m malaga.main             check and mail
  python -m malaga.main --dry-run   check and print the mail instead; state untouched
  python -m malaga.main --test      send the welcome mail with what's online now; state untouched
  python -m malaga.main --status    send the weekly status mail now
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta

import httpx

from . import mail as m
from .config import (COMMIT_FLAG, END, FAIL_ALERT_AFTER, GAP_ALERT, HISTORY_FILE, STATE_FILE, STATUS_HOUR,
                     STATUS_WEEKDAY, append_jsonl, now, read_json, write_json)
from .scrape import QueueActive, ScrapeError, scrape
from .track import fmt_dt, pattern, season_rows, update, upcoming_fixtures

log = logging.getLogger("malaga")
noteworthy = False  # a mail went out or history grew: the workflow should commit data/ right away


def status_due(state: dict, at: datetime) -> bool:
    """Once a week from Monday 09:00; a later run catches up if that one was missed."""
    monday = at.date() - timedelta(days=(at.weekday() - STATUS_WEEKDAY) % 7)
    if at.date() == monday and at.hour < STATUS_HOUR:
        monday -= timedelta(days=7)
    sent = state.get("status_sent")
    return not sent or sent < monday.isoformat()


def history_since(days: int) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    cutoff = (now() - timedelta(days=days)).isoformat()
    with open(HISTORY_FILE, encoding="utf-8") as f:
        return [row for row in map(json.loads, f) if row["at"] >= cutoff]


def season_parts(state: dict, at: datetime):
    return season_rows(state), pattern(state), upcoming_fixtures(state, at.date())


def deliver(mail: m.Mail, to: list[str], dry_run: bool) -> None:
    global noteworthy
    if dry_run:
        print(f"--- would send to {', '.join(to) or '(nobody)'}\nSubject: {mail.subject}\n\n{mail.text}\n")
    else:
        m.send(mail, to)
        noteworthy = True


def record(rows: list[dict], dry_run: bool) -> None:
    global noteworthy
    if rows and not dry_run:
        append_jsonl(HISTORY_FILE, rows)
        noteworthy = True


def ping(suffix: str = "") -> None:
    """Heartbeat for an outside monitor (healthchecks.io), which mails when the pings stop: the one
    failure this script can't report itself is the checks not running at all."""
    if url := os.environ.get("HEALTHCHECK_URL"):
        try:
            httpx.get(url.rstrip("/") + suffix, timeout=10)
        except httpx.HTTPError as e:
            log.warning("Heartbeat ping failed: %s", e)


def fresh_coverage(at: datetime) -> dict:
    return {"since": at.isoformat(timespec="minutes"), "checks": 0, "longest_gap": 0}


def note_check(state: dict, at: datetime, dry_run: bool) -> str | None:
    """Count this check and catch gaps (checks that should have run but didn't). Returns the
    previous check's time."""
    prev = state.get("last_check")
    cov = state.setdefault("coverage", fresh_coverage(at))
    if prev:
        gap = at - datetime.fromisoformat(prev)
        minutes = round(gap.total_seconds() / 60)
        cov["longest_gap"] = max(cov["longest_gap"], minutes)
        if gap > GAP_ALERT:
            log.warning("No check for %d minutes (since %s)", minutes, prev)
            deliver(m.gap_mail(datetime.fromisoformat(prev), at), m.recipients("STATUS_TO"), dry_run)
            record([{"at": at.isoformat(timespec="minutes"), "kind": "gap", "since": prev,
                     "minutes": minutes}], dry_run)
    cov["checks"] += 1
    state["last_check"] = at.isoformat(timespec="minutes")
    return prev


def on_failure(state: dict, at: datetime, error: Exception, dry_run: bool) -> None:
    state["failures"] = state.get("failures", 0) + 1
    state["last_error"] = f"{fmt_dt(at)}: {error}"
    log.warning("Check failed (%d in a row): %s", state["failures"], error)
    record([{"at": at.isoformat(timespec="minutes"), "kind": "error", "error": str(error)}], dry_run)
    if state["failures"] == FAIL_ALERT_AFTER:
        deliver(m.failure_mail(FAIL_ALERT_AFTER, str(error)), m.recipients("STATUS_TO"), dry_run)


def on_queue(state: dict, at: datetime, dry_run: bool) -> None:
    """The waiting room is holding visitors: a big sale is going on. Mail once a day."""
    log.warning("Waiting room is active")
    record([{"at": at.isoformat(timespec="minutes"), "kind": "queue"}], dry_run)
    if state.get("queue_alerted") != at.date().isoformat():
        deliver(m.queue_mail(), m.recipients("MAIL_TO"), dry_run)
        state["queue_alerted"] = at.date().isoformat()


def finish(state: dict, at: datetime, dry_run: bool) -> None:
    deliver(m.finished_mail(*season_parts(state, at)), m.recipients("MAIL_TO"), dry_run)
    state["finished"] = at.isoformat(timespec="minutes")


def check(state: dict, at: datetime, dry_run: bool, test: bool, since: str | None = None) -> None:
    first = not state.get("events")
    events = scrape()
    if state.get("failures", 0) >= FAIL_ALERT_AFTER:
        deliver(m.recovered_mail(state["failures"]), m.recipients("STATUS_TO"), dry_run)
    state["failures"] = 0

    if test:
        scratch = json.loads(json.dumps(state))
        update(scratch, events, at, baseline=True)
        deliver(m.welcome_mail(events, *season_parts(scratch, at)), m.recipients("MAIL_TO"), dry_run)
        return

    changes = update(state, events, at, baseline=first, since=since)
    log.info("%d event(s) listed; %d new, %d changed, %d removed",
             len(events), len(changes.new), len(changes.changed), len(changes.removed))
    target_news = changes.target_news()
    if first:
        deliver(m.welcome_mail(events, *season_parts(state, at)), m.recipients("MAIL_TO"), dry_run)
    elif changes.new or target_news:
        deliver(m.new_events_mail(changes.new, target_news, *season_parts(state, at)),
                m.recipients("MAIL_TO"), dry_run)
    record(changes.history, dry_run)


def send_status(state: dict, at: datetime, dry_run: bool) -> None:
    week = history_since(7)
    changes = [r for r in week if r["kind"] not in ("error", "queue", "gap")]
    last = fmt_dt(datetime.fromisoformat(changes[-1]["at"])) if changes else None
    failures = sum(r["kind"] == "error" for r in week)
    cov = state.get("coverage") or fresh_coverage(at)
    deliver(m.status_mail(*season_parts(state, at), failures, last, cov), m.recipients("STATUS_TO"), dry_run)
    state["status_sent"] = at.date().isoformat()
    state["coverage"] = fresh_coverage(at)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print mails instead of sending; don't save state")
    ap.add_argument("--test", action="store_true", help="send the welcome mail for what's online now")
    ap.add_argument("--status", action="store_true", help="send the weekly status mail now")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request, waiting room tokens
    sys.stdout.reconfigure(encoding="utf-8")

    at = now()
    live = not (args.dry_run or args.test or args.status)  # the real check: counts, pings, saves state
    state = read_json(STATE_FILE, {})
    state.setdefault("started", at.isoformat(timespec="minutes"))
    state.setdefault("status_sent", at.date().isoformat())  # the welcome mail covers the first week
    try:
        if args.status:  # runs beside the check loop, on committed state: mail only, change nothing
            send_status(state, at, args.dry_run)
        elif at.date() >= END:
            if not state.get("finished"):
                finish(state, at, args.dry_run)
        else:
            since = note_check(state, at, args.dry_run) if live else state.get("last_check")
            try:
                check(state, at, args.dry_run, args.test, since)
                live and ping()
            except QueueActive:
                on_queue(state, at, args.dry_run)
                live and ping()  # the site answers; the queue has its own mail
            except ScrapeError as e:
                on_failure(state, at, e, args.dry_run)
                live and ping("/fail")
            except m.SendError:
                raise
            except Exception as e:  # a bug must not fail silently every 5 minutes: same alert path
                log.exception("Check crashed")
                on_failure(state, at, ScrapeError(f"{type(e).__name__}: {e}"), args.dry_run)
                live and ping("/fail")
            if not args.test and status_due(state, at):
                send_status(state, at, args.dry_run)
    except m.SendError as e:
        log.error("Mail not sent, state not saved (next run retries): %s", e)
        live and ping("/fail")
        return 1
    if live:
        write_json(STATE_FILE, state)
        if noteworthy:
            COMMIT_FLAG.touch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
