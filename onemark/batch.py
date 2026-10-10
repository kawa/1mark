"""Batch BOAT RACE ingestion: races 1-12 of one venue/date, each processed independently.

Reuses onemark.boatrace (fetch/parse/validate) and onemark.package (Prediction Package).
Layout: <out>/race-NN/{racelist-..html, evidence-..json, prediction-package-..zip}; <out>/batch-summary.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from onemark import boatrace as br
from onemark import deadline as dl
from onemark import package as pkg

RACES = range(1, 13)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StageError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def process_race(date, jcd, rno, out_dir, fetcher=br.fetch, schedule=None):
    """Run one race through all stages; returns result dict. Never raises."""
    base = f"{date}-{jcd}-{rno:02d}"
    race_dir = Path(out_dir) / f"race-{rno:02d}"
    result = {"rno": rno, "race_dir": race_dir.name, "status": "success"}
    stage = "retrieval"
    try:
        try:
            url = br.race_url(date, jcd, rno)
            html = fetcher(url)
        except (ValueError, br.FetchError) as e:
            raise StageError("retrieval", str(e))
        if schedule is not None and not schedule:
            # one official racelist page lists all 12 deadlines: parse it once, no extra request
            schedule.update(dl.parse_deadlines(html, date, jcd, url, _now()))
        race_dir.mkdir(parents=True, exist_ok=True)
        html_path = race_dir / f"racelist-{base}.html"
        html_path.write_text(html, encoding="utf-8")
        result["html"] = f"{race_dir.name}/{html_path.name}"
        try:
            doc = br.build_evidence(br.parse_racelist(html), date, jcd, rno, url)
        except br.ParseError as e:
            raise StageError("parse", str(e))
        ev_path = race_dir / f"evidence-{base}.json"
        ev_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["evidence"] = f"{race_dir.name}/{ev_path.name}"
        errors = br.validate_evidence(doc)
        if errors:
            raise StageError("validation", "; ".join(errors))
        zip_path = race_dir / f"prediction-package-{base}.zip"
        try:
            pkg.write_zip(pkg.build_files(str(ev_path), schedule=(schedule or {}).get(rno)), zip_path)
        except pkg.PackageError as e:
            raise StageError("package", str(e))
        problems = pkg.validate_zip(zip_path)
        if problems:
            raise StageError("package_validation", "; ".join(problems))
        result["package"] = f"{race_dir.name}/{zip_path.name}"
        if schedule:
            result["betting_deadline"] = schedule[rno]["betting_deadline"] or "unknown"
    except StageError as e:
        result.update(status="failed", failure_stage=e.stage, error=str(e), failed_at=_now())
    except Exception as e:  # keep races independent
        result.update(status="failed", failure_stage=stage, error=f"{type(e).__name__}: {e}", failed_at=_now())
    return result


def run_batch(date, jcd, out_dir, fetcher=br.fetch, delay=3.0, sleep=time.sleep):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    schedule = {}
    for rno in RACES:
        if rno != RACES[0]:
            sleep(delay)
        results.append(process_race(date, jcd, rno, out, fetcher, schedule))
    if schedule:
        (out / "schedule.json").write_text(json.dumps({f"{k:02d}": v for k, v in sorted(schedule.items())},
                                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "date": date, "jcd": jcd, "generated_at": _now(),
        "total": len(results),
        "succeeded": [r["rno"] for r in results if r["status"] == "success"],
        "failed": [r["rno"] for r in results if r["status"] == "failed"],
        "schedule_status": "available" if schedule else "unknown",
        "races": results,
    }
    (out / "batch-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--jcd", required=True)
    ap.add_argument("--out-dir", default="batch-out")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between races")
    a = ap.parse_args(argv)
    try:
        br.race_url(a.date, a.jcd, 1)
    except ValueError as e:
        print(f"ERROR input: {e}", file=sys.stderr)
        return 2
    s = run_batch(a.date, a.jcd, a.out_dir, delay=a.delay)
    for r in s["races"]:
        if r["status"] == "failed":
            print(f"FAILED race {r['rno']} [{r['failure_stage']}]: {r['error']}", file=sys.stderr)
    print(f"batch {a.date} {a.jcd}: {len(s['succeeded'])} succeeded, {len(s['failed'])} failed")
    return 1 if s["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
