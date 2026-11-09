# Málaga tickets

Checks [entradas.malagacf.com](https://entradas.malagacf.com/?idioma=EN&act=1) every 5 minutes
(GitHub Actions) and emails when a new match goes on sale. The weekend we're after is
**Málaga – Real Betis, matchday 25, 19–22 Feb 2027**: that match gets an alarm mail, plus mails when
its stands sell out or it disappears. The checks stop after that weekend.

What gets mailed:

| Mail | To | When |
|---|---|---|
| Welcome (*staat aan*) | `MAIL_TO` | first run (and `test` mode) |
| New match online (*Nieuw online*) | `MAIL_TO` | any new event on the site |
| Betis (*Betis-kaarten online* / *Update … Betis*) | `MAIL_TO` | Betis appears, changes, sells out, or disappears |
| Waiting room (*Wachtrij*) | `MAIL_TO` | the site queues visitors (big sale), once a day |
| Weekly status | `STATUS_TO` | Mondays from 09:00, with checks done vs. expected and the longest gap. **No Monday mail = something's broken** |
| Gap (*Onderbreking*) | `STATUS_TO` | a check finds the previous one was more than 45 minutes ago |
| Failing / recovered | `STATUS_TO` | after 12 failed checks in a row (~1 hour; a crash counts too), and when it works again |

Every mail has the season so far (when each home match went online, how many days before kick-off,
when stands sold out) and a prediction for when Betis tickets appear, from the median lead time.

`data/state.json` holds what's been seen; `data/history.jsonl` logs every change (new, removed,
kick-off moved, stand sold out, price change, gaps), for working out the pattern afterwards.

## Keeping it running

GitHub runs `schedule` triggers best-effort. In Oct–Nov 2026 a `*/10` cron fired only 4–5 times a
day, every run "succeeded", and Athletic Club went on sale during an 8-hour gap without a mail.
So the checks no longer hang on cron:

- **Relay.** A check run loops for ~5.5 hours, checking every 5 minutes, then hands over. At its
  start it queues its successor via `workflow_dispatch`, which GitHub starts promptly; the
  `check` concurrency group makes that one wait until this one ends. A final step re-queues if
  no successor is waiting. The hourly cron is only a backup that restarts the relay.
- **Gap alert.** Each check stores its time; the next one mails if more than 45 minutes passed.
- **Heartbeat (optional, recommended).** With `HEALTHCHECK_URL` set, each check pings it (and
  `/fail` on failure). [healthchecks.io](https://healthchecks.io) then mails you when the pings
  stop, the one failure the script itself can't report: nothing running at all.
- **Layout safety net.** Event links on the listing (`…-YYYYMMDD-HHMM`) that no card accounts for
  still count as events, named from the link, so a markup change can't hide a match.

State is committed at once when a mail went out or something changed, otherwise hourly and at
hand-over. Manual `test` / `dry-run` / `status` runs work alongside the loop and change nothing.

## How the site works

Koobin ticketing. Each request bounces through a waiting room (`queue.koobin.com`) that sets a
cookie; with a cookie jar the redirect passes instantly. When it really queues, the scraper reports
that instead. The grid lists one `gFitxaGen_<id>` card per event; each match page lists the stand
areas with AVAILABLE / SOLD OUT and the (dynamic) price.

## Setup

Repository secrets:

- `MAIL_TO`: comma-separated recipients
- `STATUS_TO`: who gets status and failure mails
- `SMTP_USER`, `SMTP_PASSWORD` (and optional `SMTP_HOST`, `SMTP_PORT`, default Gmail 587): a Gmail
  address with an [app password](https://myaccount.google.com/apppasswords) (needs 2-step
  verification). Without these the mail goes via `RESEND_API_KEY`, which (without a verified domain)
  only reaches the Resend account owner, so only `STATUS_TO` gets it, with a warning.
- `HEALTHCHECK_URL` (optional): the ping URL of a healthchecks.io check with period 5 minutes and
  grace 30 minutes.

Current setup: Resend only, `MAIL_TO` = `STATUS_TO` = Bart, and an Outlook.com inbox rule forwards
mails from `onboarding@resend.dev` whose subject includes *staat aan*, *Nieuw online*, *Betis*,
*Wachtrij* or *gestopt* to the other two. Status, gap and failure mails aren't forwarded.
`onboarding@resend.dev` should be a safe sender: a rule doesn't forward what lands in Junk.

Actions → Check → Run workflow: `test` sends the welcome mail, to check delivery; `check` starts
the relay if it ever stopped.

Locally: copy `.env.example` to `.env`, then `uv run python -m malaga.main --dry-run`; tests: `uv run pytest`.
