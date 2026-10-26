# Málaga tickets

Checks [entradas.malagacf.com](https://entradas.malagacf.com/?idioma=EN&act=1) every ~10 minutes
(GitHub Actions) and emails when a new match goes on sale. The weekend we're after is
**Málaga – Real Betis, matchday 25, 19–22 Feb 2027**: that match gets an alarm mail, plus mails when
its stands sell out or it disappears. The checks stop after that weekend.

What gets mailed:

| Mail | To | When |
|---|---|---|
| ✅ Welcome | `MAIL_TO` | first run (and `test` mode) |
| 🎟️ New match online | `MAIL_TO` | any new event on the site |
| 🚨 Betis | `MAIL_TO` | Betis appears, changes, sells out, or disappears |
| ⏳ Waiting room | `MAIL_TO` | the site queues visitors (big sale), once a day |
| 🟢 Weekly status | `STATUS_TO` | Mondays from 09:00. **No Monday mail = something's broken** |
| ⚠️ Failing / ✅ recovered | `STATUS_TO` | after 6 failed checks in a row (~1 hour), and when it works again |

Every mail has the season so far (when each home match went online, how many days before kick-off,
when stands sold out) and a prediction for when Betis tickets appear, from the median lead time.

`data/state.json` holds what's been seen; `data/history.jsonl` logs every change (new, removed,
kick-off moved, stand sold out, price change), for working out the pattern afterwards.

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

Current setup: Resend only, `MAIL_TO` = `STATUS_TO` = Bart, and an Outlook.com inbox rule forwards
mails from `onboarding@resend.dev` whose subject includes *staat aan*, *Nieuw online*, *Betis*,
*Wachtrij* or *gestopt* to the other two. Status and failure mails aren't forwarded.

Actions → Check → Run workflow: `test` sends the welcome mail to everyone, to check delivery.

Locally: copy `.env.example` to `.env`, then `uv run python -m malaga.main --dry-run`; tests: `uv run pytest`.
