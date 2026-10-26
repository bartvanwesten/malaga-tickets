"""What changed since the last check, and the release pattern so far.

data/state.json holds every event ever listed (first/last seen, latest details, stand areas);
data/history.jsonl gets one line per change, so release times can be analysed afterwards.
"""

import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import HOME_FIXTURES, TARGET_FROM, TARGET_KEYWORD, TARGET_TO
from .scrape import Event

DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
SOLD_OUT = "SOLD OUT"


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "datum onbekend"
    return f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]} {dt:%H:%M}"


def fmt_date(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def fmt_price(p: float | None) -> str:
    return "?" if p is None else f"€{p:.2f}".replace(".", ",")


def display(title: str) -> str:
    """MÁLAGA CF - R. RACING CLUB → Málaga CF – R. Racing Club"""
    return " ".join(w if w in ("CF", "FC", "UD", "CD", "RCD", "SD") else w.title() for w in title.split()).replace(" - ", " – ")


def venue_name(venue: str) -> str:
    return "La Rosaleda" if normalize(venue).replace(" ", "") == "LAROSALEDA" else venue.title()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.upper().split())


def is_home(title: str) -> bool:
    return normalize(title).startswith("MALAGA CF -")


def fixture_of(title: str, when: datetime | None):
    """The expected home fixture this event is, if any."""
    if not is_home(title):
        return None
    opponent = normalize(title).split(" - ", 1)[1]
    for fx in HOME_FIXTURES:
        if fx[3] in opponent and (not when or abs((when.date() - fx[1]).days) <= 4):
            return fx
    return None


def is_target(title: str, when: datetime | None) -> bool:
    if not is_home(title):
        return False
    if when:
        return TARGET_FROM <= when.date() <= TARGET_TO
    return TARGET_KEYWORD in normalize(title)


def _when(saved: dict) -> datetime | None:
    return datetime.fromisoformat(saved["when"]) if saved.get("when") else None


def _areas(ev: Event) -> dict | None:
    return None if ev.areas is None else {a.name: [a.status, a.price] for a in ev.areas}


@dataclass
class Changes:
    new: list[Event] = field(default_factory=list)
    changed: list[tuple[Event, list[str]]] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    def target_news(self) -> list[tuple[Event, list[str]]]:
        """Changes to the target match other than it appearing."""
        out = [(ev, lines) for ev, lines in self.changed if is_target(ev.title, ev.when)]
        out += [(saved_event(s), ["staat niet meer op de site"]) for s in self.removed
                if is_target(s["title"], _when(s))]
        return out


def saved_event(s: dict) -> Event:
    return Event(id="", title=s["title"], venue=s["venue"], when_text=s["when_text"], when=_when(s),
                 url=s["url"], button=s["button"], areas=None)


def area_changes(old: dict | None, new: dict | None) -> list[str]:
    if old is None or new is None:
        return []
    lines = []
    for name, (status, price) in new.items():
        if name not in old:
            lines.append(f"{name}: nieuw vak ({status.lower()}, {fmt_price(price)})")
            continue
        old_status, old_price = old[name]
        if status != old_status:
            lines.append(f"{name}: {'uitverkocht' if status == SOLD_OUT else status.lower()}")
        if price != old_price:
            lines.append(f"{name}: {fmt_price(old_price)} → {fmt_price(price)}")
    lines += [f"{name}: vak verdwenen" for name in old if name not in new]
    return lines


def update(state: dict, events: list[Event], at: datetime, baseline: bool = False) -> Changes:
    """Fold this check into state; baseline marks events that were already online when we started."""
    ch = Changes()
    saved = state.setdefault("events", {})
    stamp = at.isoformat(timespec="minutes")
    listed = {ev.id for ev in events}

    for ev in events:
        areas = _areas(ev)
        s = saved.get(ev.id)
        if s is None:
            ch.new.append(ev)
            s = saved[ev.id] = {"first_seen": stamp, "baseline": baseline}
            ch.history.append({"at": stamp, "event": ev.id, "kind": "new", **ev.to_dict()})
        elif not s["listed"]:
            ch.changed.append((ev, ["weer op de site"]))
            ch.history.append({"at": stamp, "event": ev.id, "kind": "back"})
            s.pop("removed_at", None)
        else:
            lines = []
            if ev.when_text != s["when_text"]:
                lines.append(f"datum/aftrap: {fmt_dt(_when(s))} → {fmt_dt(ev.when)}")
            if ev.title != s["title"]:
                lines.append(f"titel: {s['title']} → {ev.title}")
            if ev.button != s["button"]:
                lines.append(f"knop: {s['button']} → {ev.button}")
            area_lines = area_changes(s.get("areas"), areas)
            if lines or area_lines:
                ch.changed.append((ev, lines + area_lines))
                ch.history.append({"at": stamp, "event": ev.id, "kind": "changed", "changes": lines + area_lines})
        s.update(title=ev.title, venue=ev.venue, when_text=ev.when_text,
                 when=ev.when.isoformat() if ev.when else None, url=ev.url, button=ev.button,
                 last_seen=stamp, listed=True)
        if areas is not None:
            s["areas"] = areas
            statuses = [st for st, _ in areas.values()]
            if SOLD_OUT in statuses and not s.get("first_area_sold_out"):
                s["first_area_sold_out"] = stamp
            if statuses and all(st == SOLD_OUT for st in statuses) and not s.get("sold_out"):
                s["sold_out"] = stamp
                ch.history.append({"at": stamp, "event": ev.id, "kind": "sold_out"})

    for eid, s in saved.items():
        if s.get("listed") and eid not in listed:
            s["listed"] = False
            s["removed_at"] = stamp
            ch.removed.append(s)
            ch.history.append({"at": stamp, "event": eid, "kind": "removed"})
    return ch


@dataclass
class Row:
    title: str
    when: datetime | None
    first_seen: datetime
    baseline: bool
    first_area_sold_out: datetime | None
    sold_out: datetime | None
    listed: bool
    url: str

    @property
    def lead_days(self) -> float | None:
        return (self.when - self.first_seen).total_seconds() / 86400 if self.when else None


def season_rows(state: dict) -> list[Row]:
    """Home matches seen so far, in match order."""
    def dt(v):
        return datetime.fromisoformat(v) if v else None
    rows = [Row(s["title"], _when(s), dt(s["first_seen"]), s.get("baseline", False),
                dt(s.get("first_area_sold_out")), dt(s.get("sold_out")), s.get("listed", False), s["url"])
            for s in state.get("events", {}).values() if is_home(s["title"])]
    return sorted(rows, key=lambda r: (r.when is None, r.when or r.first_seen))


@dataclass
class Pattern:
    observed: int  # releases seen happening (not already online at the start)
    median_lead: float | None  # days between going online and kick-off
    weekdays: list[str]
    hours: list[int]
    target_date: date
    predicted: date | None
    target_row: Row | None


def pattern(state: dict) -> Pattern:
    rows = season_rows(state)
    seen = [r for r in rows if not r.baseline and r.when]
    leads = [r.lead_days for r in seen]
    median = statistics.median(leads) if leads else None
    target_row = next((r for r in rows if is_target(r.title, r.when)), None)
    target_date = target_row.when.date() if target_row and target_row.when else HOME_FIXTURES[-1][1]
    predicted = target_date - timedelta(days=round(median)) if median is not None and not target_row else None
    return Pattern(
        observed=len(seen),
        median_lead=median,
        weekdays=[DAYS[r.first_seen.weekday()] for r in seen],
        hours=[r.first_seen.hour for r in seen],
        target_date=target_date,
        predicted=predicted,
        target_row=target_row,
    )


def upcoming_fixtures(state: dict, today: date) -> list[tuple]:
    """Expected home matches not yet seen on the site."""
    seen = {fixture_of(s["title"], _when(s)) for s in state.get("events", {}).values()}
    return [fx for fx in HOME_FIXTURES if fx not in seen and fx[1] >= today]
