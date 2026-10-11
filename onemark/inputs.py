"""Workflow input helpers: venue name -> JCD code, optional race date (default: today in JST)."""
import argparse
import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

VENUES = {
    "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04", "多摩川": "05", "浜名湖": "06",
    "蒲郡": "07", "常滑": "08", "津": "09", "三国": "10", "びわこ": "11", "住之江": "12",
    "尼崎": "13", "鳴門": "14", "丸亀": "15", "児島": "16", "宮島": "17", "徳山": "18",
    "下関": "19", "若松": "20", "芦屋": "21", "福岡": "22", "唐津": "23", "大村": "24",
}


def venue_code(name):
    name = (name or "").strip()
    if name in VENUES:
        return VENUES[name]
    raise ValueError(f"unknown venue: {name!r}")


def resolve_date(value, now=None):
    value = (value or "").strip()
    if not value:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return now.astimezone(JST).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("date must be YYYYMMDD or empty")
    datetime.strptime(value, "%Y%m%d")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--venue", required=True)
    a = ap.parse_args(argv)
    print(f"date={resolve_date(a.date)}")
    print(f"jcd={venue_code(a.venue)}")


if __name__ == "__main__":
    main()
