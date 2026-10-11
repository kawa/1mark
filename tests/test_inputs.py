from datetime import datetime, timezone

import pytest

from onemark import inputs as i


def test_all_24_venues_map_to_unique_codes():
    assert sorted(i.VENUES.values()) == [f"{n:02d}" for n in range(1, 25)]
    assert i.venue_code("戸田") == "02" and i.venue_code("大村") == "24" and i.venue_code("住之江") == "12"


def test_unknown_venue():
    with pytest.raises(ValueError):
        i.venue_code("02")


def test_empty_date_uses_jst_across_utc_boundary():
    assert i.resolve_date("", datetime(2026, 1, 1, 14, 59, tzinfo=timezone.utc)) == "20260101"
    assert i.resolve_date("", datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)) == "20260102"
    assert i.resolve_date("  ", datetime(2025, 12, 31, 16, 0, tzinfo=timezone.utc)) == "20260101"


def test_explicit_date_and_invalid():
    assert i.resolve_date("20260315") == "20260315"
    for bad in ("2026-03-15", "20261301", "abc"):
        with pytest.raises(ValueError):
            i.resolve_date(bad)
