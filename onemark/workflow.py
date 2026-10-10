"""User-controlled PRE / WAIT workflow: contracts' deterministic rules and batch ZIP assembly.

Workflow only: no prediction logic, no scraping, no AI calls, no betting. The PRE baseline stays immutable;
user workflow state and exhibition revisions are separate documents that reference it by id + hash.
"""
import argparse
import json
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

from onemark import package as pkg
from onemark import validate as v

# Documented default: exhibition viewing is assumed this many minutes before the betting deadline.
# It is only an estimate (labelled estimated_from_betting_deadline) and can be overridden per call/CLI.
DEFAULT_EXHIBITION_OFFSET_MINUTES = 10

TRANSITIONS = {
    "UNDECIDED": {"PRE_PURCHASED", "WAIT_SELECTED"},
    "WAIT_SELECTED": {"WAIT_SWITCHED_TO_PRE", "FINAL_PURCHASED", "FINAL_NOT_PURCHASED"},
}
BATCH_FILES = ("START_HERE.md", "manifest.json", "workflow_state.schema.json", "exhibition_revision.schema.json",
               "batch_summary.schema.json", "prediction.schema.json", "_defs.schema.json")

BATCH_START_HERE = """# START HERE (batch of 12 independent Prediction Packages)

You are a stateless AI session in ONE conversation. Do not rely on prior conversations.

## Stage 1: PRE predictions for all 12 races
1. Discover packages: every `race-NN/*.zip` listed in `manifest.json` (`races`). Each is independent; open each one
   and follow its own `START_HERE.md` / `protocol.md` exactly. This file adds no prediction rules.
2. Analyze EVERY race using only its package. Never require exhibition data for the initial prediction.
3. Produce one prediction JSON per race (PRE or SKIP; combinations, units, reasoning), validated against `prediction.schema.json`.
4. Produce one `batch_summary` JSON (`batch_summary.schema.json`): per race the status (PRE/SKIP), combinations, units,
   `exhibition_time` and `data_limitations`. Exhibition time: `official` only if an official schedule is in the package;
   `estimated_from_betting_deadline` (with `offset_minutes`, clearly labelled estimate) if only a betting deadline is known;
   otherwise `unknown`. Never invent a time and never present an estimate as official. Do not ask the user for per-race times.
5. A race you could not process is `processing: FAILED` with `failure.stage` and `failure.error`. Never report it as SKIP,
   and do not guess a status. Report these races explicitly in the text summary too.
6. Show the user a concise table of the summary. Prediction units are independent of bankroll.

## Stage 2: user decides
The user buys PRE predictions they want now and chooses which others to hold (WAIT) for exhibition.
WAIT is a user selection, not a prediction status. Record it in a `workflow_state` JSON; never edit the original PRE prediction.
The user may switch WAIT back to the original PRE if exhibition cannot be viewed (state `WAIT_SWITCHED_TO_PRE`).
Purchasing is the user's action; you never place bets.

## Stage 3: exhibition follow-up (same conversation)
When the user posts exhibition results:
1. Identify the race. If ambiguous, ask (outcome `NEEDS_CLARIFICATION`, `candidate_race_ids`).
2. Parse entry course, exhibition time, exhibition ST, weather and other observations as given; keep the original text in `raw`
   and the `source`. Never invent missing items; list them in `missing_observations`.
3. Compare against the locked PRE baseline, reassess with the same protocol principles (no arbitrary exhibition thresholds;
   exhibition ST is not a deterministic predictor of race ST).
4. Produce an `exhibition_revision` JSON (`exhibition_revision.schema.json`) referencing the baseline `prediction_id` and `prediction_sha256`:
   `REVISED` (new prediction, PRE or SKIP, with a new prediction_id) or `RETAINED` (explain why unchanged).
   Report changes in combinations, units and reasoning, and explain unchanged parts too.
5. The user decides whether to purchase the final prediction.

Validate each file: `python -m onemark.validate <file>` (names start with `prediction`, `workflow_state`, `exhibition_revision`, `batch_summary`).
"""


def exhibition_time(official=None, betting_deadline=None, offset_minutes=DEFAULT_EXHIBITION_OFFSET_MINUTES):
    """Return an `exhibition_time` object. Official wins; else deadline-offset estimate (labelled); else unknown."""
    if official:
        return {"kind": "official", "time": official, "source": "official schedule"}
    if betting_deadline:
        t = v.parse_ts(betting_deadline) - timedelta(minutes=offset_minutes)
        return {"kind": "estimated_from_betting_deadline", "time": t.isoformat(), "betting_deadline": betting_deadline,
                "offset_minutes": offset_minutes, "source": "estimate: betting deadline minus offset"}
    return {"kind": "unknown"}


def _bets(pred):
    return {v._key(b): b["units"] for b in pred.get("bets", [])}


def diff_bets(old, new):
    """Changes from baseline bets to final bets (both {key: units}), in schema form."""
    row = lambda k: {"bet_type": k[0], "combination": list(k[1])}
    return {
        "added": [{**row(k), "units": n} for k, n in sorted(new.items()) if k not in old],
        "removed": [row(k) for k in sorted(old) if k not in new],
        "units_changed": [{**row(k), "from_units": old[k], "to_units": new[k]}
                          for k in sorted(old) if k in new and old[k] != new[k]],
    }


def workflow_state_errors(doc, baseline_pred=None):
    errors = []
    if baseline_pred is not None:
        errors += _baseline_errors(doc["baseline"], baseline_pred, doc["race_id"])
    hist = doc["history"]
    if hist[0]["state"] != "UNDECIDED":
        errors.append("history must start with UNDECIDED")
    for a, b in zip(hist, hist[1:]):
        if b["state"] not in TRANSITIONS.get(a["state"], ()):
            errors.append(f"invalid transition {a['state']} -> {b['state']}")
        elif v.parse_ts(b["at"]) < v.parse_ts(a["at"]):
            errors.append("history timestamps must not decrease")
    for h in hist:
        if h["state"].startswith("FINAL_") and "revision_id" not in h:
            errors.append(f"{h['state']} requires revision_id")
    if doc["state"] != hist[-1]["state"]:
        errors.append("state must equal the last history entry")
    return errors


def _baseline_errors(ref, pred, race_id):
    errors = []
    if pred["status"] != "PRE":
        errors.append("baseline prediction must be PRE")
    if pred["prediction_id"] != ref["prediction_id"]:
        errors.append("baseline prediction_id mismatch")
    if pred["race_id"] != race_id:
        errors.append("baseline race_id mismatch")
    if v.canonical_sha256(pred) != ref["prediction_sha256"]:
        errors.append("baseline prediction_sha256 mismatch (baseline modified?)")
    return errors


def revision_errors(rev, baseline_pred=None):
    errors, outcome = [], rev["outcome"]
    if outcome == "NEEDS_CLARIFICATION":
        for k in ("prediction", "changes", "baseline"):
            if k in rev:
                errors.append(f"NEEDS_CLARIFICATION must not contain {k}")
        if not rev.get("candidate_race_ids") and "race_id" not in rev:
            errors.append("NEEDS_CLARIFICATION requires candidate_race_ids or race_id")
        return errors
    for k in ("race_id", "baseline", "changes"):
        if k not in rev:
            errors.append(f"{outcome} requires {k}")
    if not rev.get("exhibition_observations"):
        errors.append(f"{outcome} requires at least one exhibition observation (never invent data)")
    if errors:
        return errors
    for o in rev["exhibition_observations"]:
        errors += v.field_errors(o["field"], o.get("boat"), o["value"], check_value=True)
    if baseline_pred is not None:
        errors += _baseline_errors(rev["baseline"], baseline_pred, rev["race_id"])
    old = _bets(baseline_pred) if baseline_pred is not None else None
    if outcome == "RETAINED":
        if "prediction" in rev:
            errors.append("RETAINED must not contain a prediction")
        new = old
    else:
        pred = rev.get("prediction")
        if pred is None:
            return errors + ["REVISED requires prediction"]
        errors += [f"prediction: {e}" for e in v.schema_errors("prediction", pred)] or \
                  [f"prediction: {e}" for e in v.prediction_errors(pred)]
        if pred["race_id"] != rev["race_id"]:
            errors.append("revised prediction race_id mismatch")
        if pred["status"] not in ("PRE", "SKIP"):
            errors.append("revised prediction status must be PRE or SKIP")
        if pred["prediction_id"] == rev["baseline"]["prediction_id"]:
            errors.append("revised prediction must have a new prediction_id")
        new = _bets(pred)
    if old is not None and new is not None:
        if diff_bets(old, new) != rev["changes"]:
            errors.append("changes do not match the difference between baseline and final bets")
        if outcome == "REVISED" and not any(rev["changes"].values()) and "unchanged" not in rev["explanation"]:
            errors.append("REVISED without bet changes must explain what is unchanged")
    elif outcome == "RETAINED" and any(rev["changes"].values()):
        errors.append("RETAINED must have no changes")
    return errors


def batch_summary_errors(doc, predictions=None):
    """predictions: optional {race_id: prediction doc} to cross-check status, hash and bets."""
    errors, ids = [], [r["race_id"] for r in doc["races"]]
    if len(set(ids)) != 12 or len({i[:-3] for i in ids}) != 1 or sorted(i[-2:] for i in ids) != [f"{n:02d}" for n in range(1, 13)]:
        errors.append("races must be exactly race 01-12 of one date/venue")
    for r in doc["races"]:
        rid, et = r["race_id"], r["exhibition_time"]
        if r["processing"] == "FAILED":
            if "failure" not in r:
                errors.append(f"{rid}: FAILED requires failure")
            for k in ("status", "prediction_id", "bets"):
                if k in r:
                    errors.append(f"{rid}: FAILED must not carry {k} (failure is not SKIP)")
        else:
            if "status" not in r:
                errors.append(f"{rid}: PROCESSED requires status")
            if "failure" in r:
                errors.append(f"{rid}: PROCESSED must not carry failure")
            if r.get("status") == "PRE" and not r.get("bets"):
                errors.append(f"{rid}: PRE requires bets")
            if r.get("status") != "PRE" and r.get("bets"):
                errors.append(f"{rid}: only PRE may carry bets")
            if "bets" in r:
                errors += [f"{rid}: {e}" for e in v.bets_errors(r["bets"])]
                if r.get("total_units") != sum(b["units"] for b in r["bets"]):
                    errors.append(f"{rid}: total_units does not equal the sum of bet units")
            if r.get("status") in ("PRE", "SKIP") and "prediction_id" not in r:
                errors.append(f"{rid}: prediction_id required")
        kind = et["kind"]
        if kind == "unknown" and set(et) != {"kind"}:
            errors.append(f"{rid}: unknown exhibition time must carry no time")
        if kind != "unknown" and "time" not in et:
            errors.append(f"{rid}: exhibition time required for {kind}")
        if kind == "estimated_from_betting_deadline":
            if "betting_deadline" not in et or "offset_minutes" not in et:
                errors.append(f"{rid}: estimate requires betting_deadline and offset_minutes")
            elif v.parse_ts(et["time"]) != v.parse_ts(et["betting_deadline"]) - timedelta(minutes=et["offset_minutes"]):
                errors.append(f"{rid}: estimate is not betting_deadline minus offset_minutes")
        if predictions and r["processing"] == "PROCESSED" and rid in predictions:
            p = predictions[rid]
            if (p["status"], p["prediction_id"], v.canonical_sha256(p)) != (
                    r.get("status"), r.get("prediction_id"), r.get("prediction_sha256")):
                errors.append(f"{rid}: summary does not match prediction")
            elif r.get("status") == "PRE" and _bets(p) != \
                    {v._key(b): b["units"] for b in r["bets"]}:
                errors.append(f"{rid}: summary bets differ from prediction")
    return errors


def build_batch_zip(batch_dir, out, batch_id=None):
    """Wrap the independent race-NN package ZIPs from onemark.batch into one batch ZIP. Fails on missing races."""
    batch_dir = Path(batch_dir)
    packages = {}
    for rno in range(1, 13):
        found = sorted((batch_dir / f"race-{rno:02d}").glob("prediction-package-*.zip"))
        if len(found) > 1:
            raise ValueError(f"race-{rno:02d} has multiple package ZIPs; keep exactly one")
        if found:
            packages[rno] = found[0]
    files = {f"race-{n:02d}/{p.name}": p.read_bytes() for n, p in packages.items()}
    for name, src in (("workflow_state.schema.json", v.SCHEMA_DIR), ("exhibition_revision.schema.json", v.SCHEMA_DIR),
                      ("batch_summary.schema.json", v.SCHEMA_DIR), ("prediction.schema.json", v.SCHEMA_DIR),
                      ("_defs.schema.json", v.SCHEMA_DIR)):
        files[name] = (src / name).read_bytes()
    files["START_HERE.md"] = BATCH_START_HERE.encode("utf-8")
    manifest = {
        "batch_id": batch_id or batch_dir.name,
        "races": {f"{n:02d}": f"race-{n:02d}/{p.name}" for n, p in packages.items()},
        "missing_races": [f"{n:02d}" for n in range(1, 13) if n not in packages],
        "exhibition_offset_minutes_default": DEFAULT_EXHIBITION_OFFSET_MINUTES,
        "files": {k: {"sha256": pkg.sha256(d), "bytes": len(d)} for k, d in sorted(files.items())},
    }
    files["manifest.json"] = pkg._json(manifest)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            z.writestr(zipfile.ZipInfo(name, pkg.ZIP_EPOCH), data)
    return manifest


def check_revision(baseline, revision, state=None):
    """End-to-end check of a locked PRE baseline against an exhibition revision (and optional workflow state)."""
    errors = [f"baseline: {e}" for e in v.schema_errors("prediction", baseline)] or \
             [f"baseline: {e}" for e in v.prediction_errors(baseline)]
    errors += [f"revision: {e}" for e in v.schema_errors("exhibition_revision", revision)]
    if errors:
        return errors
    errors += [f"revision: {e}" for e in revision_errors(revision, baseline)]
    if state is not None:
        errors += [f"state: {e}" for e in v.schema_errors("workflow_state", state)]
        if not errors:
            errors += [f"state: {e}" for e in workflow_state_errors(state, baseline)]
            if state["baseline"] != revision.get("baseline"):
                errors.append("state: baseline differs from revision baseline")
            finals = [h for h in state["history"] if h["state"].startswith("FINAL_")]
            if not finals:
                errors.append("state: no FINAL_* state recorded for this revision")
            elif finals[-1].get("revision_id") != revision["revision_id"]:
                errors.append("state: FINAL_* revision_id does not match the revision")
            if not any(h["state"] == "WAIT_SELECTED" for h in state["history"]):
                errors.append("state: a revision requires a prior WAIT_SELECTED")
    return errors


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check-revision":
        ap = argparse.ArgumentParser(prog="onemark.workflow check-revision",
                                     description="Validate an exhibition revision against its locked PRE baseline.")
        ap.add_argument("--baseline", required=True, help="original locked PRE prediction JSON")
        ap.add_argument("--revision", required=True, help="exhibition_revision JSON")
        ap.add_argument("--state", help="optional workflow_state JSON")
        a = ap.parse_args(argv[1:])
        try:
            load = lambda f: json.loads(Path(f).read_text())
            errors = check_revision(load(a.baseline), load(a.revision), load(a.state) if a.state else None)
        except (OSError, ValueError) as e:
            errors = [f"cannot read input: {e}"]
        for e in errors:
            print(e)
        print("OK" if not errors else f"{len(errors)} error(s)")
        return 1 if errors else 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batch_dir")
    ap.add_argument("--out", default="batch-package.zip")
    a = ap.parse_args(argv)
    m = build_batch_zip(a.batch_dir, a.out)
    if m["missing_races"]:
        print(f"WARNING races without a package (report as not processed, not SKIP): {m['missing_races']}", file=sys.stderr)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
