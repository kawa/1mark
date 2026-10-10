"""Deterministic validation for 1mark documents.

JSON Schema covers structure; the functions here cover rules a schema cannot
express (sorted trios, cross-document consistency, pre-race integrity, hashing).
No network access, no AI calls.
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
KINDS = ("task", "evidence", "prediction", "risk_decision", "review", "learning_proposal")


def _load_schemas():
    return {p.name.split(".")[0]: json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")}


SCHEMAS = _load_schemas()


def _validator(kind):
    schema = SCHEMAS[kind]
    try:
        from referencing import Registry, Resource

        registry = Registry().with_resources(
            (s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values()
        )
        # relative refs such as "_defs.schema.json#/..." resolve against $id
        return Draft202012Validator(schema, registry=registry)
    except ImportError:
        from jsonschema import RefResolver

        store = {s["$id"]: s for s in SCHEMAS.values()}
        return Draft202012Validator(schema, resolver=RefResolver.from_schema(schema, store=store))


def schema_errors(kind, doc):
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in _validator(kind).iter_errors(doc)]


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_sha256(doc):
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def combination_errors(bet_type, combination):
    if len(set(combination)) != 3 or not all(isinstance(b, int) and 1 <= b <= 6 for b in combination):
        return [f"invalid combination {combination}"]
    if bet_type == "trio" and list(combination) != sorted(combination):
        return [f"trio combination must be ascending: {combination}"]
    return []


def bets_errors(bets):
    errors, seen = [], set()
    for bet in bets:
        errors += combination_errors(bet["bet_type"], bet["combination"])
        key = (bet["bet_type"], tuple(bet["combination"]))
        if key in seen:
            errors.append(f"duplicate bet {key}")
        seen.add(key)
    return errors


def prediction_errors(pred):
    errors = bets_errors(pred.get("bets", []))
    for step in pred.get("wait_plan", []):
        errors += bets_errors(step["expected_change"].get("add_bets", []))
        errors += bets_errors(step["expected_change"].get("remove_bets", []))
    return errors


def trio_of(finish_order):
    return sorted(finish_order)


def bet_hit(bet, finish_order):
    if bet["bet_type"] == "trifecta":
        return list(bet["combination"]) == list(finish_order)
    return sorted(bet["combination"]) == trio_of(finish_order)


def evidence_errors(evidence):
    errors = []
    ids = [o["id"] for o in evidence["observations"]] + [i["id"] for i in evidence["inferences"]]
    if len(ids) != len(set(ids)):
        errors.append("duplicate evidence ids")
    obs_ids = {o["id"] for o in evidence["observations"]}
    for inf in evidence["inferences"]:
        for ref in inf["based_on"]:
            if ref not in obs_ids:
                errors.append(f"inference {inf['id']} must be based on observations, got {ref}")
    return errors


def prediction_integrity_errors(task, evidence, pred):
    """Cross-document rules, including post-race leakage prevention."""
    errors = []
    if pred["task_id"] != task["task_id"] or pred["race_id"] != task["race_id"]:
        errors.append("prediction does not match task")
    if evidence["race_id"] != task["race_id"] or pred["evidence_id"] != evidence["evidence_id"]:
        errors.append("evidence does not match task/prediction")
    cutoff = parse_ts(task["prediction_cutoff"])
    if cutoff > parse_ts(task["scheduled_start"]):
        errors.append("prediction_cutoff is after scheduled_start")
    if parse_ts(pred["locked_at"]) > cutoff:
        errors.append("prediction locked after prediction_cutoff")
    obs = {o["id"]: o for o in evidence["observations"]}
    for o in obs.values():
        if o["phase"] == "post_race" or parse_ts(o["observed_at"]) > cutoff:
            errors.append(f"post-cutoff evidence leaked into pre-race bundle: {o['id']}")
    known = set(obs) | {i["id"] for i in evidence["inferences"]}
    cited = [e for a in pred.get("boat_assessments", []) for e in a["evidence_ids"]]
    cited += pred.get("skip_analysis", {}).get("evidence_ids", [])
    errors += [f"unknown evidence id {e}" for e in cited if e not in known]
    return errors


def risk_errors(risk, pred):
    errors = []
    if risk["prediction_id"] != pred["prediction_id"] or risk["prediction_sha256"] != canonical_sha256(pred):
        errors.append("risk decision does not match locked prediction")
    if pred["status"] != "PRE":
        errors.append("risk decisions apply only to PRE predictions")
        return errors
    allowed = {(b["bet_type"], tuple(b["combination"])): b["units"] for b in pred["bets"]}
    for ex in risk.get("executions", []):
        key = (ex["bet_type"], tuple(ex["combination"]))
        if key not in allowed:
            errors.append(f"execution {key} not in prediction")
        elif ex["units"] > allowed[key]:
            errors.append(f"execution {key} exceeds predicted units")
        if ex["stake_jpy"] != ex["units"] * risk["unit_value_jpy"]:
            errors.append(f"execution {key} stake_jpy != units * unit_value_jpy")
    return errors


def review_errors(review, pred):
    errors = []
    if review["prediction_sha256"] != canonical_sha256(pred) or review["prediction_id"] != pred["prediction_id"]:
        errors.append("review does not reference the locked prediction")
    order = review["result"]["finish_order"]
    expected = [
        {"bet_type": b["bet_type"], "combination": b["combination"], "hit": bet_hit(b, order)}
        for b in pred.get("bets", [])
    ]
    if review["outcome"]["bet_results"] != expected:
        errors.append("outcome.bet_results does not match locked bets and result")
    return errors


def validate_file(path):
    doc = json.loads(Path(path).read_text())
    name = Path(path).name
    for k in KINDS:
        if name.startswith(k):
            kind = k
            break
    else:
        return [f"cannot infer kind of {name}"]
    errors = schema_errors(kind, doc)
    if not errors and kind == "prediction":
        errors += prediction_errors(doc)
    if not errors and kind == "evidence":
        errors += evidence_errors(doc)
    return errors


def main(argv):
    failed = False
    for path in argv:
        for err in validate_file(path):
            failed = True
            print(f"{path}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
