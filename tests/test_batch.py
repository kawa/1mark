import json
import zipfile
from datetime import datetime
from pathlib import Path

from onemark import batch as b
from onemark import boatrace as br
from onemark import package as p

HTML = (Path(__file__).parent / "fixtures" / "racelist.20261011.toda.html").read_text(encoding="utf-8")
DATE, JCD = "20261011", "02"


BEFORE_ALL_DEADLINES = lambda: datetime.fromisoformat("2026-10-11T08:00:00+09:00")  # first deadline is 10:47


def run(tmp_path, fetcher):
    return b.run_batch(DATE, JCD, tmp_path, fetcher=fetcher, delay=0, sleep=lambda s: None, now=BEFORE_ALL_DEADLINES)


def test_all_twelve_processed_and_layout(tmp_path):
    urls = []

    def fetcher(url):
        urls.append(url)
        return HTML

    s = run(tmp_path, fetcher)
    assert [u.split("rno=")[1].split("&")[0] for u in urls] == [str(i) for i in range(1, 13)]
    assert s["succeeded"] == list(range(1, 13)) and s["failed"] == []
    for n in range(1, 13):
        d = tmp_path / f"race-{n:02d}"
        base = f"{DATE}-{JCD}-{n:02d}"
        assert (d / f"racelist-{base}.html").is_file()
        assert (d / f"evidence-{base}.json").is_file()
        assert (d / f"prediction-package-{base}.zip").is_file()
    assert json.loads((tmp_path / "batch-summary.json").read_text())["total"] == 12


def test_packages_independently_valid(tmp_path):
    run(tmp_path, lambda url: HTML)
    for n in (1, 12):
        z = tmp_path / f"race-{n:02d}" / f"prediction-package-{DATE}-{JCD}-{n:02d}.zip"
        assert p.validate_zip(z) == []
        ev = json.loads(zipfile.ZipFile(z).read("evidence.json"))
        assert ev["race_id"].endswith(f"{n:02d}")


def test_partial_failures_isolated(tmp_path):
    def fetcher(url):
        rno = int(url.split("rno=")[1].split("&")[0])
        if rno == 3:
            raise br.FetchError("HTTP 403")
        if rno == 7:
            return "<html>garbage</html>"
        return HTML

    s = run(tmp_path, fetcher)
    assert s["failed"] == [3, 7]
    assert s["succeeded"] == [1, 2, 4, 5, 6, 8, 9, 10, 11, 12]
    r3, r7 = s["races"][2], s["races"][6]
    assert r3["failure_stage"] == "retrieval" and "403" in r3["error"] and r3["failed_at"]
    assert r7["failure_stage"] == "parse" and r7["error"]
    assert (tmp_path / "race-03").exists() is False
    assert (tmp_path / "race-12" / f"prediction-package-{DATE}-{JCD}-12.zip").is_file()


def test_unexpected_exception_isolated(tmp_path):
    def fetcher(url):
        raise RuntimeError("boom")

    s = run(tmp_path, fetcher)
    assert len(s["failed"]) == 12 and "boom" in s["races"][0]["error"]


def test_cli_exit_codes(tmp_path, monkeypatch):
    assert b.main(["--date", "bad", "--jcd", JCD, "--out-dir", str(tmp_path)]) == 2
