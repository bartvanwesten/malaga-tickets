"""Read entradas.malagacf.com (Koobin): the event grid, and per match the stand areas on sale.

Every request first bounces through Koobin's waiting room (queue.koobin.com), which hands back a
token that the site swaps for a cookie; a client that keeps cookies gets straight through. When
the waiting room is actually queueing (a big sale), the redirect stays on queue.koobin.com.
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .config import LISTING_URL, TZ

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
QUEUE_HOST = "queue.koobin.com"
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


class ScrapeError(RuntimeError):
    pass


class QueueActive(ScrapeError):
    """The waiting room is holding visitors: something is in high demand."""


@dataclass
class Area:
    name: str
    status: str  # AVAILABLE, SOLD OUT, ...
    price: float | None


@dataclass
class Event:
    id: str
    title: str
    venue: str
    when_text: str
    when: datetime | None
    url: str
    button: str
    areas: list[Area] | None = field(default=None)  # None: page not read

    def to_dict(self) -> dict:
        d = asdict(self)
        d["when"] = self.when.isoformat() if self.when else None
        return d


def client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True, max_redirects=10,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})


def fetch(http: httpx.Client, url: str) -> str:
    try:
        r = http.get(url)
    except httpx.TooManyRedirects as e:
        raise ScrapeError("redirect loop (waiting room cookie not accepted?)") from e
    except httpx.HTTPError as e:
        raise ScrapeError(f"{type(e).__name__}: {e}") from e
    if urlparse(str(r.url)).hostname == QUEUE_HOST:
        raise QueueActive(f"waiting room active for {url}")
    if r.status_code != 200:
        raise ScrapeError(f"HTTP {r.status_code} for {url}")
    return r.text


def _text(node: Node | None) -> str:
    return " ".join(node.text().split()) if node else ""


def parse_when(text: str) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, month, year, hour, minute = map(int, m.groups())
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def parse_listing(html: str) -> list[Event]:
    tree = HTMLParser(html)
    if not tree.css_first(".gridGen, #contingut"):
        raise ScrapeError("event grid not found: page layout changed?")
    events = []
    for card in tree.css("div.gFitxaGen[id^='gFitxaGen_']"):
        when_text = _text(card.css_first(".dataE"))
        link = card.css_first(".gBoto a[href]")
        events.append(Event(
            id=card.attributes["id"].removeprefix("gFitxaGen_"),
            title=_text(card.css_first(".gTitSpan")) or _text(card.css_first(".gTitol")),
            venue=_text(card.css_first(".nomEspai")),
            when_text=when_text,
            when=parse_when(when_text),
            url=link.attributes.get("href", "") if link else "",
            button=_text(card.css_first(".gBoto")),
        ))
    return events


def parse_event(html: str) -> list[Area]:
    """The stand areas of a match page with their status and price."""
    tree = HTMLParser(html)
    areas = []
    for block in tree.css("div.seleccioarea"):
        name = _text(block.css_first(".areaNom"))
        price = None
        if strong := block.css_first(".areaPreu strong"):
            if m := PRICE_RE.search(strong.text()):
                price = float(m.group(1).replace(",", "."))
        areas.append(Area(name=name, status=_text(block.css_first(".areaLliures")).upper(), price=price))
    return areas


def scrape(http: httpx.Client | None = None, with_areas: bool = True) -> list[Event]:
    http = http or client()
    events = parse_listing(fetch(http, LISTING_URL))
    if with_areas:
        for ev in events:
            if not ev.url:
                continue
            try:
                ev.areas = parse_event(fetch(http, ev.url))
            except ScrapeError as e:  # the listing is what matters; areas are extra
                log.warning("Areas of %s not read: %s", ev.title, e)
    return events
