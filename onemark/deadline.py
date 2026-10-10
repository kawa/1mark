"""Official betting deadline (締切予定時刻) ingestion from the BOAT RACE racelist page, and exhibition estimate.

The official racelist page of any race lists the deadlines of all 12 races of that venue/date, so one fetched
page covers the whole batch (no extra requests). Observation only: deadlines that are absent or malformed are
reported as unknown, never invented. The exhibition time is only an ESTIMATE (deadline minus offset),
never an official exhibition start time.
"""
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser

from onemark import boatrace as br

DEFAULT_EXHIBITION_OFFSET_MINUTES = 20
ESTIMATE_KIND = "estimated_from_betting_deadline"
DEADLINE_LABEL = "締切予定時刻"
_LINK = re.compile(r"[?&]rno=(\d+)&jcd=(\d{2})&hd=(\d{8})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")


class _Tables(HTMLParser):
    """Collects, per <table>, rows of cells as (text, hrefs)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": "", "hrefs": []}
            self._row.append(self._cell)
        elif tag == "a" and self._cell is not None and dict(attrs).get("href"):
            self._cell["hrefs"].append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _unknown(date, jcd, rno, raw, url, retrieved_at, reason):
    return {"race_id": f"{date}-{jcd}-{rno:02d}", "date": date, "jcd": jcd, "rno": rno, "status": "unknown",
            "betting_deadline": None, "raw_time": raw, "reason": reason, "source_url": url,
            "retrieved_at": retrieved_at}


def parse_deadlines(html, date, jcd, source_url, retrieved_at):
    """Return {rno: schedule entry} for rno 1-12. Each race is `available` (ISO 8601 +09:00) or `unknown` with a reason."""
    parser = _Tables()
    parser.feed(html)
    header = row = None
    for table in parser.tables:
        for r in table:
            if r and r[0]["text"].strip() == DEADLINE_LABEL:
                row, header = r[1:], table[0] if table[0] is not r else None
        if row is not None:
            break
    if row is None:
        return {n: _unknown(date, jcd, n, None, source_url, retrieved_at, "deadline row not found on official page")
                for n in range(1, 13)}
    cols = {}  # rno -> raw cell text, associated via the header links (date/venue verified)
    for i, cell in enumerate(header or []):
        for href in cell["hrefs"]:
            m = _LINK.search(href.replace("&amp;", "&"))
            if m and m.group(2) == jcd and m.group(3) == date and 1 <= int(m.group(1)) <= 12:
                idx = i - (len(header) - len(row))
                if 0 <= idx < len(row):
                    cols[int(m.group(1))] = row[idx]["text"].strip()
    out = {}
    for n in range(1, 13):
        if n not in cols:
            out[n] = _unknown(date, jcd, n, None, source_url, retrieved_at,
                              "race column not found for this date/venue on official page")
            continue
        raw = cols[n]
        m = _TIME.fullmatch(raw)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            out[n] = _unknown(date, jcd, n, raw, source_url, retrieved_at, "deadline missing or malformed")
            continue
        try:
            dt = datetime(int(date[:4]), int(date[4:6]), int(date[6:]), int(m.group(1)), int(m.group(2)), tzinfo=br.JST)
        except ValueError:
            out[n] = _unknown(date, jcd, n, raw, source_url, retrieved_at, "invalid date")
            continue
        out[n] = {"race_id": f"{date}-{jcd}-{n:02d}", "date": date, "jcd": jcd, "rno": n, "status": "available",
                  "betting_deadline": dt.isoformat(), "raw_time": raw, "reason": None, "source_url": source_url,
                  "retrieved_at": retrieved_at}
    return out


def estimate_exhibition(entry, offset_minutes=DEFAULT_EXHIBITION_OFFSET_MINUTES):
    """Estimated exhibition CHECK time = official betting deadline - offset. Unknown if the deadline is unknown."""
    if not entry or not entry.get("betting_deadline"):
        return {"kind": "unknown"}
    t = datetime.fromisoformat(entry["betting_deadline"]) - timedelta(minutes=offset_minutes)
    return {"kind": ESTIMATE_KIND, "time": t.isoformat(), "betting_deadline": entry["betting_deadline"],
            "offset_minutes": offset_minutes,
            "source": "estimate: betting deadline minus offset (not an official exhibition start time)"}
