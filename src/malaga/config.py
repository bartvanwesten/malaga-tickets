import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"

load_dotenv(ROOT / ".env")

TZ = ZoneInfo("Europe/Madrid")  # same clock as Amsterdam
LISTING_URL = "https://entradas.malagacf.com/?idioma=EN&act=1"

# The weekend we're going for, and when the watch ends.
TARGET_FROM = date(2027, 2, 19)
TARGET_TO = date(2027, 2, 22)
TARGET_KEYWORD = "BETIS"
END = date(2027, 2, 23)

# Remaining LaLiga 26/27 home matches up to the target. Dates are the matchday weekends from the
# official fixture list; LaLiga fixes the exact day and kick-off about 2-3 weeks ahead.
HOME_FIXTURES = [
    (12, date(2026, 11, 8), "Racing de Santander", "RACING"),
    (14, date(2026, 11, 29), "Athletic Club", "ATHLETIC"),
    (16, date(2026, 12, 13), "FC Barcelona", "BARCEL"),
    (18, date(2027, 1, 3), "Elche CF", "ELCHE"),
    (20, date(2027, 1, 17), "Real Madrid", "REAL MADRID"),
    (22, date(2027, 1, 31), "Deportivo Alavés", "ALAV"),
    (25, date(2027, 2, 21), "Real Betis", "BETIS"),
]

FAIL_ALERT_AFTER = 6  # consecutive failed checks (~1 hour) before mailing STATUS_TO
STATUS_WEEKDAY = 0  # weekly status mail on Monday...
STATUS_HOUR = 9  # ...from 09:00


def now() -> datetime:
    return datetime.now(TZ)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
