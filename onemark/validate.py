"""Deterministic validation for 1mark documents.

JSON Schema covers structure; the functions here cover rules a schema cannot
express (sorted trios, cross-document consistency, pre-race integrity, hashing).
No network access, no AI calls.
"""
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
CONFIG_DIR = ROOT / "config"
KINDS = ("task", "evidence", "prediction", "decision", "risk_decision", "review", "learning_proposal")


def load_units_config():
    return json.loads((CONFIG_DIR / "units-v1.json").read_text())


def load_vocabulary():
    return json.loads((CONFIG_DIR / "evidence-vocabulary-v1.json").read_text())


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


def field_errors(field, boat=None, value=None, check_value=False):
    """Standard vocabulary names are checked; namespaced extensions (x-ns:name) are free-form."""
    vocab = load_vocabulary()
    if re.match(vocab["extension_pattern"], field):
        return []
    spec = vocab["fields"].get(field)
    if spec is None:
        return [f"unknown field {field}: use the standard vocabulary or an x-<ns>:<name> extension"]
    errors = []
    if spec["per_boat"] and boat is None:
        errors.append(f"{field} requires boat")
    if not spec["per_boat"] and boat is not None:
        errors.append(f"{field} must not have boat")
    if check_value:
        if spec["type"] == "integer":
            okay = isinstance(value, int) and not isinstance(value, bool)
        elif spec["type"] == "number":
            okay = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            okay = isinstance(value, str)
        if not okay:
            errors.append(f"{field} must be {spec['type']}")
        elif "enum" in spec and value not in spec["enum"]:
            errors.append(f"{field} must be one of {spec['enum']}")
        elif spec["type"] != "string" and not spec["min"] <= value <= spec["max"]:
            errors.append(f"{field} out of range {spec['min']}..{spec['max']} {spec['unit']}")
    return errors


def prediction_errors(pred):
    errors = bets_errors(pred.get("bets", []))
    for step in pred.get("wait_plan", []):
        obs = step["observation"]
        errors += field_errors(obs["field"], obs.get("boat"))
        change = step["expected_change"]
        for key in ("add_bets", "remove_bets", "change_units"):
            errors += bets_errors(change.get(key, []))
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
    for o in evidence["observations"]:
        errors += [f"{o['id']}: {e}" for e in field_errors(o["field"], o.get("boat"), o["value"], True)]
    obs_ids = {o["id"] for o in evidence["observations"]}
    for inf in evidence["inferences"]:
        for ref in inf["based_on"]:
            if ref not in obs_ids:
                errors.append(f"inference {inf['id']} must be based on observations, got {ref}")
    return errors


def leak_errors(task, evidence):
    """Pre-race evidence must be pre_race and observed no later than the cutoff."""
    cutoff = parse_ts(task["prediction_cutoff"])
    errors = []
    if cutoff > parse_ts(task["scheduled_start"]):
        errors.append("prediction_cutoff is after scheduled_start")
    if evidence["race_id"] != task["race_id"]:
        errors.append("evidence does not match task")
    for o in evidence["observations"]:
        if o["phase"] == "post_race" or parse_ts(o["observed_at"]) > cutoff:
            errors.append(f"post-cutoff evidence leaked into pre-race bundle: {o['id']}")
    return errors


def prediction_integrity_errors(task, evidence, pred):
    """Cross-document rules, including post-race leakage prevention."""
    errors = leak_errors(task, evidence)
    if pred["task_id"] != task["task_id"] or pred["race_id"] != task["race_id"]:
        errors.append("prediction does not match task")
    if pred["evidence_id"] != evidence["evidence_id"]:
        errors.append("prediction does not match evidence")
    if parse_ts(pred["locked_at"]) > parse_ts(task["prediction_cutoff"]):
        errors.append("prediction locked after prediction_cutoff")
    known = {o["id"] for o in evidence["observations"]} | {i["id"] for i in evidence["inferences"]}
    cited = [e for a in pred.get("boat_assessments", []) for e in a["evidence_ids"]]
    cited += pred.get("skip_analysis", {}).get("evidence_ids", [])
    errors += [f"unknown evidence id {e}" for e in cited if e not in known]
    return errors


def _key(bet):
    return (bet["bet_type"], tuple(bet["combination"]))


def apply_changes(baseline_bets, changes):
    """Deterministically derive final bets from a locked baseline; returns (bets, errors)."""
    bets = {_key(b): b["units"] for b in baseline_bets}
    errors = []
    touched = set()
    for c in changes:
        k = _key(c)
        errors += combination_errors(c["bet_type"], c["combination"])
        if k in touched:
            errors.append(f"multiple changes to {k}")
        touched.add(k)
        if c["action"] == "add":
            if k in bets:
                errors.append(f"cannot add existing bet {k}")
            bets[k] = c["units"]
        elif k not in bets:
            errors.append(f"cannot {c['action']} missing bet {k}")
        elif c["action"] == "remove":
            del bets[k]
        elif c["units"] == bets[k]:
            errors.append(f"change_units for {k} does not change units")
        else:
            bets[k] = c["units"]
    return bets, errors


def decision_errors(task, baseline, evidence, decision):
    errors = []
    if baseline["status"] != "WAIT":
        errors.append("decision requires a WAIT baseline")
    if decision["baseline_prediction_id"] != baseline["prediction_id"] or decision["baseline_sha256"] != canonical_sha256(baseline):
        errors.append("decision does not reference the locked baseline")
    if decision["task_id"] != task["task_id"] or decision["race_id"] != task["race_id"]:
        errors.append("decision does not match task")
    if decision["evidence_id"] != evidence["evidence_id"]:
        errors.append("decision does not match evidence")
    errors += leak_errors(task, evidence)
    if not parse_ts(baseline["locked_at"]) <= parse_ts(decision["locked_at"]) <= parse_ts(task["prediction_cutoff"]):
        errors.append("decision must be locked between baseline lock and prediction_cutoff")
    known = {o["id"] for o in evidence["observations"]} | {i["id"] for i in evidence["inferences"]}
    cited = [e for c in decision["changes"] for e in c["evidence_ids"]]
    cited += decision.get("skip_analysis", {}).get("evidence_ids", [])
    errors += [f"unknown evidence id {e}" for e in cited if e not in known]
    bets, errs = apply_changes(baseline.get("bets", []), decision["changes"])
    errors += errs
    if decision["final"] == "BET":
        errors += bets_errors(decision["final_bets"])
        final = {_key(b): b["units"] for b in decision["final_bets"]}
        if final != bets:
            errors.append("final_bets do not equal baseline bets with changes applied")
    return errors


def subject_bets(subject):
    return subject["bets"] if "bets" in subject else subject.get("final_bets", [])


def risk_errors(risk, subject, config=None):
    """The risk gate approves or rejects the complete proposal; it never alters it."""
    config = config or load_units_config()
    errors = []
    if risk["subject_id"] != subject.get("prediction_id", subject.get("decision_id")) or risk["subject_sha256"] != canonical_sha256(subject):
        errors.append("risk decision does not match locked subject")
    is_pre = subject.get("status") == "PRE"
    is_bet = "decision_id" in subject and subject["final"] == "BET"
    if not (is_pre or is_bet):
        errors.append("risk decisions apply only to PRE predictions or BET decisions")
    if risk["subject_type"] != ("decision" if "decision_id" in subject else "prediction"):
        errors.append("subject_type does not match subject")
    if risk["config_version"] != config["config_version"]:
        errors.append("config_version mismatch")
    if risk["decision"] == "approved":
        expected = sum(b["units"] for b in subject_bets(subject)) * config["unit_value_jpy"]
        if risk["total_stake_jpy"] != expected:
            errors.append(f"total_stake_jpy must be {expected} (all units x unit value, no partial execution)")
        if risk["total_stake_jpy"] % config["stake_multiple_jpy"]:
            errors.append("stake not a multiple of stake_multiple_jpy")
        if any(b["units"] < config["min_units_per_bet"] for b in subject_bets(subject)):
            errors.append("units below min_units_per_bet")
    return errors


def review_errors(review, pred, decision=None, risk=None, config=None):
    config = config or load_units_config()
    errors = []
    if review["prediction_sha256"] != canonical_sha256(pred) or review["prediction_id"] != pred["prediction_id"]:
        errors.append("review does not reference the locked prediction")
    subject = pred
    if decision is not None or "decision_id" in review:
        if decision is None or review.get("decision_sha256") != canonical_sha256(decision) or review.get("decision_id") != decision["decision_id"]:
            errors.append("review does not reference the locked decision")
        else:
            subject = decision
    order = review["result"]["finish_order"]
    bets = subject_bets(subject)
    expected = [{"bet_type": b["bet_type"], "combination": b["combination"], "hit": bet_hit(b, order)} for b in bets]
    if review["outcome"]["bet_results"] != expected:
        errors.append("outcome.bet_results does not match locked bets and result")
    for p in review["result"]["payouts"]:
        errors += combination_errors(p["bet_type"], p["combination"])
    if "settlement" in review:
        errors += settlement_errors(review, subject, risk, config)
    return errors


def settlement_errors(review, subject, risk, config):
    st = review["settlement"]
    if risk is None or risk["decision"] != "approved":
        return ["settlement requires an approved risk decision"]
    errors = []
    if st["risk_decision_id"] != risk["risk_decision_id"] or st["risk_decision_sha256"] != canonical_sha256(risk):
        errors.append("settlement does not reference the risk decision")
    if risk["subject_sha256"] != canonical_sha256(subject):
        errors.append("risk decision does not match reviewed subject")
    order = review["result"]["finish_order"]
    payouts = {(p["bet_type"], tuple(p["combination"])): p for p in review["result"]["payouts"]}
    lines, total_stake, total_ret = [], 0, 0
    for b in subject_bets(subject):
        stake = b["units"] * config["unit_value_jpy"]
        p = payouts.get(_key(b))
        if p and p["kind"] == "refund":
            status, ret = "refund", stake
        elif bet_hit(b, order):
            win = payouts.get(_key({"bet_type": b["bet_type"], "combination": sorted(order) if b["bet_type"] == "trio" else list(order)}))
            if not win or win["kind"] != "win":
                errors.append(f"missing official win payout for {_key(b)}")
                continue
            status, ret = "win", win["payout_per_100_jpy"] * stake // 100
        else:
            status, ret = "miss", 0
        lines.append({"bet_type": b["bet_type"], "combination": b["combination"], "units": b["units"],
                      "stake_jpy": stake, "status": status, "returned_jpy": ret})
        total_stake += stake
        total_ret += ret
    if not errors and (st["lines"] != lines or st["total_stake_jpy"] != total_stake
                       or st["total_returned_jpy"] != total_ret or st["profit_jpy"] != total_ret - total_stake):
        errors.append("settlement does not match deterministic calculation")
    if risk["total_stake_jpy"] != total_stake and not errors:
        errors.append("settlement stake differs from approved stake")
    return errors


def validate_file(path):
    doc = json.loads(Path(path).read_text())
    name = Path(path).name
    if name.startswith("units-"):
        return schema_errors("units_config", doc)
    if name.startswith("evidence-vocabulary"):
        return []
    for k in KINDS:
        if name.startswith(k):
            kind = k
            break
    else:
        return [f"cannot infer kind of {name}"]
    errors = schema_errors(kind, doc)
    if not errors and kind == "prediction":
        errors += prediction_errors(doc)
    if not errors and kind == "decision":
        errors += bets_errors(doc.get("final_bets", []))
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
