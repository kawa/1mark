import urllib.error
from pathlib import Path

import pytest

from onemark import boatrace as b

HTML = (Path(__file__).parent / "fixtures" / "racelist.synthetic.html").read_text(encoding="utf-8")
OFFICIAL_HTML = (Path(__file__).parent / "fixtures" / "racelist.20261011.toda.html").read_text(encoding="utf-8")
URL = b.race_url("20260101", "02", 1)


def entries():
    return b.parse_racelist(HTML)


def test_url_validation():
    assert URL.endswith("rno=1&jcd=02&hd=20260101")
    for args in (("2026-01-01", "02", 1), ("20260101", "99", 1), ("20260101", "02", 13)):
        with pytest.raises(ValueError):
            b.race_url(*args)


def test_six_entries_and_fields():
    es = entries()
    assert [e["boat"] for e in es] == [1, 2, 3, 4, 5, 6]
    f = es[0]["fields"]
    assert f["racer_registration_number"] == ("9001", "9001")
    assert f["racer_class"][1] == "A1"
    assert f["national_win_rate"] == ("6.01", 6.01)
    assert f["motor_number"][1] == "11" and f["motor_top2_rate"][1] == 31.0
    assert "recent_results" not in f


def test_zero_is_not_missing_but_dash_is():
    es = entries()
    assert es[4]["fields"]["national_win_rate"] == ("0.00", 0.0)
    assert es[5]["fields"]["local_win_rate"] == ("-", None)


def test_evidence_valid_and_missing_reported():
    doc = b.build_evidence(entries(), "20260101", "02", 1, URL, "2026-01-01T10:00:00+09:00")
    assert b.validate_evidence(doc) == []
    assert doc["race_id"] == "20260101-02-01" and doc["synthetic"] is False
    assert {"field": "x-boatrace:local_win_rate", "boat": 6, "reason": "unparseable raw value '-'"} in doc["missing_data"]
    zero = [o for o in doc["observations"] if o["boat"] == 5 and o["field"] == "x-boatrace:national_win_rate"][0]
    assert zero["value"] == 0.0 and zero["raw_value"] == "0.00"


def test_validation_rejects_bad_timestamp():
    doc = b.build_evidence(entries(), "20260101", "02", 1, URL, "2026-01-01T10:00:00")
    assert b.validate_evidence(doc)


@pytest.mark.parametrize("html", ["", "<html><body><p>hi", "<tbody class='is-fs12'><tr><td>1"])
def test_malformed_or_unexpected_structure(html):
    with pytest.raises(b.ParseError):
        b.parse_racelist(html)


def test_five_entries_rejected():
    with pytest.raises(b.ParseError):
        b.parse_racelist(HTML.replace("</tbody>", "", 1).replace('<tbody class="is-fs12">', "<div>", 1))


def test_wrong_boat_number_rejected():
    with pytest.raises(b.ParseError):
        b.parse_racelist(HTML.replace('rowspan="4">1</td>', 'rowspan="4">9</td>', 1))


def test_fetch_retries_then_fails_without_network():
    calls = []

    def opener(req, timeout):
        calls.append(req.get_header("User-agent"))
        raise urllib.error.URLError("down")

    with pytest.raises(b.FetchError):
        b.fetch(URL, retries=2, opener=opener, sleep=lambda s: None)
    assert len(calls) == 3 and "1mark" in calls[0]


@pytest.mark.parametrize("n", range(1, 7))
def test_fullwidth_boat_number_normalized(n):
    full = chr(0xFF10 + n)
    html = HTML.replace(f'rowspan="4">{n}</td>', f'rowspan="4">{full}</td>', 1)
    assert full in html
    entry = b.parse_racelist(html)[n - 1]
    assert entry["boat"] == n
    assert entry["fields"]["boat_number"] == (full, n)


def test_official_current_series_rows_stay_aligned_for_all_boats():
    es = b.parse_racelist(OFFICIAL_HTML)
    expected_first_races = [
        ("8", "2", ".09", "４"),
        ("4", "5", ".16", "４"),
        ("1", "2", ".25", "２"),
        ("2", "5", ".19", "２"),
        ("6", "6", ".15", "２"),
        ("4", "1", ".25", "２"),
    ]
    assert len(es) == 6
    for entry, expected in zip(es, expected_first_races):
        first = entry["current_series_performance"][0]
        assert first["slot"] == 1
        assert tuple(first["fields"][key][0] for key in (
            "race_number", "entry_course", "start_timing", "finish_position"
        )) == expected

    boat_one = es[0]["current_series_performance"]
    assert [item["slot"] for item in boat_one] == [1, 3, 4]
    assert [item["fields"]["race_number"][1] for item in boat_one] == [8, 2, 7]
    assert [item["fields"]["finish_position"][1] for item in boat_one] == [4, 5, 5]
    assert all(
        item["fields"]["race_number"][1] != item["fields"]["finish_position"][1]
        for item in boat_one
    )

    observed_at = "2026-10-10T22:22:41+09:00"
    doc = b.build_evidence(es, "20261011", "02", 1, b.race_url("20261011", "02", 1), observed_at)
    assert b.validate_evidence(doc) == []
    race = next(o for o in doc["observations"] if
                o["boat"] == 1 and o["field"] == "x-boatrace:current_series_race_number_1")
    finish = next(o for o in doc["observations"] if
                  o["boat"] == 1 and o["field"] == "x-boatrace:current_series_finish_position_1")
    assert (race["value"], race["raw_value"]) == (8, "8")
    assert (finish["value"], finish["raw_value"]) == (4, "４")
    assert race["source_url"] == finish["source_url"]
    assert race["observed_at"] == finish["observed_at"] == observed_at


def test_official_current_series_missing_and_irregular_values_are_not_invented():
    html = OFFICIAL_HTML.replace(
        '<td class=" ">2</td>', '<td class=" ">&nbsp;</td>', 1
    ).replace(">４</a></td>", ">転</a></td>", 1)
    es = b.parse_racelist(html)
    first = es[0]["current_series_performance"][0]["fields"]
    assert first["entry_course"] == (None, None)
    assert first["finish_position"] == ("転", None)

    doc = b.build_evidence(es, "20261011", "02", 1, b.race_url("20261011", "02", 1),
                           "2026-10-10T22:22:41+09:00")
    assert b.validate_evidence(doc) == []
    assert {"field": "x-boatrace:current_series_entry_course_1", "boat": 1,
            "reason": "blank"} in doc["missing_data"]
    assert {"field": "x-boatrace:current_series_finish_position_1", "boat": 1,
            "reason": "unparseable raw value '転'"} in doc["missing_data"]
