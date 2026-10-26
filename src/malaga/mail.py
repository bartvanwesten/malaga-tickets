"""The alert, status and failure emails (in Dutch), and sending them.

The HTML is table-based with inline styles only, which is what Outlook and Gmail render reliably.
Subjects keep the words the Outlook forwarding rule matches on: staat aan, Nieuw online, Betis,
Wachtrij, gestopt (and status/failure mails avoid them).

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
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr

import httpx

from .config import HOME_FIXTURES, LISTING_URL, TARGET_FROM, TARGET_TO, now
from .scrape import Event
from .track import (DAYS, MONTHS, SOLD_OUT, Pattern, Row, display, fixture_of, fmt_date, fmt_dt, fmt_price,
                    is_target, venue_name)

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
SENDER_NAME = "Málaga Ticketalert"
RESEND_SENDER = f"{SENDER_NAME} <onboarding@resend.dev>"
RETRIES = 3

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
NAVY = "#0b1f3a"
NAVY_2 = "#1b3557"
SKY = "#2f8fdc"
SKY_LIGHT = "#9fd0f7"
INK = "#0f172a"
TEXT = "#334155"
MUTED = "#64748b"
LINE = "#e2e8f0"
SOFT = "#f6f8fb"
PAGE = "#eceff4"
RED = "#c8102e"
RED_SOFT = "#fdecef"
GREEN = "#15803d"
GREEN_SOFT = "#e7f6ec"
AMBER = "#b45309"
AMBER_SOFT = "#fdf3e1"

DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MONTH_NAMES = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
               "oktober", "november", "december"]


class SendError(RuntimeError):
    pass


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def long_date(d: date | datetime) -> str:
    return f"{DAY_NAMES[d.weekday()]} {d.day} {MONTH_NAMES[d.month - 1]} {d.year}"


def table(inner: str, style: str = "", width: str = "100%") -> str:
    return (f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:separate;mso-table-lspace:0;mso-table-rspace:0;{style}">{inner}</table>')


def row(inner: str, style: str = "") -> str:
    """A full-width row of the card."""
    return f'<tr><td style="{style}">{inner}</td></tr>'


def pill(text: str, color: str, background: str) -> str:
    return (f'<span style="display:inline-block;padding:3px 9px;border-radius:999px;background:{background};'
            f'font-family:{FONT};line-height:1.5;'
            f'color:{color};font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;'
            f'white-space:nowrap">{esc(text)}</span>')


def button(label: str, url: str, color: str = SKY) -> str:
    return table(f'<tr><td style="border-radius:8px;background:{color}">'
                 f'<a href="{esc(url)}" style="display:inline-block;padding:12px 22px;font-family:{FONT};'
                 f'font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px">'
                 f'{esc(label)} &rarr;</a></td></tr>', width="auto")


@dataclass
class Mail:
    subject: str
    preheader: str = ""
    blocks: list[tuple[str, str]] = field(default_factory=list)  # (html row, text)

    def add(self, html_part: str, text_part: str) -> "Mail":
        self.blocks.append((html_part, text_part))
        return self

    @property
    def text(self) -> str:
        footer = (f"Málaga Ticketalert: automatische controle van {LISTING_URL}, elke 10 minuten, "
                  f"tot en met {long_date(TARGET_TO)}.")
        return "\n\n".join([t for _, t in self.blocks if t] + [footer])

    @property
    def html(self) -> str:
        body = "".join(h for h, _ in self.blocks)
        brand = table(
            f'<tr><td style="font-family:{FONT};font-size:12px;font-weight:800;letter-spacing:.16em;color:{NAVY}">'
            f'MÁLAGA CF <span style="color:{SKY}">TICKETALERT</span></td>'
            f'<td align="right" style="font-family:{FONT};font-size:12px;color:{MUTED}">{esc(long_date(now()))}'
            f'</td></tr>')
        footer = (f'<div style="font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};text-align:center">'
                  f'Automatische controle van <a href="{esc(LISTING_URL)}" style="color:{MUTED}">'
                  f'entradas.malagacf.com</a>, elke 10 minuten, tot en met {esc(long_date(TARGET_TO))}.<br>'
                  f'Kaarten koop je altijd zelf op de officiële site van Málaga CF.</div>')
        return (
            f'<!doctype html><html lang="nl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light only"><title>{esc(self.subject)}</title></head>'
            f'<body style="margin:0;padding:0;background:{PAGE}">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAGE}">{esc(self.preheader)}'
            f'{"&nbsp;&zwnj;" * 40}</div>'
            + table(row(table(
                row(brand, "padding:0 6px 14px")
                + row(table(body, f"background:#ffffff;border:1px solid {LINE};border-radius:14px;overflow:hidden"))
                + row(footer, "padding:20px 24px 0"),
                "max-width:600px;margin:0 auto"), "padding:32px 12px"),
                f"background:{PAGE}")
            + '</body></html>')


# --- building blocks (each returns (html row, text)) ------------------------------------------

def hero(eyebrow: str, title: str, subtitle: str = "", alarm: bool = False) -> tuple[str, str]:
    bg, accent, eyebrow_bg, eyebrow_fg, sub = ((RED, "#ff8a9b", "#a50d26", "#ffd5dc", "#ffe3e8") if alarm
                                               else (NAVY, SKY, NAVY_2, SKY_LIGHT, "#c6d3e3"))
    sub_html = (f'<div style="margin-top:10px;font-family:{FONT};font-size:15px;line-height:1.55;color:{sub}">'
                f'{esc(subtitle)}</div>') if subtitle else ""
    h = (f'<tr><td style="height:4px;line-height:4px;font-size:0;background:{accent}">&nbsp;</td></tr>'
         f'<tr><td style="background:{bg};padding:34px 32px 32px">'
         f'<span style="display:inline-block;padding:5px 11px;border-radius:999px;background:{eyebrow_bg};'
         f'font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.14em;color:{eyebrow_fg}">'
         f'{esc(eyebrow.upper())}</span>'
         f'<div style="margin-top:16px;font-family:{FONT};font-size:26px;line-height:1.25;font-weight:800;'
         f'color:#ffffff">{esc(title)}</div>{sub_html}</td></tr>')
    return h, f"{title}\n{subtitle}".strip()


def paragraph(text: str) -> tuple[str, str]:
    return (row(f'<div style="font-family:{FONT};font-size:15px;line-height:1.65;color:{TEXT}">{esc(text)}</div>',
                "padding:26px 32px 0"), text)


def section(title: str) -> tuple[str, str]:
    return (row(f'<div style="font-family:{FONT};font-size:11px;font-weight:800;letter-spacing:.14em;'
                f'text-transform:uppercase;color:{MUTED};padding-bottom:10px;border-bottom:1px solid {LINE}">'
                f'{esc(title)}</div>', "padding:34px 32px 0"), title.upper())


def banner(text: str) -> tuple[str, str]:
    return (row(f'<div style="font-family:{FONT};font-size:13px;line-height:1.5;color:{AMBER}">{esc(text)}</div>',
                f"background:{AMBER_SOFT};padding:12px 32px;border-bottom:1px solid #f3dfb8"), f"⚠️ {text}")


def availability(ev: Event) -> tuple[str, str, float | None]:
    """(summary, cheapest price, share of stand areas still on sale)."""
    if ev.areas is None:
        return "Beschikbaarheid onbekend", "", None
    if not ev.areas:
        return "Nog geen vakken in de verkoop", "", 0.0
    open_ = [a for a in ev.areas if a.status != SOLD_OUT]
    prices = [a.price for a in open_ if a.price is not None]
    cheapest = f"vanaf {fmt_price(min(prices))}" if prices else ""
    if not open_:
        return "Uitverkocht", "", 0.0
    return f"{len(open_)} van {len(ev.areas)} vakken beschikbaar", cheapest, len(open_) / len(ev.areas)


def availability_bar(share: float, color: str) -> str:
    filled = max(1, round(share * 100)) if share else 0
    cells = (f'<td width="{filled}%" style="height:6px;line-height:6px;font-size:0;background:{color};'
             f'border-radius:3px">&nbsp;</td>' if filled else "")
    if filled < 100:
        cells += f'<td style="height:6px;line-height:6px;font-size:0;background:{LINE}">&nbsp;</td>'
    return table(f"<tr>{cells}</tr>", f"border-radius:3px;background:{LINE}")


def date_tile(when: datetime | None, target: bool, compact: bool = False) -> str:
    bg, month_color = (RED_SOFT, RED) if target else (SOFT, SKY)
    if when:
        top, big, bottom = DAYS[when.weekday()].upper(), str(when.day), MONTHS[when.month - 1].upper()
    else:
        top, big, bottom = "", "?", "DATUM"
    size, pad = ("22px", "10px 0") if compact else ("34px", "18px 0")
    weekday = (f'<div style="font-size:11px;font-weight:700;letter-spacing:.1em;color:{MUTED}">{top}</div>'
               if top and not compact else "")
    return (f'<div style="background:{bg};border-radius:10px;padding:{pad};text-align:center;font-family:{FONT}">'
            f'{weekday}<div style="font-size:{size};line-height:1.1;font-weight:800;color:{NAVY}">{big}</div>'
            f'<div style="font-size:11px;font-weight:800;letter-spacing:.12em;color:{month_color}">{bottom}</div>'
            f'</div>')


def match_card(ev: Event, lines: list[str] = (), label: str = "") -> tuple[str, str]:
    target = is_target(ev.title, ev.when)
    color = RED if target else SKY
    fx = fixture_of(ev.title, ev.when)
    meta = " · ".join(filter(None, [f"{ev.when:%H:%M} uur" if ev.when else "Datum nog onbekend",
                                    f"Estadio {venue_name(ev.venue)}" if ev.venue else "",
                                    f"Speeldag {fx[0]}" if fx else ""]))
    summary, cheapest, share = availability(ev)
    tag = pill(label, RED, RED_SOFT) if label else (pill("Ons weekend", RED, RED_SOFT) if target else "")
    tag = f'<div style="margin-bottom:8px">{tag}</div>' if tag else ""
    bar = (f'<div style="margin-top:16px">{availability_bar(share, GREEN if share else RED)}</div>'
           if share is not None else "")
    stock = table(f'<tr><td style="padding-top:8px;font-family:{FONT};font-size:13px;color:{TEXT}">{esc(summary)}</td>'
                  f'<td align="right" style="padding-top:8px;font-family:{FONT};font-size:13px;font-weight:700;'
                  f'color:{INK}">{esc(cheapest)}</td></tr>')
    changes = ""
    if lines:
        items = "".join(f'<div style="padding:3px 0">&bull;&nbsp; {esc(line)}</div>' for line in lines)
        changes = (f'<div style="margin-top:14px;padding:10px 12px;background:{SOFT};border-radius:8px;'
                   f'font-family:{FONT};font-size:13px;line-height:1.5;color:{TEXT}">{items}</div>')
    cta = f'<div style="margin-top:18px">{button("Kaarten kopen", ev.url, color)}</div>' if ev.url else ""
    body = (f'{tag}<div style="font-family:{FONT};font-size:19px;line-height:1.3;font-weight:800;color:{INK}">'
            f'{esc(display(ev.title))}</div>'
            f'<div style="margin-top:4px;font-family:{FONT};font-size:14px;color:{MUTED}">{esc(meta)}</div>'
            f'{bar}{stock}{changes}{cta}')
    card = table(f'<tr><td width="92" valign="top" style="padding:18px 0 18px 18px">{date_tile(ev.when, target)}</td>'
                 f'<td valign="top" style="padding:18px 20px 20px 18px">{body}</td></tr>',
                 f"border:{'2px' if target else '1px'} solid {RED if target else LINE};border-radius:12px")
    text = "\n".join(filter(None, [label, display(ev.title), f"{fmt_dt(ev.when)} · {meta}",
                                   " · ".join(filter(None, [summary, cheapest])),
                                   *[f"- {line}" for line in lines], f"Kaarten: {ev.url}" if ev.url else ""]))
    return row(card, "padding:22px 32px 0"), text


def stats(items: list[tuple[str, str]]) -> tuple[str, str]:
    """A row of key figures: (value, label)."""
    width = f"{100 // len(items)}%"
    cells = "".join(  # background on the cells, so all tiles get the row's height
        f'<td width="{width}" valign="top" style="background:{SOFT};border-radius:10px;padding:16px 14px;'
        f'font-family:{FONT}">'
        f'<div style="font-size:21px;line-height:1.2;font-weight:800;color:{NAVY}">{esc(value)}</div>'
        f'<div style="margin-top:4px;font-size:12px;line-height:1.4;color:{MUTED}">{esc(label)}</div></td>'
        for value, label in items)
    return (row(table(f"<tr>{cells}</tr>", "border-spacing:10px 0"), "padding:18px 22px 0"),
            "\n".join(f"- {label}: {value}" for value, label in items))


def _days(v: float | None, baseline: bool) -> str:
    if v is None:
        return "–"
    return f"≥ {v:.0f} d" if baseline else f"{v:.1f} d".replace(".", ",")


def status_pill(r: Row) -> tuple[str, str]:
    if r.sold_out:
        return pill("Uitverkocht", RED, RED_SOFT), "uitverkocht"
    if not r.listed:
        return pill("Offline", MUTED, SOFT), "offline"
    if r.first_area_sold_out:
        return pill("Deels uitverkocht", AMBER, AMBER_SOFT), "deels uitverkocht"
    return pill("In verkoop", GREEN, GREEN_SOFT), "in de verkoop"


def season_table(rows: list[Row]) -> tuple[str, str]:
    if not rows:
        return paragraph("Nog geen thuiswedstrijden gezien.")
    th = (f'style="padding:12px 8px 8px;font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.08em;'
          f'text-transform:uppercase;color:{MUTED};border-bottom:1px solid {LINE};text-align:left"')
    td = f'style="padding:12px 8px;font-family:{FONT};font-size:13px;color:{TEXT};border-bottom:1px solid {LINE}"'
    out_h, out_t = [], []
    for r in rows:
        online = "Al online bij start" if r.baseline else fmt_dt(r.first_seen)
        badge, status = status_pill(r)
        out_h.append(f'<tr><td {td}><div style="font-weight:700;color:{INK}">{esc(display(r.title))}</div>'
                     f'<div style="color:{MUTED};font-size:12px">{esc(fmt_dt(r.when))}</div></td>'
                     f'<td {td}>{esc(online)}</td><td {td}>{esc(_days(r.lead_days, r.baseline))}</td>'
                     f'<td {td}>{badge}</td></tr>')
        out_t.append(f"- {display(r.title)} ({fmt_dt(r.when)}): online {online.lower()}, "
                     f"{_days(r.lead_days, r.baseline)} vooraf, {status}")
    h = table(f'<tr><th {th}>Wedstrijd</th><th {th}>Online sinds</th><th {th}>Vooraf</th><th {th}>Status</th></tr>'
              + "".join(out_h))
    return row(h, "padding:4px 32px 0"), "\n".join(out_t)


def pattern_section(p: Pattern) -> list[tuple[str, str]]:
    if p.target_row:
        target = ("Online", f"Málaga – Betis, sinds {fmt_date(p.target_row.first_seen.date())}")
    else:
        target = (f"± {fmt_date(p.predicted)}" if p.predicted else "–", "verwachte verkoopstart Málaga – Betis")
    lead = f"{p.median_lead:.1f}".replace(".", ",") + " dagen" if p.median_lead is not None else "–"
    blocks = [stats([(str(p.observed), "verkoopstarts live gezien"), (lead, "mediaan vóór de aftrap"), target])]
    if p.target_row:
        text = f"Málaga – Betis staat online sinds {fmt_dt(p.target_row.first_seen)}."
    elif p.median_lead is None:
        text = ("Nog geen verkoopstart live meegemaakt: de eerste wedstrijd die erbij komt, levert het eerste "
                "meetpunt. Daarna voorspelt elke mail wanneer de kaarten voor Málaga – Betis online komen.")
    else:
        days = ", ".join(DAY_NAMES[DAYS.index(d)] for d in sorted(set(p.weekdays), key=DAYS.index))
        hours = f"tussen {min(p.hours)}:00 en {max(p.hours) + 1}:00 uur" if p.hours else ""
        text = (f"Tot nu toe kwamen de kaarten op {days} online, {hours}, gemiddeld {lead} voor de aftrap. "
                f"Houdt dat stand, dan verschijnen de kaarten voor Málaga – Betis ({fmt_date(p.target_date)}) "
                f"rond {fmt_date(p.predicted)}.")
    blocks.append((row(f'<div style="font-family:{FONT};font-size:14px;line-height:1.6;color:{TEXT}">{esc(text)}</div>',
                       "padding:14px 32px 0"), text))
    return blocks


def upcoming(fixtures: list[tuple]) -> tuple[str, str]:
    if not fixtures:
        return paragraph("Alle verwachte thuiswedstrijden tot en met Málaga – Betis staan al online.")
    out_h, out_t = [], []
    for n, d, name, _ in fixtures:
        target = TARGET_FROM <= d <= TARGET_TO
        when = datetime(d.year, d.month, d.day)
        tag = f'&nbsp; {pill("Ons weekend", RED, RED_SOFT)}' if target else ""
        out_h.append(
            f'<tr><td width="64" valign="middle" style="padding:8px 0">{date_tile(when, target, compact=True)}</td>'
            f'<td valign="middle" style="padding:8px 0 8px 14px;font-family:{FONT}">'
            f'<div style="font-size:15px;font-weight:700;color:{INK}">Málaga CF – {esc(name)}{tag}</div>'
            f'<div style="font-size:13px;color:{MUTED}">Speeldag {n} · weekend van {esc(fmt_date(d))}; '
            f'dag en aftrap volgen</div></td></tr>')
        out_t.append(f"- Speeldag {n}: Málaga CF – {name}, weekend van {fmt_date(d)}")
    return row(table("".join(out_h)), "padding:8px 32px 0"), "\n".join(out_t)


def season_sections(mail: Mail, rows: list[Row], p: Pattern, fixtures: list[tuple]) -> Mail:
    mail.add(*section("Verkooppatroon"))
    for block in pattern_section(p):
        mail.add(*block)
    mail.add(*section("Seizoensoverzicht"))
    mail.add(*season_table(rows))
    mail.add(*section("Verwachte thuiswedstrijden"))
    mail.add(*upcoming(fixtures))
    mail.add(row("", "padding:32px 0 0"), "")
    return mail


# --- the mails ------------------------------------------------------------------------------

def new_events_mail(new: list[Event], target_news: list[tuple[Event, list[str]]], rows: list[Row],
                    p: Pattern, fixtures: list[tuple]) -> Mail:
    targets = [ev for ev in new if is_target(ev.title, ev.when)]
    if targets:
        ev = targets[0]
        mail = Mail(f"Betis-kaarten online: {display(ev.title)} · {fmt_dt(ev.when)}",
                    "Dit is de wedstrijd van ons weekend. Wees er snel bij.")
        mail.add(*hero("Betis-alarm", "De kaarten voor Málaga – Betis zijn online",
                       "Dit is de wedstrijd van ons weekend. Een derby verkoopt snel: wacht niet te lang.", alarm=True))
    elif new:
        ev = new[0]
        more = f" (+{len(new) - 1})" if len(new) > 1 else ""
        mail = Mail(f"Nieuw online: {display(ev.title)} · {fmt_dt(ev.when)}{more}",
                    f"Kaarten voor {display(ev.title)} staan sinds zojuist online.")
        mail.add(*hero("Nieuw in de verkoop", display(ev.title),
                       f"{long_date(ev.when)} · {ev.when:%H:%M} uur" if ev.when else "Datum nog onbekend"))
    else:
        ev = target_news[0][0]
        mail = Mail(f"Update Málaga – Betis: {target_news[0][1][0]}",
                    "Er is iets veranderd bij de wedstrijd van ons weekend.")
        mail.add(*hero("Update Betis", "Er is iets veranderd bij Málaga – Betis",
                       "De wedstrijd van ons weekend. Dit is er gewijzigd sinds de vorige controle.", alarm=True))
    for ev in new:
        mail.add(*match_card(ev))
    for ev, lines in target_news:
        mail.add(*match_card(ev, lines, "Gewijzigd"))
    return season_sections(mail, rows, p, fixtures)


def welcome_mail(events: list[Event], rows: list[Row], p: Pattern, fixtures: list[tuple]) -> Mail:
    mail = Mail("Málaga Ticketalert staat aan",
                "Vanaf nu hoor je het direct als er kaarten voor een thuiswedstrijd online komen.")
    mail.add(*hero("Systeem actief", "De ticketalert voor Málaga CF staat aan",
                   "Vanaf nu hoor je het direct als er kaarten voor een nieuwe thuiswedstrijd online komen."))
    mail.add(*paragraph(
        "Elke tien minuten controleren we entradas.malagacf.com. Komt er een nieuwe wedstrijd in de verkoop, "
        "dan krijg je meteen een mail met een directe link om kaarten te kopen. Voor Málaga – Real Betis op "
        f"{long_date(HOME_FIXTURES[-1][1])}, de wedstrijd van ons weekend, volgt een "
        "aparte alarmmelding, en daarna een update zodra vakken uitverkopen."))
    mail.add(*section("Nu in de verkoop"))
    for ev in events:
        mail.add(*match_card(ev))
    return season_sections(mail, rows, p, fixtures)


def status_mail(rows: list[Row], p: Pattern, fixtures: list[tuple], failures_this_week: int,
                last_change: str | None) -> Mail:
    mail = Mail("Weekstatus Málaga Ticketalert", "De controle draait. Overzicht van de afgelopen week.")
    if failures_this_week:
        mail.add(*hero("Weekstatus", "De controle draait, met enkele haperingen",
                       "Deze update komt elke maandag. Geen maandagmail? Dan is er iets mis."))
    else:
        mail.add(*hero("Weekstatus", "Alles werkt naar behoren",
                       "Deze update komt elke maandag. Geen maandagmail? Dan is er iets mis."))
    mail.add(*stats([(str(failures_this_week), "mislukte controles, afgelopen 7 dagen"),
                     (last_change or "geen", "laatste wijziging op de site"),
                     (str(len(fixtures)), "thuiswedstrijden nog te verwachten")]))
    return season_sections(mail, rows, p, fixtures)


def failure_mail(failures: int, error: str) -> Mail:
    mail = Mail("Storing Málaga Ticketalert", "De controle van de kaartensite mislukt al een uur.")
    mail.add(*hero("Storing", "De controle mislukt al een uur", alarm=True))
    mail.add(*paragraph(f"{failures} controles op rij zijn mislukt. Laatste foutmelding: {error}. "
                        "Je krijgt automatisch bericht zodra het weer werkt."))
    return mail.add(row("", "padding:32px 0 0"), "")


def recovered_mail(failures: int) -> Mail:
    mail = Mail("Hersteld: Málaga Ticketalert werkt weer", "De controle van de kaartensite werkt weer.")
    mail.add(*hero("Hersteld", "De controle werkt weer"))
    mail.add(*paragraph(f"Na {failures} mislukte controles lukt het weer om de kaartensite te lezen."))
    return mail.add(row("", "padding:32px 0 0"), "")


def queue_mail() -> Mail:
    mail = Mail("Wachtrij actief op de kaartensite van Málaga CF",
                "Er is een grote verkoop bezig. Kijk zelf even op de site.")
    mail.add(*hero("Wachtrij actief", "De kaartensite zet bezoekers in een wachtrij",
                   "Dat gebeurt alleen bij een grote verkoop.", alarm=True))
    mail.add(*paragraph("Zolang de wachtrij aan staat kan de controle de wedstrijden niet zien. Kijk zelf even "
                        "op de site of er iets voor ons bij zit."))
    mail.add(row(button("Naar entradas.malagacf.com", LISTING_URL, RED), "padding:20px 32px 0"), LISTING_URL)
    return mail.add(row("", "padding:32px 0 0"), "")


def finished_mail(rows: list[Row], p: Pattern, fixtures: list[tuple]) -> Mail:
    mail = Mail("Málaga Ticketalert gestopt: het seizoensoverzicht", "Het weekend is voorbij; dit is wat we zagen.")
    mail.add(*hero("Afgerond", "De ticketalert is gestopt",
                   f"Het weekend van {fmt_date(TARGET_FROM)} is voorbij. Dit is wat we het hele seizoen zagen."))
    mail.add(*paragraph("De GitHub-workflow kan nu uit: Actions → Check → Disable workflow."))
    return season_sections(mail, rows, p, fixtures)


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
        mail.blocks.insert(0, banner(f"Alleen naar jou gestuurd: zonder SMTP-instellingen kan Resend niet "
                                     f"mailen naar {', '.join(skipped)}."))
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
