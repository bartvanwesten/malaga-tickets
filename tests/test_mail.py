from datetime import datetime

from malaga import mail as m
from malaga.config import TZ
from malaga.main import status_due
from malaga.scrape import Area, Event
from malaga.track import pattern, season_rows, update, upcoming_fixtures

BETIS = Event("2400", "MÁLAGA CF - REAL BETIS", "LAROSALEDA", "21/2/2027 21:00",
              datetime(2027, 2, 21, 21, 0, tzinfo=TZ), "https://entradas.malagacf.com/en/betis", "Purchase tickets",
              [Area("FONDO ALTO", "AVAILABLE", 60.0), Area("TRIBUNA", "SOLD OUT", 90.0)])
NOW = datetime(2027, 2, 8, 10, 5, tzinfo=TZ)


def parts(state):
    return season_rows(state), pattern(state), upcoming_fixtures(state, NOW.date())


def test_target_mail():
    state = {}
    update(state, [BETIS], NOW)
    mail = m.new_events_mail([BETIS], [], *parts(state))
    assert mail.subject.startswith("Betis-kaarten online")
    assert "1 van 2 vakken beschikbaar · vanaf €60,00" in mail.text
    assert 'href="https://entradas.malagacf.com/en/betis"' in mail.html


def test_without_smtp_only_owner_gets_it(monkeypatch):
    for var in ("SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STATUS_TO", "me@example.org")
    sent = {}
    monkeypatch.setattr(m, "_send_resend", lambda mail, to: sent.update(mail=mail, to=to))
    m.send(m.Mail("x").add("<p>hi</p>", "hi"), ["me@example.org", "friend@example.org"])
    assert sent["to"] == ["me@example.org"]
    assert "friend@example.org" in sent["mail"].text


def test_status_due():
    monday_8 = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)
    monday_9 = datetime(2026, 11, 2, 9, 5, tzinfo=TZ)
    tuesday = datetime(2026, 11, 3, 7, 0, tzinfo=TZ)
    assert not status_due({"status_sent": "2026-10-26"}, monday_8)
    assert status_due({"status_sent": "2026-10-26"}, monday_9)
    assert status_due({"status_sent": "2026-10-26"}, tuesday)  # Monday's run was missed
    assert not status_due({"status_sent": "2026-11-02"}, tuesday)


FORWARD_WORDS = ("staat aan", "nieuw online", "betis", "wachtrij", "gestopt")  # the Outlook forwarding rule


def forwarded(subject: str) -> bool:
    return any(w in subject.lower() for w in FORWARD_WORDS)


def test_forwarding_rule_subjects():
    state = {}
    update(state, [BETIS], NOW)
    athletic = Event("2320", "MÁLAGA CF - ATHLETIC CLUB", "LAROSALEDA", "29/11/2026 18:30",
                     datetime(2026, 11, 29, 18, 30, tzinfo=TZ), "https://x", "Purchase tickets", None)
    for mail in (m.welcome_mail([BETIS], *parts(state)), m.new_events_mail([athletic], [], *parts(state)),
                 m.new_events_mail([BETIS], [], *parts(state)), m.new_events_mail([], [(BETIS, ["x"])], *parts(state)),
                 m.queue_mail(), m.finished_mail(*parts(state))):
        assert forwarded(mail.subject), mail.subject
    for mail in (m.status_mail(*parts(state), 0, None), m.failure_mail(6, "x"), m.recovered_mail(6)):
        assert not forwarded(mail.subject), mail.subject
