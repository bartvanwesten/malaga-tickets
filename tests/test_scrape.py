from datetime import datetime
from pathlib import Path

import httpx
import pytest

from malaga.config import TZ
from malaga.scrape import QueueActive, fetch, parse_event, parse_listing, parse_when

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_listing():
    [ev] = parse_listing(read("listing_en.html"))
    assert ev.id == "2304"
    assert ev.title == "MÁLAGA CF - R. RACING CLUB"
    assert ev.venue == "LAROSALEDA"
    assert ev.when == datetime(2026, 11, 7, 14, 0, tzinfo=TZ)
    assert ev.url == "https://entradas.malagacf.com/en/malaga-cf---r.-racing-club-20261108-2100"
    assert ev.button == "Purchase tickets"


def test_event_areas():
    areas = parse_event(read("event_en.html"))
    assert len(areas) == 22
    by_name = {a.name: a for a in areas}
    assert by_name["FONDO ALTO"].status == "AVAILABLE" and by_name["FONDO ALTO"].price == 32.5
    assert by_name["ANILLO INF CUBIERTO"].status == "SOLD OUT"
    assert sum(a.status == "AVAILABLE" for a in areas) == 21


def test_when():
    assert parse_when("Sunday, 21/2/2027 18:30") == datetime(2027, 2, 21, 18, 30, tzinfo=TZ)
    assert parse_when("Date to be confirmed") is None


def test_waiting_room_detected():
    def handler(request: httpx.Request):
        if request.url.host == "entradas.malagacf.com":
            return httpx.Response(307, headers={"Location": "https://queue.koobin.com/?event_id=x"})
        return httpx.Response(200, text="You are in the queue")
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(QueueActive):
        fetch(http, "https://entradas.malagacf.com/?idioma=EN&act=1")


def test_layout_change_is_an_error():
    from malaga.scrape import ScrapeError
    with pytest.raises(ScrapeError):
        parse_listing("<html><body>Maintenance</body></html>")
