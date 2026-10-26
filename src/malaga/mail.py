"""The alert, status and failure emails (in Dutch), and sending them.

Sending: SMTP when SMTP_USER/SMTP_PASSWORD are set (e.g. Gmail with an app password), which
reaches every address in MAIL_TO. Otherwise Resend's test sender, which only delivers to the
Resend account's own address, so the mail then goes to STATUS_TO alone with a warning on top.
"""

import html
import logging
import os
import smtplib
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr

import httpx

from .config import LISTING_URL, TARGET_FROM, TARGET_TO
from .scrape import Event
from .track import SOLD_OUT, Pattern, Row, display, fmt_date, fmt_dt, fmt_price, is_target, venue_name

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
RESEND_SENDER = "Málaga tickets <onboarding@resend.dev>"
SENDER_NAME = "Málaga tickets"
RETRIES = 3

BLUE = "#17223e"
SKY = "#3598db"
RED = "#c0392b"
GREEN = "#1a9241"
GREY = "#6b7280"


class SendError(RuntimeError):
    pass


def esc(s) -> str:
    return html.escape(str(s), quote=True)


@dataclass
class Mail:
    subject: str
    blocks: list[tuple[str, str]] = field(default_factory=list)  # (html, text)

    def add(self, html_part: str, text_part: str) -> "Mail":
        self.blocks.append((html_part, text_part))
        return self

    @property
    def text(self) -> str:
        return "\n\n".join(t for _, t in self.blocks if t)

    @property
    def html(self) -> str:
        body = "\n".join(h for h, _ in self.blocks)
        return (f'<div style="background:#f3f4f6;padding:24px 12px;font-family:system-ui,-apple-system,Segoe UI,'
                f'Roboto,sans-serif;color:#111827;font-size:15px;line-height:1.5">'
                f'<div style="max-width:620px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden">'
                f'{body}'
                f'<div style="padding:14px 24px;color:{GREY};font-size:12px;border-top:1px solid #e5e7eb">'
                f'Automatische check van <a href="{esc(LISTING_URL)}" style="color:{GREY}">entradas.malagacf.com</a>, '
                f'ongeveer elke 10 minuten, tot het weekend van {TARGET_FROM.day}–{fmt_date(TARGET_TO)}.</div>'
                f'</div></div>')


def header(title: str, subtitle: str = "", color: str = BLUE) -> tuple[str, str]:
    sub = f'<div style="opacity:.85;margin-top:4px">{esc(subtitle)}</div>' if subtitle else ""
    return (f'<div style="background:{color};color:#fff;padding:20px 24px">'
            f'<div style="font-size:20px;font-weight:700">{esc(title)}</div>{sub}</div>',
            f"{title}\n{subtitle}".strip())


def paragraph(text: str) -> tuple[str, str]:
    return f'<div style="padding:12px 24px 0">{esc(text)}</div>', text


def heading(text: str) -> tuple[str, str]:
    return (f'<div style="padding:20px 24px 4px;font-size:13px;font-weight:700;letter-spacing:.05em;'
            f'text-transform:uppercase;color:{GREY}">{esc(text)}</div>', text.upper())


def availability(ev: Event) -> str:
    if ev.areas is None:
        return "beschikbaarheid onbekend"
    if not ev.areas:
        return "geen vakken in de verkoop"
    open_ = [a for a in ev.areas if a.status != SOLD_OUT]
    if not open_:
        return "uitverkocht"
    prices = [a.price for a in open_ if a.price is not None]
    cheapest = f" · vanaf {fmt_price(min(prices))}" if prices else ""
    return f"{len(open_)} van {len(ev.areas)} vakken beschikbaar{cheapest}"


def event_card(ev: Event, note: str = "", lines: list[str] = ()) -> tuple[str, str]:
    target = is_target(ev.title, ev.when)
    border = RED if target else SKY
    extra = "".join(f'<li>{esc(line)}</li>' for line in lines)
    extra = f'<ul style="margin:8px 0 0;padding-left:18px;color:#374151">{extra}</ul>' if extra else ""
    note_html = f'<div style="color:{RED};font-weight:700;margin-bottom:4px">{esc(note)}</div>' if note else ""
    button = (f'<a href="{esc(ev.url)}" style="display:inline-block;margin-top:12px;background:{border};color:#fff;'
              f'text-decoration:none;padding:9px 16px;border-radius:6px;font-weight:600">Kaarten kopen</a>'
              if ev.url else "")
    h = (f'<div style="margin:16px 24px 0;padding:14px 16px;border-left:4px solid {border};background:#f9fafb;'
         f'border-radius:4px">{note_html}<div style="font-size:17px;font-weight:700">{esc(display(ev.title))}</div>'
         f'<div>{esc(fmt_dt(ev.when))} · {esc(venue_name(ev.venue))}</div>'
         f'<div style="color:{GREY}">{esc(availability(ev))}</div>{extra}{button}</div>')
    t = "\n".join(filter(None, [note, display(ev.title), f"{fmt_dt(ev.when)} · {venue_name(ev.venue)}", availability(ev),
                                *[f"- {line}" for line in lines], ev.url]))
    return h, t


def _days(v: float | None, baseline: bool) -> str:
    if v is None:
        return "?"
    return f"≥ {v:.0f}" if baseline else f"{v:.1f}".replace(".", ",")


def season_table(rows: list[Row]) -> tuple[str, str]:
    if not rows:
        return paragraph("Nog geen thuiswedstrijden gezien.")
    th = f'style="text-align:left;padding:6px 8px;border-bottom:1px solid #e5e7eb;color:{GREY};font-weight:600"'
    td = 'style="padding:6px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top"'
    out_h, out_t = [], []
    for r in rows:
        sold = fmt_dt(r.sold_out) if r.sold_out else ("deels" if r.first_area_sold_out else "–")
        online = "al bij de start" if r.baseline else fmt_dt(r.first_seen)
        out_h.append(f'<tr><td {td}>{esc(display(r.title))}</td><td {td}>{esc(fmt_dt(r.when))}</td>'
                     f'<td {td}>{esc(online)}</td><td {td}>{esc(_days(r.lead_days, r.baseline))}</td>'
                     f'<td {td}>{esc(sold)}</td></tr>')
        out_t.append(f"- {display(r.title)}: aftrap {fmt_dt(r.when)}, online: {online}, "
                     f"{_days(r.lead_days, r.baseline)} dagen vooraf, uitverkocht: {sold}")
    h = (f'<div style="padding:4px 16px 0;overflow-x:auto"><table style="border-collapse:collapse;width:100%;'
         f'font-size:13px"><tr><th {th}>Wedstrijd</th><th {th}>Aftrap</th><th {th}>Online sinds</th>'
         f'<th {th}>Dagen vooraf</th><th {th}>Uitverkocht</th></tr>{"".join(out_h)}</table></div>')
    return h, "\n".join(out_t)


def prediction(p: Pattern) -> tuple[str, str]:
    if p.target_row:
        return paragraph(f"Málaga – Betis staat online sinds {fmt_dt(p.target_row.first_seen)}.")
    if p.median_lead is None:
        return paragraph(f"Nog geen verkoopstart live gezien, dus nog geen voorspelling. Málaga – Betis: "
                         f"{fmt_date(p.target_date)} (aftrap volgt).")
    when = ", ".join(sorted(set(p.weekdays), key=lambda d: "ma di wo do vr za zo".split().index(d)))
    hours = f"{min(p.hours)}–{max(p.hours) + 1} uur" if p.hours else "?"
    text = (f"{p.observed} verkoopstart(s) gezien: mediaan {p.median_lead:.1f} dagen voor de aftrap, op {when}, "
            f"tussen {hours}. Als dat zo blijft komt Málaga – Betis ({fmt_date(p.target_date)}) rond "
            f"{fmt_date(p.predicted)} online.").replace(".0 dagen", " dagen")
    return paragraph(text)


def expected_list(fixtures: list[tuple]) -> tuple[str, str]:
    if not fixtures:
        return paragraph("Alle verwachte thuiswedstrijden tot en met Betis zijn al gezien.")
    items = [f"Speeldag {n}: Málaga – {name}, weekend van {fmt_date(d)}" for n, d, name, _ in fixtures]
    h = "".join(f"<li>{esc(i)}</li>" for i in items)
    return (f'<ul style="margin:4px 0 0;padding:0 24px 0 42px">{h}</ul>', "\n".join(f"- {i}" for i in items))


def new_events_mail(new: list[Event], target_news: list[tuple[Event, list[str]]], rows: list[Row],
                    p: Pattern, fixtures: list[tuple]) -> Mail:
    targets = [ev for ev in new if is_target(ev.title, ev.when)]
    if targets:
        ev = targets[0]
        mail = Mail(f"🚨 BETIS-KAARTEN ONLINE: {display(ev.title)} ({fmt_dt(ev.when)})")
        mail.add(*header("Kaarten voor Málaga – Betis zijn online!", "Dit is de wedstrijd van ons weekend. Snel zijn.", RED))
    elif new:
        ev = new[0]
        more = f" (+{len(new) - 1})" if len(new) > 1 else ""
        mail = Mail(f"🎟️ Nieuw online: {display(ev.title)} ({fmt_dt(ev.when)}){more}")
        mail.add(*header("Nieuwe wedstrijd in de verkoop", display(ev.title)))
    else:
        ev = target_news[0][0]
        mail = Mail(f"🚨 Update Málaga – Betis: {target_news[0][1][0]}")
        mail.add(*header("Update over Málaga – Betis", display(ev.title), RED))
    for ev in new:
        mail.add(*event_card(ev, "Dit is ‘m!" if is_target(ev.title, ev.when) else ""))
    for ev, lines in target_news:
        mail.add(*event_card(ev, "Gewijzigd", lines))
    return season_sections(mail, rows, p, fixtures)


def season_sections(mail: Mail, rows: list[Row], p: Pattern, fixtures: list[tuple]) -> Mail:
    mail.add(*heading("Seizoen tot nu toe"))
    mail.add(*season_table(rows))
    mail.add(*heading("Patroon"))
    mail.add(*prediction(p))
    mail.add(*heading("Nog te verwachten"))
    mail.add(*expected_list(fixtures))
    return mail


def welcome_mail(events: list[Event], rows: list[Row], p: Pattern, fixtures: list[tuple]) -> Mail:
    mail = Mail("✅ Málaga-kaartencheck staat aan")
    mail.add(*header("De Málaga-kaartencheck staat aan",
                     f"Je krijgt een mail zodra er een nieuwe wedstrijd online komt, tot {fmt_date(TARGET_TO)}."))
    mail.add(*paragraph("Elke ~10 minuten wordt entradas.malagacf.com bekeken. Nieuwe wedstrijd → mail. "
                        "Málaga – Betis (speeldag 25, weekend 19–22 feb) krijgt een rode alarm-mail, en ook "
                        "updates als vakken uitverkopen. Dit staat er nu:"))
    for ev in events:
        mail.add(*event_card(ev))
    return season_sections(mail, rows, p, fixtures)


def status_mail(rows: list[Row], p: Pattern, fixtures: list[tuple], failures_this_week: int,
                last_change: str | None) -> Mail:
    mail = Mail("🟢 Málaga-kaartencheck: weekstatus")
    mail.add(*header("Weekstatus: de check draait", "Deze mail komt elke maandag. Geen mail = iets mis."))
    checks = (f"Mislukte checks afgelopen week: {failures_this_week}." if failures_this_week
              else "Geen mislukte checks afgelopen week.")
    mail.add(*paragraph(f"{checks} Laatste wijziging op de site: {last_change or 'geen'}."))
    return season_sections(mail, rows, p, fixtures)


def problem_mail(subject: str, title: str, text: str) -> Mail:
    mail = Mail(subject)
    mail.add(*header(title, color=RED if subject.startswith("⚠️") else GREEN))
    mail.add(*paragraph(text))
    return mail


# --- sending ---------------------------------------------------------------------------------

def recipients(var: str) -> list[str]:
    return [a.strip() for a in os.environ.get(var, "").split(",") if a.strip()]


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send(mail: Mail, to: list[str]) -> None:
    if not to:
        raise SendError("no recipients (MAIL_TO / STATUS_TO not set)")
    if smtp_configured():
        return _send_smtp(mail, to)
    owner = recipients("STATUS_TO")
    skipped = [a for a in to if a not in owner]
    if skipped:
        mail.blocks.insert(0, (f'<div style="background:#fef3c7;padding:10px 24px;font-size:13px">⚠️ Alleen naar '
                               f'jou gestuurd: zonder SMTP-instellingen kan Resend niet mailen naar '
                               f'{esc(", ".join(skipped))}.</div>',
                               f"⚠️ Alleen naar jou gestuurd (geen SMTP): niet naar {', '.join(skipped)}."))
    _send_resend(mail, owner)


def _send_smtp(mail: Mail, to: list[str]) -> None:
    user = os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = mail.subject
    msg["From"] = formataddr((SENDER_NAME, os.environ.get("SMTP_FROM") or user))
    msg["To"] = ", ".join(to)
    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"  # unset secrets arrive as ""
    port = int(os.environ.get("SMTP_PORT") or 587)
    for attempt in range(1, RETRIES + 1):
        try:
            with smtplib.SMTP(host, port, timeout=60) as s:
                s.starttls()
                s.login(user, os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
            log.info("Email sent via SMTP to %d recipient(s): %s", len(to), mail.subject)
            return
        except smtplib.SMTPAuthenticationError as e:
            raise SendError(f"SMTP login refused: {e.smtp_code}") from e  # retrying won't help
        except (smtplib.SMTPException, OSError) as e:
            problem = f"{type(e).__name__}: {e}"
        log.warning("SMTP send failed (%s), attempt %d", problem, attempt)
        if attempt == RETRIES:
            raise SendError(f"SMTP failed: {problem}")
        time.sleep(5 * attempt)


def _send_resend(mail: Mail, to: list[str]) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise SendError("neither SMTP_USER/SMTP_PASSWORD nor RESEND_API_KEY set")
    payload = {"from": RESEND_SENDER, "to": to, "subject": mail.subject, "text": mail.text, "html": mail.html}
    with httpx.Client(timeout=60) as http:
        for attempt in range(1, RETRIES + 1):
            try:
                r = http.post(RESEND_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"})
                if r.status_code == 200:
                    log.info("Email sent via Resend (%s): %s", r.json().get("id"), mail.subject)
                    return
                problem = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code != 429 and r.status_code < 500:
                    raise SendError(f"Resend refused the email: {problem}")
            except httpx.HTTPError as e:
                problem = type(e).__name__
            log.warning("Resend failed (%s), attempt %d", problem, attempt)
            if attempt == RETRIES:
                raise SendError(f"Resend failed: {problem}")
            time.sleep(5 * attempt)
