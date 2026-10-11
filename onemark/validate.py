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
KINDS = ("task", "evidence", "prediction", "decision", "risk_decision", "review", "learning_proposal",
         "workflow_state", "exhibition_revision", "batch_summary")


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
    """Structure and type of standard fields. Ranges are provisional: see field_warnings."""
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
    return errors


def field_warnings(field, value):
    """Provisional ranges/enums never reject an observation; they only warn."""
    spec = load_vocabulary()["fields"].get(field)
    if spec is None or isinstance(value, bool):
        return []
    if "provisional_enum" in spec and value not in spec["provisional_enum"]:
        return [f"{field}={value!r} outside provisional set (raw value kept)"]
    if "provisional_min" in spec and isinstance(value, (int, float)):
        if not spec["provisional_min"] <= value <= spec["provisional_max"]:
            return [f"{field}={value} outside provisional range {spec['provisional_min']}..{spec['provisional_max']} {spec['unit']} (raw value kept)"]
    return []


def operations_errors(ops):
    errors = []
    for op in ops:
        errors += combination_errors(op["bet_type"], op["combination"])
    return errors


def _key(bet):
    return (bet["bet_type"], tuple(bet["combination"]))


def apply_operations(baseline_bets, ops):
    """Apply add/remove/change_units (shared by WAIT plan and decision); returns ({key: units}, errors)."""
    bets = {_key(b): b["units"] for b in baseline_bets}
    errors, touched = operations_errors(ops), set()
    for op in ops:
        k = _key(op)
        if k in touched:
            errors.append(f"multiple operations on {k}")
        touched.add(k)
        if op["action"] == "add":
            if k in bets:
                errors.append(f"cannot add existing bet {k}")
            bets[k] = op["units"]
        elif k not in bets:
            errors.append(f"cannot {op['action']} missing bet {k}")
        elif op["action"] == "remove":
            del bets[k]
        elif op["units"] == bets[k]:
            errors.append(f"change_units for {k} does not change units")
        else:
            bets[k] = op["units"]
    return bets, errors


def _semver(text):
    return tuple(int(x) for x in text.split("."))


WAIT_EVIDENCE_FIELDS = ("uncertainty", "why_relevant", "expected_change", "materiality")


def protocol_v11_errors(pred):
    """Prediction Protocol v1.1 rules (applies to protocol_version >= 1.1.0).

    No bet count, unit total or target PRE/WAIT/SKIP ratio is imposed; only that choices are justified.
    """
    status = pred["status"]
    if status not in ("PRE", "WAIT", "SKIP"):
        return []
    errors = []
    if "evaluation" not in pred:
        errors.append("protocol 1.1: evaluation is required")
    if status == "PRE" and "timing_rationale" not in pred:
        errors.append("protocol 1.1: PRE requires timing_rationale (why waiting adds no material value)")
    if status == "SKIP" and "completed_analysis" not in pred.get("skip_analysis", {}):
        errors.append("protocol 1.1: SKIP requires skip_analysis.completed_analysis")
    if status in ("PRE", "WAIT"):
        br = pred.get("bet_rationale")
        if br is None:
            errors.append("protocol 1.1: bet_rationale is required")
        else:
            bets = {_key(b): b["units"] for b in pred.get("bets", [])}
            sel = [_key(s) for s in br["selections"]]
            if len(sel) != len(set(sel)) or set(sel) != set(bets):
                errors.append("bet_rationale.selections must explain exactly the proposed bets")
            if br["total_units"]["units"] != sum(bets.values()):
                errors.append("bet_rationale.total_units does not equal the sum of bet units")
    for st in pred.get("wait_plan", []):
        sid = st["step_id"]
        errors += [f"{sid}: protocol 1.1 requires {k}" for k in WAIT_EVIDENCE_FIELDS if k not in st]
        basis = st.get("threshold_basis")
        if re.search(r"\d", st["decision_threshold"]):
            if basis is None:
                errors.append(f"{sid}: numerical threshold without threshold_basis (unsupported)")
        if basis:
            if basis["kind"] in ("protocol_rule", "validated_knowledge") and "ref" not in basis:
                errors.append(f"{sid}: threshold_basis {basis['kind']} requires ref")
            if basis["kind"] == "unvalidated_hypothesis" and basis.get("automatic"):
                errors.append(f"{sid}: unvalidated hypothesis must not be an automatic decision rule")
    return errors


def prediction_errors(pred):
    errors = bets_errors(pred.get("bets", []))
    if _semver(pred["protocol_version"]) >= (1, 1, 0):
        errors += protocol_v11_errors(pred)
    steps = pred.get("wait_plan", [])
    ids = [st["step_id"] for st in steps]
    if len(ids) != len(set(ids)):
        errors.append("duplicate wait_plan step_id")
    for st in steps:
        obs = st["observation"]
        errors += field_errors(obs["field"], obs.get("boat"))
        errors += [f"{st['step_id']}: {e}" for e in apply_operations(pred.get("bets", []), st["expected_operations"])[1]]
    return errors


def _prediction_explanations(pred):
    """Yield labeled explanation text without changing the prediction document."""
    for i, assessment in enumerate(pred.get("boat_assessments", [])):
        yield f"boat_assessments[{i}].rationale", assessment.get("rationale")
    evaluation = pred.get("evaluation", {})
    for section, details in evaluation.items():
        if isinstance(details, dict):
            yield f"evaluation.{section}.summary", details.get("summary")
        elif section == "primary_drivers":
            for i, driver in enumerate(details):
                yield f"evaluation.primary_drivers[{i}].influence", driver.get("influence")
    rationale = pred.get("bet_rationale", {})
    for i, selection in enumerate(rationale.get("selections", [])):
        yield f"bet_rationale.selections[{i}].reason", selection.get("reason")
    yield "bet_rationale.allocation", rationale.get("allocation")
    yield "bet_rationale.total_units.reason", rationale.get("total_units", {}).get("reason")
    for i, exclusion in enumerate(rationale.get("exclusions", [])):
        yield f"bet_rationale.exclusions[{i}].reason", exclusion.get("reason")
    yield "timing_rationale", pred.get("timing_rationale")
    skip = pred.get("skip_analysis", {})
    yield "skip_analysis.reason", skip.get("reason")
    yield "skip_analysis.completed_analysis", skip.get("completed_analysis")


_NUMERIC_METHOD_RE = re.compile(
    r"\b(?:score|scoring|weight|weighted|probability|confidence)\b.{0,32}\b\d+(?:\.\d+)?\s*%?"
    r"|\b\d+(?:\.\d+)?\s*%?\s*(?:weight|points?|score|probability|confidence)\b",
    re.IGNORECASE,
)
_STATUS_THRESHOLD_RE = re.compile(
    r"\b(?:PRE|SKIP)\b.{0,48}\b(?:threshold|cutoff|at least|at most|above|below|greater than|less than)"
    r"\b.{0,24}\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def prediction_quality_warnings(pred):
    """Return advisory review prompts; these never affect deterministic validation."""
    if _semver(pred.get("protocol_version", "0.0.0")) < (1, 1, 0):
        return []
    warnings = []
    for path, text in _prediction_explanations(pred):
        if not isinstance(text, str) or not text.strip():
            warnings.append(f"machine-checkable: {path} has no explanation text")
        elif _NUMERIC_METHOD_RE.search(text) or _STATUS_THRESHOLD_RE.search(text):
            warnings.append(
                f"human review: {path} mentions numerical scoring, weights, or a PRE/SKIP threshold; "
                "verify its basis in protocol or validated knowledge"
            )
    if pred.get("status") in ("PRE", "WAIT"):
        warnings.append(
            "human review: check that each selected bet, excluded alternative, and unit allocation "
            "has a race-specific explanation grounded in the provided evidence"
        )
    return warnings


def _normalize_explanation(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def batch_prediction_quality_warnings(predictions):
    """Flag identical explanation wording reused across races as a review concern."""
    uses = {}
    for source, pred in predictions:
        race_id = pred.get("race_id")
        if not race_id:
            continue
        for path, text in _prediction_explanations(pred):
            if isinstance(text, str):
                normalized = _normalize_explanation(text)
                if len(normalized) >= 12:
                    uses.setdefault(normalized, []).append((source, race_id, path))

    warnings = []
    for occurrences in uses.values():
        races = {race_id for _, race_id, _ in occurrences}
        if len(races) < 2:
            continue
        locations = ", ".join(f"{source} ({race_id}: {path})" for source, race_id, path in occurrences)
        warning = (
            "human review: identical explanation wording appears across different races; "
            f"confirm it is not generic boilerplate: {locations}"
        )
        warnings.extend((source, warning) for source, _, _ in occurrences)
    return warnings


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


def evidence_warnings(evidence):
    return [f"{o['id']}: {w}" for o in evidence["observations"] for w in field_warnings(o["field"], o["value"])]


def leak_errors(task, evidence):
    """Pre-race evidence must be pre_race and observed no later than the cutoff."""
    cutoff = parse_ts(task["prediction_cutoff"])
    errors = []
    if "scheduled_start" in task and cutoff > parse_ts(task["scheduled_start"]):
        errors.append("prediction_cutoff is after scheduled_start")
    if evidence["race_id"] != task["race_id"]:
        errors.append("evidence does not match task")
    for o in evidence["observations"]:
        if o["phase"] == "post_race" or parse_ts(o["observed_at"]) > cutoff:
            errors.append(f"post-cutoff evidence leaked into pre-race bundle: {o['id']}")
    return errors


def lock_time_errors(task, evidence, record):
    """locked_at is the actual finalization time (never substituted). It may be later than prediction_cutoff, which only
    bounds Evidence observation, but it must not precede the Evidence it used nor follow the applicable deadline.
    The applicable deadline is task.betting_deadline, else scheduled_start. Both are SCHEDULED times: passing this
    check does not prove betting was still open (that needs the actual close time, which is not recorded)."""
    errors = []
    locked = parse_ts(record["locked_at"])
    if evidence["observations"] and locked < max(parse_ts(o["observed_at"]) for o in evidence["observations"]):
        errors.append("record locked before the latest evidence observation")
    deadline = task.get("betting_deadline") or task.get("scheduled_start")
    if deadline and locked > parse_ts(deadline):
        errors.append("record locked after the applicable deadline; not an actionable pre-race record")
    return errors


def prediction_integrity_errors(task, evidence, pred):
    """Cross-document rules, including post-race leakage prevention."""
    errors = leak_errors(task, evidence)
    if pred["task_id"] != task["task_id"] or pred["race_id"] != task["race_id"]:
        errors.append("prediction does not match task")
    if pred["evidence_id"] != evidence["evidence_id"]:
        errors.append("prediction does not match evidence")
    errors += lock_time_errors(task, evidence, pred)
    known = {o["id"] for o in evidence["observations"]} | {i["id"] for i in evidence["inferences"]}
    cited = [e for a in pred.get("boat_assessments", []) for e in a["evidence_ids"]]
    cited += pred.get("skip_analysis", {}).get("evidence_ids", [])
    ev = pred.get("evaluation", {})
    for k, sec in ev.items():
        if isinstance(sec, dict):
            cited += sec.get("evidence_ids", [])
    cited += [d["evidence_id"] for d in ev.get("primary_drivers", [])]
    errors += [f"unknown evidence id {e}" for e in cited if e not in known]
    return errors


def decision_errors(task, baseline, evidence, decision):
    errors = []
    if baseline["status"] != "WAIT":
        errors.append("decision requires a WAIT baseline")
    if decision["baseline_prediction_id"] != baseline["prediction_id"] or decision["baseline_sha256"] != canonical_sha256(baseline):
        errors.append("decision does not reference the locked baseline")
    if decision["task_id"] != task["task_id"] or decision["race_id"] != task["race_id"]:
        errors.append("decision does not match task")
    cutoff = parse_ts(task["prediction_cutoff"])
    locked = parse_ts(decision["locked_at"])
    if decision["final"] == "INCOMPLETE":
        if locked < cutoff:
            errors.append("INCOMPLETE may only be recorded at or after prediction_cutoff")
        return errors
    if evidence is None or decision["evidence_id"] != evidence["evidence_id"]:
        return errors + ["decision does not match evidence"]
    errors += leak_errors(task, evidence)
    if locked < parse_ts(baseline["locked_at"]):
        errors.append("decision must be locked at or after the baseline lock")
    errors += lock_time_errors(task, evidence, decision)
    known = {o["id"] for o in evidence["observations"]} | {i["id"] for i in evidence["inferences"]}
    steps = {st["step_id"] for st in baseline.get("wait_plan", [])}
    cited = [e for c in decision["changes"] for e in c["evidence_ids"]]
    cited += decision.get("skip_analysis", {}).get("evidence_ids", [])
    errors += [f"unknown evidence id {e}" for e in cited if e not in known]
    errors += [f"unknown plan step {sid}" for c in decision["changes"] for sid in c["plan_step_ids"] if sid not in steps]
    bets, errs = apply_operations(baseline.get("bets", []), decision["changes"])
    errors += errs
    if decision["final"] == "BET":
        errors += bets_errors(decision["final_bets"])
        if {_key(b): b["units"] for b in decision["final_bets"]} != bets:
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
    if review["race_id"] != pred["race_id"]:
        errors.append("review race_id does not match prediction")
    subject = pred
    if pred["status"] == "WAIT" and decision is None:
        errors.append("WAIT prediction must be reviewed through its decision")
    if decision is not None or "decision_id" in review:
        if decision is None or review.get("decision_sha256") != canonical_sha256(decision) or review.get("decision_id") != decision["decision_id"]:
            errors.append("review does not reference the locked decision")
        else:
            subject = decision
    order = review["result"]["finish_order"]
    expected = [{"bet_type": b["bet_type"], "combination": b["combination"], "hit": bet_hit(b, order)} for b in subject_bets(subject)]
    if review["outcome"]["bet_results"] != expected:
        errors.append("outcome.bet_results does not match locked bets and result")
    errors += result_errors(review["result"])
    if "settlement" in review:
        errors += settlement_errors(review, subject, risk, config)
    return errors


def result_errors(result):
    errors = []
    keys = [_key(p) for p in result["payouts"]]
    if len(keys) != len(set(keys)):
        errors.append("duplicate payout entries")
    info = result["refund_info"]
    refunded = [_key(r) for r in info["refunded"]]
    if len(refunded) != len(set(refunded)):
        errors.append("duplicate refunded combinations")
    for p in result["payouts"]:
        errors += combination_errors(p["bet_type"], p["combination"])
    for r in info["refunded"]:
        errors += combination_errors(r["bet_type"], r["combination"])
    if set(keys) & set(refunded):
        errors.append("combination has both a win payout and a refund")
    return errors


def settle(subject, result, config):
    """Deterministic settlement. Unknown refund status is never counted as a loss.

    Returns (lines, unresolved_reasons, errors).
    """
    info = result["refund_info"]
    refunded = {_key(r) for r in info["refunded"]}
    payouts = {_key(p): p["payout_per_100_jpy"] for p in result["payouts"]}
    order = result["finish_order"]
    lines, reasons, errors = [], [], []
    for b in subject_bets(subject):
        k, stake = _key(b), b["units"] * config["unit_value_jpy"]
        line = {"bet_type": b["bet_type"], "combination": b["combination"], "units": b["units"], "stake_jpy": stake}
        if info["race_wide"] or k in refunded:
            line.update(status="refund", returned_jpy=stake)
        elif bet_hit(b, order):
            win_key = ("trio", tuple(sorted(order))) if b["bet_type"] == "trio" else ("trifecta", tuple(order))
            if win_key in payouts:
                line.update(status="win", returned_jpy=payouts[win_key] * stake // 100)
            elif info["status"] == "confirmed":
                errors.append(f"missing official win payout for {k}")
                continue
            else:
                line["status"] = "unresolved"
                reasons.append(f"{k}: no payout and refund status {info['status']}")
        elif info["status"] == "confirmed":
            line.update(status="miss", returned_jpy=0)
        else:
            line["status"] = "unresolved"
            reasons.append(f"{k}: refund status {info['status']}")
        lines.append(line)
    return lines, reasons, errors


def settlement_errors(review, subject, risk, config):
    st = review["settlement"]
    if risk is None or risk["decision"] != "approved":
        return ["settlement requires an approved risk decision"]
    if not subject_bets(subject):
        return ["settlement present but there are no bets"]
    errors = []
    if st["risk_decision_id"] != risk["risk_decision_id"] or st["risk_decision_sha256"] != canonical_sha256(risk):
        errors.append("settlement does not reference the risk decision")
    if risk["subject_sha256"] != canonical_sha256(subject):
        errors.append("risk decision does not match reviewed subject")
    lines, reasons, errs = settle(subject, review["result"], config)
    if errs:
        return errors + errs
    total_stake = sum(line["stake_jpy"] for line in lines)
    if risk["total_stake_jpy"] != total_stake:
        errors.append("settlement stake differs from approved stake")
    if st["lines"] != lines:
        errors.append("settlement lines do not match deterministic calculation")
    if "total_stake_jpy" in st and st["total_stake_jpy"] != total_stake:
        errors.append("settlement total_stake_jpy mismatch")
    if reasons:
        if st["status"] != "unresolved":
            errors.append("settlement must be unresolved: " + "; ".join(reasons))
    elif st["status"] != "resolved":
        errors.append("settlement is resolvable but marked unresolved")
    else:
        returned = sum(line["returned_jpy"] for line in lines)
        if st["total_returned_jpy"] != returned or st["profit_jpy"] != returned - total_stake:
            errors.append("settlement totals do not match deterministic calculation")
    return errors


def validate_lifecycle(doc, config=None):
    """Single entry point: schema validation of every record plus all cross-document checks.

    `doc` is a lifecycle fixture (schemas/lifecycle.schema.json) holding the records of one race.
    """
    errors = schema_errors("lifecycle", doc)
    if errors:
        return errors
    task, pred = doc["task"], doc["prediction"]
    records = [("task", task, "task"), ("prediction", pred, "prediction")]
    records += [(f"evidence[{i}]", e, "evidence") for i, e in enumerate(doc["evidence"])]
    records += [(k, doc[k], k) for k in ("decision", "risk_decision", "review") if k in doc]
    records += [(f"learning_proposals[{i}]", lp, "learning_proposal") for i, lp in enumerate(doc.get("learning_proposals", []))]
    for label, rec, kind in records:
        errors += [f"{label}: {e}" for e in schema_errors(kind, rec)]
        if rec.get("synthetic") != doc["synthetic"]:
            errors.append(f"{label}: synthetic flag differs from lifecycle")
    if errors:
        return errors
    errors += prediction_errors(pred)
    evid = {}
    for e in doc["evidence"]:
        if e["evidence_id"] in evid:
            errors.append(f"duplicate evidence_id {e['evidence_id']}")
        evid[e["evidence_id"]] = e
        errors += evidence_errors(e)
        errors += leak_errors(task, e)
    if pred["evidence_id"] not in evid:
        return errors + ["prediction evidence_id not in lifecycle evidence"]
    errors += prediction_integrity_errors(task, evid[pred["evidence_id"]], pred)
    decision = doc.get("decision")
    if pred["status"] == "WAIT" and decision is None:
        errors.append("WAIT baseline needs a decision (record INCOMPLETE if none was made before the cutoff)")
    if decision is not None:
        errors += decision_errors(task, pred, evid.get(decision.get("evidence_id")), decision)
    subject = decision if decision is not None else (pred if pred["status"] == "PRE" else None)
    risk = doc.get("risk_decision")
    if risk is not None:
        errors += risk_errors(risk, subject, config) if subject is not None else ["risk decision without a betting proposal"]
    review = doc.get("review")
    if review is not None:
        errors += review_errors(review, pred, decision, risk, config)
        if review["race_id"] != task["race_id"]:
            errors.append("review does not match task")
        lps = doc.get("learning_proposals", [])
        if sorted(review["learning_proposal_ids"]) != sorted(lp["proposal_id"] for lp in lps):
            errors.append("learning proposals do not match review.learning_proposal_ids")
        errors += [f"proposal {lp['proposal_id']} cites unknown review" for lp in lps for ev in lp["evidence"] if ev["review_id"] != review["review_id"]]
    elif doc.get("learning_proposals"):
        errors.append("learning proposals require a review")
    return errors


def validate_file(path, warnings=None):
    doc = json.loads(Path(path).read_text())
    name = Path(path).name
    if name.startswith("units-"):
        return schema_errors("units_config", doc)
    if name.startswith("evidence-vocabulary"):
        return []
    if name.startswith("lifecycle"):
        errors = validate_lifecycle(doc)
        if not errors and warnings is not None:
            for e in doc["evidence"]:
                warnings += evidence_warnings(e)
            warnings += prediction_quality_warnings(doc["prediction"])
        return errors
    for k in KINDS:
        if name.startswith(k):
            kind = k
            break
    else:
        return [f"cannot infer kind of {name}"]
    errors = schema_errors(kind, doc)
    if not errors and kind == "prediction":
        errors += prediction_errors(doc)
        if not errors and warnings is not None:
            warnings += prediction_quality_warnings(doc)
    if not errors and kind in ("workflow_state", "exhibition_revision", "batch_summary"):
        from onemark import workflow as wf

        errors += {"workflow_state": wf.workflow_state_errors, "exhibition_revision": wf.revision_errors,
                   "batch_summary": wf.batch_summary_errors}[kind](doc)
    if not errors and kind == "decision":
        errors += bets_errors(doc.get("final_bets", []))
    if not errors and kind == "evidence":
        errors += evidence_errors(doc)
        if warnings is not None:
            warnings += evidence_warnings(doc)
    return errors


def main(argv):
    failed = False
    predictions = []
    for path in argv:
        warnings = []
        errors = validate_file(path, warnings)
        for err in errors:
            failed = True
            print(f"{path}: {err}")
        for w in warnings:
            print(f"{path}: warning: {w}", file=sys.stderr)
        if not errors and Path(path).name.startswith(("prediction", "lifecycle")):
            doc = json.loads(Path(path).read_text())
            pred = doc["prediction"] if Path(path).name.startswith("lifecycle") else doc
            predictions.append((path, pred))
    for path, warning in batch_prediction_quality_warnings(predictions):
        print(f"{path}: warning: {warning}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
