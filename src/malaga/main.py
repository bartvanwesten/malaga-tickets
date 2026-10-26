"""One check: read the ticket site, mail what's new, keep state. Run every ~10 minutes.

  python -m malaga.main             check and mail
  python -m malaga.main --dry-run   check and print the mail instead; state untouched
  python -m malaga.main --test      send the welcome mail with what's online now; state untouched
  python -m malaga.main --status    send the weekly status mail now
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

from . import mail as m
from .config import (END, FAIL_ALERT_AFTER, HISTORY_FILE, STATE_FILE, STATUS_HOUR, STATUS_WEEKDAY,
                     append_jsonl, now, read_json, write_json)
from .scrape import QueueActive, ScrapeError, scrape
from .track import fmt_dt, pattern, season_rows, update, upcoming_fixtures

log = logging.getLogger("malaga")


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
    if dry_run:
        print(f"--- would send to {', '.join(to) or '(nobody)'}\nSubject: {mail.subject}\n\n{mail.text}\n")
    else:
        m.send(mail, to)


def on_failure(state: dict, at: datetime, error: Exception, dry_run: bool) -> None:
    state["failures"] = state.get("failures", 0) + 1
    state["last_error"] = f"{fmt_dt(at)}: {error}"
    log.warning("Check failed (%d in a row): %s", state["failures"], error)
    append_jsonl(HISTORY_FILE, [] if dry_run else [{"at": at.isoformat(timespec="minutes"), "kind": "error",
                                                     "error": str(error)}])
    if state["failures"] == FAIL_ALERT_AFTER:
        deliver(m.failure_mail(FAIL_ALERT_AFTER, str(error)), m.recipients("STATUS_TO"), dry_run)


def on_queue(state: dict, at: datetime, dry_run: bool) -> None:
    """The waiting room is holding visitors: a big sale is going on. Mail once a day."""
    log.warning("Waiting room is active")
    append_jsonl(HISTORY_FILE, [] if dry_run else [{"at": at.isoformat(timespec="minutes"), "kind": "queue"}])
    if state.get("queue_alerted") != at.date().isoformat():
        deliver(m.queue_mail(), m.recipients("MAIL_TO"), dry_run)
        state["queue_alerted"] = at.date().isoformat()


def finish(state: dict, at: datetime, dry_run: bool) -> None:
    deliver(m.finished_mail(*season_parts(state, at)), m.recipients("MAIL_TO"), dry_run)
    state["finished"] = at.isoformat(timespec="minutes")


def check(state: dict, at: datetime, dry_run: bool, test: bool) -> None:
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

    changes = update(state, events, at, baseline=first)
    log.info("%d event(s) listed; %d new, %d changed, %d removed",
             len(events), len(changes.new), len(changes.changed), len(changes.removed))
    target_news = changes.target_news()
    if first:
        deliver(m.welcome_mail(events, *season_parts(state, at)), m.recipients("MAIL_TO"), dry_run)
    elif changes.new or target_news:
        deliver(m.new_events_mail(changes.new, target_news, *season_parts(state, at)),
                m.recipients("MAIL_TO"), dry_run)
    if not dry_run:
        append_jsonl(HISTORY_FILE, changes.history)


def send_status(state: dict, at: datetime, dry_run: bool) -> None:
    week = history_since(7)
    changes = [r for r in week if r["kind"] not in ("error", "queue")]
    last = fmt_dt(datetime.fromisoformat(changes[-1]["at"])) if changes else None
    failures = sum(r["kind"] == "error" for r in week)
    deliver(m.status_mail(*season_parts(state, at), failures, last), m.recipients("STATUS_TO"), dry_run)
    state["status_sent"] = at.date().isoformat()


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
    state = read_json(STATE_FILE, {})
    state.setdefault("started", at.isoformat(timespec="minutes"))
    state.setdefault("status_sent", at.date().isoformat())  # the welcome mail covers the first week
    try:
        if at.date() >= END:
            if not state.get("finished"):
                finish(state, at, args.dry_run)
        else:
            try:
                check(state, at, args.dry_run, args.test)
            except QueueActive:
                on_queue(state, at, args.dry_run)
            except ScrapeError as e:
                on_failure(state, at, e, args.dry_run)
            if args.status or (not args.test and status_due(state, at)):
                send_status(state, at, args.dry_run)
    except m.SendError as e:
        log.error("Mail not sent, state not saved (next run retries): %s", e)
        return 1
    if not (args.dry_run or args.test):
        write_json(STATE_FILE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
