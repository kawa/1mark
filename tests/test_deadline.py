import json
import zipfile
from pathlib import Path

from onemark import batch as b
from onemark import deadline as dl
from onemark import package as p
from onemark import workflow as wf

HTML = (Path(__file__).parent / "fixtures" / "racelist.20261011.toda.html").read_text(encoding="utf-8")
DATE, JCD, URL, TS = "20261011", "02", "https://example/racelist", "2026-10-11T00:00:00+00:00"
FDATE = "20991011"
FHTML = HTML.replace(DATE, FDATE)  # future date keeps the evidence retrieval time before the deadline
EXPECTED = ["10:47", "11:16", "11:45", "12:14", "12:44", "13:14", "13:45", "14:16", "14:48", "15:21", "15:55", "16:30"]


def parse(html=HTML, date=DATE, jcd=JCD):
    return dl.parse_deadlines(html, date, jcd, URL, TS)


def test_extraction_from_captured_html_all_twelve_with_association():
    s = parse()
    assert [s[n]["raw_time"] for n in range(1, 13)] == EXPECTED
    e = s[3]
    assert (e["race_id"], e["date"], e["jcd"], e["rno"]) == ("20261011-02-03", DATE, JCD, 3)
    assert e["betting_deadline"] == "2026-10-11T11:45:00+09:00" and e["status"] == "available"


def test_provenance_preserved():
    e = parse()[1]
    assert e["source_url"] == URL and e["retrieved_at"] == TS and e["raw_time"] == "10:47"


def test_wrong_date_or_venue_is_unknown_not_invented():
    for s in (parse(date="20261012"), parse(jcd="03")):
        assert all(e["status"] == "unknown" and e["betting_deadline"] is None for e in s.values())


def test_missing_row_and_malformed_time():
    s = parse(HTML.replace("締切予定時刻", "他"))
    assert all(e["status"] == "unknown" and e["reason"] for e in s.values())
    s = parse(HTML.replace(">10:47<", ">--<").replace(">11:16<", ">25:99<"))
    assert s[1]["status"] == s[2]["status"] == "unknown" and s[1]["raw_time"] == "--"
    assert s[3]["status"] == "available"


def test_default_offset_exactly_20_and_configurable():
    e = parse()[1]
    x = dl.estimate_exhibition(e)
    assert x["kind"] == "estimated_from_betting_deadline" and x["offset_minutes"] == 20
    assert x["time"] == "2026-10-11T10:27:00+09:00" and x["betting_deadline"] == e["betting_deadline"]
    assert dl.estimate_exhibition(e, 15)["time"] == "2026-10-11T10:32:00+09:00"
    assert wf.DEFAULT_EXHIBITION_OFFSET_MINUTES == 20


def test_unknown_deadline_gives_unknown_estimate():
    assert dl.estimate_exhibition(parse(HTML.replace(">10:47<", ">--<"))[1]) == {"kind": "unknown"}
    assert dl.estimate_exhibition(None) == {"kind": "unknown"}


def test_package_integration_and_execution_not_approved(tmp_path):
    s = parse()
    ev = tmp_path / "ev.json"
    from onemark import boatrace as br
    ev.write_text(json.dumps(br.build_evidence(br.parse_racelist(HTML), DATE, JCD, 1, URL)), encoding="utf-8")
    # evidence retrieved before the deadline is required; use a far-future deadline-independent check via manifest
    files = p.build_files(str(ev), evidence_cutoff="2026-10-11T09:00:00+09:00", schedule=s[1])
    m = json.loads(files["manifest.json"])
    t = json.loads(files["task.json"])
    assert t["betting_deadline"] == "2026-10-11T10:47:00+09:00" and t["execution_allowed"] is False
    assert m["execution_allowed"] is False and m["betting_deadline"] == t["betting_deadline"]
    sc = m["schedule"]
    assert sc["source_url"] == URL and sc["retrieved_at"] == TS and sc["raw_time"] == "10:47"
    assert sc["estimated_exhibition_check_time"]["time"] == "2026-10-11T10:27:00+09:00"
    assert t["evidence_cutoff"] != t["betting_deadline"]


def test_package_unknown_schedule_and_backward_compat(tmp_path):
    ev = tmp_path / "ev.json"
    from onemark import boatrace as br
    ev.write_text(json.dumps(br.build_evidence(br.parse_racelist(HTML), DATE, JCD, 1, URL)), encoding="utf-8")
    s = parse(HTML.replace(">10:47<", ">--<"))
    m = json.loads(p.build_files(str(ev), schedule=s[1])["manifest.json"])
    assert m["betting_deadline"] == "unknown" and m["schedule"]["estimated_exhibition_check_time"] == {"kind": "unknown"}
    old = json.loads(p.build_files(str(ev))["manifest.json"])
    assert "schedule" not in old and old["betting_deadline"] == "unknown"


def test_batch_single_page_parse_and_zip(tmp_path):
    urls = []

    def fetcher(url):
        urls.append(url)
        return FHTML

    s = b.run_batch(FDATE, JCD, tmp_path, fetcher=fetcher, delay=0, sleep=lambda x: None)
    assert len(urls) == 12  # no extra request for deadlines
    assert s["schedule_status"] == "available" and s["failed"] == []
    assert s["races"][0]["betting_deadline"] == "2099-10-11T10:47:00+09:00"
    sched = json.loads((tmp_path / "schedule.json").read_text())
    assert sched["12"]["raw_time"] == "16:30"
    out = tmp_path / "batch.zip"
    manifest = wf.build_batch_zip(tmp_path, out)
    assert manifest["schedule"]["12"]["betting_deadline"] == "2099-10-11T16:30:00+09:00"
    with zipfile.ZipFile(out) as z:
        assert "schedule.json" in z.namelist()
        text = z.read("START_HERE.md").decode()
    assert "estimated_exhibition_check_time" in text and "NOT a SKIP" in text


def test_batch_missing_schedule_is_not_failure(tmp_path):
    html = FHTML.replace("締切予定時刻", "他")
    s = b.run_batch(FDATE, JCD, tmp_path, fetcher=lambda u: html, delay=0, sleep=lambda x: None)
    assert s["failed"] == [] and s["races"][0]["betting_deadline"] == "unknown"
