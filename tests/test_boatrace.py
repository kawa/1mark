import urllib.error
from pathlib import Path

import pytest

from onemark import boatrace as b

HTML = (Path(__file__).parent / "fixtures" / "racelist.synthetic.html").read_text(encoding="utf-8")
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
    assert f["recent_results"][0] == "1 2 3"


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
