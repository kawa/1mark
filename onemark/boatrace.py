"""BOAT RACE entry-list (racelist) ingestion: fetch, parse, build an evidence bundle.

Stdlib only. Observation only: no predictions, betting, or inference.
Raw strings are preserved; unparseable/blank values are reported in missing_data, never as 0.
"""
import argparse
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://www.boatrace.jp/owpc/pc/race/racelist"
USER_AGENT = "1mark-poc/0.1 (+https://github.com/kawa/1mark; manual low-volume entry-list retrieval)"
JST = timezone(timedelta(hours=9))
MISSING_TOKENS = {"", "-", "--", "---", "–", "ー"}
SCHEMA_VERSION = "1.0.0"
VOCABULARY_VERSION = "1.0.0"
NS = "x-boatrace:"


class ParseError(Exception):
    pass


class FetchError(Exception):
    pass


def race_url(date, jcd, rno):
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError("date must be YYYYMMDD")
    if not re.fullmatch(r"(0[1-9]|1\d|2[0-4])", jcd):
        raise ValueError("jcd must be a two-digit venue code 01-24")
    if not 1 <= int(rno) <= 12:
        raise ValueError("rno must be 1-12")
    return f"{BASE_URL}?rno={int(rno)}&jcd={jcd}&hd={date}"


def fetch(url, retries=2, interval=3.0, timeout=20, opener=urllib.request.urlopen, sleep=time.sleep):
    """Polite GET: descriptive UA, timeout, few retries. Never tries to bypass blocking."""
    last = None
    for attempt in range(retries + 1):
        if attempt:
            sleep(interval * attempt)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
        try:
            with opener(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if re.search(r"captcha|access denied", body[:5000], re.I):
                raise FetchError("access restriction page returned; not bypassing")
            return body
        except urllib.error.HTTPError as e:
            last = FetchError(f"HTTP {e.code} for {url}")
            if e.code in (401, 403, 429):
                raise last
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = FetchError(f"request failed for {url}: {e}")
    raise last


class _Table(HTMLParser):
    """Collects table rows and cells from each <tbody class~=is-fs12>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bodies = []
        self._body = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag == "tbody" and "is-fs12" in cls.split():
            self._body = []
        elif self._body is not None and tag == "tr":
            self._row = []
            self._body.append(self._row)
        elif self._row is not None and tag == "td":
            self._cell = [""]
        elif self._cell is not None and tag in ("br", "div", "p"):
            self._cell.append("")

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            lines = [re.sub(r"\s+", " ", s).strip() for s in self._cell]
            self._row.append([s for s in lines if s])
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "tbody" and self._body is not None:
            self.bodies.append(self._body)
            self._body = None
        elif self._cell is not None and tag in ("div", "p"):
            self._cell.append("")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[-1] += data


def _num(raw, cast=float):
    if raw is None or raw.strip() in MISSING_TOKENS:
        return None
    try:
        return cast(raw.strip())
    except ValueError:
        return None


def _line(cell, i):
    return cell[i] if i < len(cell) else None


def _cell_value(cell):
    value = " ".join(cell).strip()
    return value or None


def _flc(cell):
    f = re.fullmatch(r"F(\d+)", _line(cell, 0) or "")
    l = re.fullmatch(r"L(\d+)", _line(cell, 1) or "")
    return (f and f.group(1)), (l and l.group(1)), _line(cell, 2)


def parse_racelist(html):
    """Return six entries: {boat, fields: {name: (raw|None, normalized|None)}}. Raises ParseError."""
    p = _Table()
    try:
        p.feed(html)
        p.close()
    except Exception as e:  # malformed markup
        raise ParseError(f"HTML could not be parsed: {e}") from e
    if len(p.bodies) != 6:
        raise ParseError(f"expected 6 entry rows (tbody.is-fs12), found {len(p.bodies)}")
    entries = []
    for idx, cells in enumerate(p.bodies, 1):
        if len(cells) != 4:
            raise ParseError(f"entry {idx}: expected 4 rows, found {len(cells)}")
        rows = cells
        cells = rows[0]
        if len(cells) < 8:
            raise ParseError(f"entry {idx}: expected >=8 cells, found {len(cells)}")
        boat_raw = _line(cells[0], 0)
        boat_norm = unicodedata.normalize("NFKC", boat_raw).strip() if boat_raw else boat_raw
        if boat_norm != str(idx):
            raise ParseError(f"entry {idx}: boat number cell is {boat_raw!r}")
        head = re.match(r"(\d{4})\s*/\s*(\S+)$", _line(cells[2], 0) or "")
        if not head:
            raise ParseError(f"entry {idx}: cannot read registration number/class")
        f_raw, l_raw, st_raw = _flc(cells[3])
        f = {}
        f["boat_number"] = (boat_raw, int(boat_norm))
        f["racer_registration_number"] = (head.group(1), head.group(1))
        f["racer_class"] = (head.group(2), head.group(2))
        name = _line(cells[2], 1)
        f["racer_name"] = (name, name)
        f["flying_count"] = (f_raw, _num(f_raw, int))
        f["late_count"] = (l_raw, _num(l_raw, int))
        f["avg_start_timing"] = (st_raw, _num(st_raw))
        for prefix, cell in (("national", cells[4]), ("local", cells[5])):
            for i, suffix in enumerate(("win_rate", "top2_rate", "top3_rate")):
                raw = _line(cell, i)
                f[f"{prefix}_{suffix}"] = (raw, _num(raw))
        for prefix, cell in (("motor", cells[6]), ("boat_equipment", cells[7])):
            raw = _line(cell, 0)
            f[f"{prefix}_number"] = (raw, raw if raw and raw not in MISSING_TOKENS else None)
            for i, suffix in ((1, "top2_rate"), (2, "top3_rate")):
                raw = _line(cell, i)
                f[f"{prefix}_{suffix}"] = (raw, _num(raw))
        performance = []
        if len(cells) > 9 and not _line(cells[8], 0):
            performance_rows = [cells[9:]] + rows[1:]
            slot_count = max(len(row) for row in performance_rows)
            for slot in range(slot_count):
                raw_values = [
                    _cell_value(row[slot]) if slot < len(row) else None
                    for row in performance_rows
                ]
                if not any(raw_values):
                    continue
                values = {}
                for name, raw, cast in zip(
                    ("race_number", "entry_course", "start_timing", "finish_position"),
                    raw_values,
                    (int, int, float, int),
                ):
                    normalized = _num(unicodedata.normalize("NFKC", raw), cast) if raw else None
                    values[name] = (raw, normalized)
                performance.append({"slot": slot + 1, "fields": values})
        entries.append({"boat": idx, "fields": f, "current_series_performance": performance})
    return entries


# fields that exist in the standard vocabulary keep their standard name
_STANDARD = {"motor_top2_rate", "motor_top3_rate"}


def build_evidence(entries, date, jcd, rno, url, observed_at=None):
    observed_at = observed_at or datetime.now(JST).isoformat(timespec="seconds")
    race_id = f"{date}-{jcd}-{int(rno):02d}"
    obs, missing = [], []

    def add_observation(field, boat, raw, value):
        if value is None:
            missing.append({"field": field, "boat": boat,
                            "reason": "blank" if raw is None else f"unparseable raw value {raw!r}"})
            return
        obs.append({"id": f"o{len(obs) + 1}", "field": field, "boat": boat, "value": value,
                    "raw_value": raw, "source_url": url, "observed_at": observed_at, "phase": "pre_race"})

    for e in entries:
        for name, (raw, value) in e["fields"].items():
            field = name if name in _STANDARD else NS + name
            add_observation(field, e["boat"], raw, value)
        for result in e.get("current_series_performance", []):
            for name, (raw, value) in result["fields"].items():
                field = NS + f"current_series_{name}_{result['slot']}"
                add_observation(field, e["boat"], raw, value)
    return {"schema_version": SCHEMA_VERSION, "vocabulary_version": VOCABULARY_VERSION,
            "evidence_id": f"BR-RACELIST-{race_id}", "race_id": race_id, "synthetic": False,
            "observations": obs, "inferences": [], "missing_data": missing}


def validate_evidence(doc):
    from onemark import validate as v
    errors = list(v.schema_errors("evidence", doc))
    if not errors:
        errors += v.evidence_errors(doc)
        boats = {o["boat"] for o in doc["observations"] if "boat" in o} | {m["boat"] for m in doc["missing_data"]}
        if boats != {1, 2, 3, 4, 5, 6}:
            errors.append(f"expected entries for boats 1-6, got {sorted(boats)}")
    return errors


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--jcd", default="02")
    ap.add_argument("--rno", required=True, type=int)
    ap.add_argument("--out-dir", default="out")
    a = ap.parse_args(argv)
    try:
        url = race_url(a.date, a.jcd, a.rno)
        html = fetch(url)
    except (ValueError, FetchError) as e:
        print(f"ERROR retrieval: {e}", file=sys.stderr)
        return 2
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = f"{a.date}-{a.jcd}-{a.rno:02d}"
    (out / f"racelist-{base}.html").write_text(html, encoding="utf-8")
    try:
        doc = build_evidence(parse_racelist(html), a.date, a.jcd, a.rno, url)
    except ParseError as e:
        print(f"ERROR parse: {e}", file=sys.stderr)
        return 3
    (out / f"evidence-{base}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    errors = validate_evidence(doc)
    if errors:
        print("ERROR validation:\n" + "\n".join(errors), file=sys.stderr)
        return 4
    print(f"OK {doc['race_id']}: 6 entries, {len(doc['observations'])} observations, {len(doc['missing_data'])} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
