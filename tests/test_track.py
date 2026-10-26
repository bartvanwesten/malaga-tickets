from datetime import date, datetime

from malaga.config import TZ
from malaga.scrape import Area, Event
from malaga.track import fixture_of, is_target, pattern, update, upcoming_fixtures


def ev(id, title, when, areas=None):
    return Event(id=id, title=title, venue="LAROSALEDA", when_text=f"{when:%d/%m/%Y %H:%M}" if when else "",
                 when=when, url=f"https://entradas.malagacf.com/en/{id}", button="Purchase tickets", areas=areas)


def at(month, day, hour=10, year=2026):
    return datetime(year, month, day, hour, 0, tzinfo=TZ)


RACING = ev("2304", "MÁLAGA CF - R. RACING CLUB", at(11, 7, 14), [Area("FONDO ALTO", "AVAILABLE", 32.5)])
ATHLETIC = ev("2320", "MÁLAGA CF - ATHLETIC CLUB", at(11, 29, 18))
BETIS = ev("2400", "MÁLAGA CF - REAL BETIS", datetime(2027, 2, 21, 21, 0, tzinfo=TZ))


def test_first_run_is_baseline_then_new_events():
    state = {}
    ch = update(state, [RACING], at(10, 26), baseline=True)
    assert [e.id for e in ch.new] == ["2304"] and state["events"]["2304"]["baseline"]

    ch = update(state, [RACING], at(10, 26, 11))
    assert not ch.new and not ch.changed

    ch = update(state, [RACING, ATHLETIC], at(11, 16, 10))
    assert [e.id for e in ch.new] == ["2320"]
    assert not state["events"]["2320"]["baseline"]


def test_area_and_date_changes():
    state = {}
    update(state, [RACING], at(10, 26))
    sold = ev("2304", RACING.title, at(11, 8, 21), [Area("FONDO ALTO", "SOLD OUT", 35.5)])
    ch = update(state, [sold], at(10, 27))
    [(_, lines)] = ch.changed
    assert any(line.startswith("datum/aftrap") for line in lines)
    assert "FONDO ALTO: uitverkocht" in lines
    assert "FONDO ALTO: €32,50 → €35,50" in lines
    assert state["events"]["2304"]["sold_out"]


def test_removed_and_back():
    state = {}
    update(state, [RACING], at(10, 26))
    ch = update(state, [], at(11, 8))
    assert ch.removed and not state["events"]["2304"]["listed"]
    ch = update(state, [RACING], at(11, 8, 11))
    assert not ch.new and ch.changed[0][1] == ["weer op de site"]


def test_target():
    assert is_target(BETIS.title, BETIS.when)
    assert is_target("MÁLAGA CF - REAL BETIS", None)
    assert not is_target("REAL BETIS - MÁLAGA CF", at(10, 31))  # away
    assert not is_target(RACING.title, RACING.when)
    assert not is_target("MÁLAGA CF - ATLÉTICO DE MADRID", datetime(2027, 2, 28, 18, tzinfo=TZ))


def test_target_changes_are_news():
    state = {}
    update(state, [RACING, BETIS], at(2, 8, year=2027))
    sold = ev("2400", BETIS.title, BETIS.when, [Area("FONDO ALTO", "SOLD OUT", 60.0)])
    update(state, [RACING, sold], at(2, 8, 11, year=2027))  # areas read the first time: no news
    ch = update(state, [RACING, ev("2400", BETIS.title, BETIS.when, [Area("FONDO ALTO", "AVAILABLE", 60.0)])],
                at(2, 9, year=2027))
    assert [e.title for e, _ in ch.target_news()] == [BETIS.title]


def test_fixtures_and_pattern():
    assert fixture_of(RACING.title, RACING.when)[2] == "Racing de Santander"
    assert fixture_of("MÁLAGA CF - REAL MADRID", datetime(2027, 1, 16, 21, tzinfo=TZ))[2] == "Real Madrid"
    assert fixture_of("MÁLAGA CF FEMENINO - SPORTING", at(11, 8)) is None

    state = {}
    update(state, [RACING], at(10, 26), baseline=True)
    update(state, [RACING, ATHLETIC], at(11, 16, 10))  # 13.3 days ahead, Monday
    p = pattern(state)
    assert p.observed == 1 and round(p.median_lead) == 13
    assert p.weekdays == ["ma"] and p.hours == [10]
    assert p.predicted == date(2027, 2, 8)
    assert [fx[2] for fx in upcoming_fixtures(state, date(2026, 11, 20))][0] == "FC Barcelona"
