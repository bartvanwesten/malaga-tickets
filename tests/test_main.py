from datetime import datetime, timedelta

from malaga import main
from malaga.config import TZ
from malaga.scrape import Event

AT = datetime(2026, 11, 9, 15, 0, tzinfo=TZ)
ATHLETIC = Event("2466", "MÁLAGA CF - ATHLETIC CLUB", "LAROSALEDA", "Saturday, 28/11/2026 16:15",
                 datetime(2026, 11, 28, 16, 15, tzinfo=TZ), "https://x/athletic", "Purchase tickets", [])


def run(monkeypatch, tmp_path, state, at=AT, events=(ATHLETIC,)):
    """One live check against a fake site; returns (saved state, subjects mailed)."""
    sent = []
    monkeypatch.setattr(main, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(main, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(main, "COMMIT_FLAG", tmp_path / ".commit-now")
    monkeypatch.setattr(main, "noteworthy", False)
    monkeypatch.setattr(main, "now", lambda: at)
    monkeypatch.setattr(main, "scrape", lambda: list(events))
    monkeypatch.setattr(main.m, "send", lambda mail, to: sent.append(mail.subject))
    monkeypatch.setenv("MAIL_TO", "a@example.org")
    monkeypatch.setenv("STATUS_TO", "a@example.org")
    main.write_json(main.STATE_FILE, state)
    assert main.main([]) == 0
    return main.read_json(main.STATE_FILE), sent


def test_gap_is_mailed_and_new_event_gets_its_window(monkeypatch, tmp_path):
    before = (AT - timedelta(hours=8)).isoformat(timespec="minutes")
    state = {"events": {"1": {"listed": False, "title": "x", "first_seen": before}}, "last_check": before,
             "status_sent": AT.date().isoformat()}
    saved, sent = run(monkeypatch, tmp_path, state)
    assert any(s.startswith("Onderbreking") for s in sent), sent
    assert any("Nieuw online" in s for s in sent), sent
    assert saved["events"]["2466"]["not_before"] == before
    assert saved["last_check"] == AT.isoformat(timespec="minutes")
    assert saved["coverage"]["longest_gap"] == 480
    assert (tmp_path / ".commit-now").exists()


def test_regular_checks_stay_quiet(monkeypatch, tmp_path):
    before = (AT - timedelta(minutes=5)).isoformat(timespec="minutes")
    state = {"events": {"2466": {**ATHLETIC.to_dict(), "first_seen": before, "baseline": False, "listed": True,
                                 "areas": {}}},
             "last_check": before, "status_sent": AT.date().isoformat(),
             "coverage": {"since": before, "checks": 1, "longest_gap": 0}}
    saved, sent = run(monkeypatch, tmp_path, state)
    assert sent == []
    assert saved["coverage"] == {"since": before, "checks": 2, "longest_gap": 5}
    assert not (tmp_path / ".commit-now").exists()


def test_a_crash_counts_as_a_failed_check(monkeypatch, tmp_path):
    def boom():
        raise KeyError("layout")
    monkeypatch.setattr(main, "scrape", boom)
    before = (AT - timedelta(minutes=5)).isoformat(timespec="minutes")
    sent = []
    monkeypatch.setattr(main, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(main, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(main, "COMMIT_FLAG", tmp_path / ".commit-now")
    monkeypatch.setattr(main, "now", lambda: AT)
    monkeypatch.setattr(main.m, "send", lambda mail, to: sent.append(mail.subject))
    monkeypatch.setenv("STATUS_TO", "a@example.org")
    main.write_json(main.STATE_FILE, {"events": {}, "last_check": before, "failures": main.FAIL_ALERT_AFTER - 1,
                                      "status_sent": AT.date().isoformat()})
    assert main.main([]) == 0
    assert main.read_json(main.STATE_FILE)["failures"] == main.FAIL_ALERT_AFTER
    assert sent == ["Storing Málaga Ticketalert"]
