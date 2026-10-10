import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from onemark import validate as v

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def load(name):
    return json.loads((EX / f"{name}.synthetic.json").read_text())


def bad(name, mutate):
    doc = copy.deepcopy(load(name))
    mutate(doc)
    return doc


def ok(kind, doc):  # structural + file-level rules
    if v.schema_errors(kind, doc):
        return False
    if kind == "prediction":
        return not v.prediction_errors(doc)
    if kind == "evidence":
        return not v.evidence_errors(doc)
    return True


@pytest.mark.parametrize("schema", v.SCHEMAS.values(), ids=list(v.SCHEMAS))
def test_schemas_are_valid(schema):
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", sorted((ROOT / "config").glob("*.json")), ids=lambda p: p.name)
def test_config_valid(path):
    assert v.validate_file(path) == []


@pytest.mark.parametrize("path", sorted(EX.glob("*.json")), ids=lambda p: p.name)
def test_examples_valid_and_synthetic(path):
    assert path.name.endswith(".synthetic.json")
    assert json.loads(path.read_text())["synthetic"] is True
    assert v.validate_file(path) == []


# --- boats, combinations, units, stakes
def test_exactly_six_boats_in_order():
    assert not ok("prediction", bad("prediction_pre", lambda d: d["boat_assessments"].pop()))
    assert not ok("prediction", bad("prediction_pre", lambda d: d["boat_assessments"].reverse()))
    assert not ok("prediction", bad("prediction_pre", lambda d: d["boat_assessments"][5].update(boat=7)))


@pytest.mark.parametrize("combo", [[1, 2], [1, 2, 2], [0, 1, 2], [1, 2, 7], [1, 2, 3, 4]])
def test_invalid_combinations(combo):
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(combination=combo)))


def test_trio_must_be_ascending_but_trifecta_ordered():
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][1].update(combination=[4, 2, 1])))
    assert ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(combination=[4, 2, 1])))


# --- statuses
def test_pre_requires_bets_and_assessments():
    for key in ("bets", "boat_assessments"):
        assert not ok("prediction", bad("prediction_pre", lambda d: d.pop(key)))
    assert not ok("prediction", bad("prediction_pre", lambda d: d.update(bets=[])))


def test_pre_does_not_need_risk_approval():
    assert ok("prediction", load("prediction_pre"))
    assert not ok("prediction", bad("prediction_pre", lambda d: d.update(risk_decision_id="x")))


def test_wait_requires_baseline_and_plan():
    for key in ("bets", "boat_assessments", "wait_plan"):
        assert not ok("prediction", bad("prediction_wait", lambda d: d.pop(key)))
    assert not ok("prediction", bad("prediction_wait", lambda d: d.update(wait_plan=[])))


@pytest.mark.parametrize("key", ["observation", "decision_threshold", "expected_change"])
def test_wait_plan_items_complete(key):
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0].pop(key)))


def test_skip_requires_analysis_and_has_no_bets():
    assert not ok("prediction", bad("prediction_skip", lambda d: d.pop("skip_analysis")))
    assert not ok("prediction", bad("prediction_skip", lambda d: d.pop("boat_assessments")))
    assert not ok("prediction", bad("prediction_skip", lambda d: d.update(bets=load("prediction_pre")["bets"])))
    assert not ok("prediction", bad("prediction_skip", lambda d: d["skip_analysis"].update(evidence_ids=[])))


@pytest.mark.parametrize("name,key", [("prediction_unavailable", "unavailable_reason"), ("prediction_incomplete", "missing")])
def test_operational_states_carry_no_prediction(name, key):
    assert not ok("prediction", bad(name, lambda d: d.pop(key)))
    assert not ok("prediction", bad(name, lambda d: d.update(bets=load("prediction_pre")["bets"])))
    assert not ok("prediction", bad(name, lambda d: d.update(boat_assessments=load("prediction_pre")["boat_assessments"])))


def test_unknown_status_rejected():
    assert not ok("prediction", bad("prediction_skip", lambda d: d.update(status="MAYBE")))


# --- risk gate
# --- evidence integrity
def test_evidence_requires_source_and_timestamp_and_missing_list():
    for key in ("source_url", "observed_at", "phase"):
        assert not ok("evidence", bad("evidence", lambda d: d["observations"][0].pop(key)))
    assert not ok("evidence", bad("evidence", lambda d: d.pop("missing_data")))
    assert not ok("evidence", bad("evidence", lambda d: d["observations"][0].update(source_url="not a url")))


def test_inference_must_reference_observations():
    assert v.evidence_errors(bad("evidence", lambda d: d["inferences"][0].update(based_on=["i1"])))
    assert not ok("evidence", bad("evidence", lambda d: d["inferences"][0].update(based_on=[])))


def test_post_race_information_cannot_leak():
    task, pred = load("task"), load("prediction_pre")
    assert v.prediction_integrity_errors(task, load("evidence"), pred) == []
    late = bad("evidence", lambda d: d["observations"][0].update(observed_at="2000-01-01T15:30:00+09:00"))
    assert v.prediction_integrity_errors(task, late, pred)
    post = bad("evidence", lambda d: d["observations"][0].update(phase="post_race"))
    assert v.prediction_integrity_errors(task, post, pred)
    assert v.prediction_integrity_errors(task, load("evidence"), bad("prediction_pre", lambda d: d.update(locked_at="2000-01-01T14:55:00+09:00")))
    unknown = bad("prediction_pre", lambda d: d["boat_assessments"][0].update(evidence_ids=["zzz"]))
    assert v.prediction_integrity_errors(task, load("evidence"), unknown)


# --- review and learning
@pytest.mark.parametrize("key", ["outcome", "prediction_error", "decision_quality", "protocol_compliance"])
def test_review_separates_four_assessments(key):
    assert not ok("review", bad("review", lambda d: d.pop(key)))


def test_learning_proposals_need_evidence_and_stay_proposed():
    assert not ok("learning_proposal", bad("learning_proposal", lambda d: d.update(evidence=[])))
    assert not ok("learning_proposal", bad("learning_proposal", lambda d: d.update(status="validated")))


def test_validated_knowledge_and_protocols_untouched_by_examples():
    assert [p.name for p in (ROOT / "knowledge" / "validated").iterdir()] == [".gitkeep"]


# --- v0.1 decisions: qualitative, units, bet types
def test_assessments_are_qualitative_not_probabilities():
    assert not ok("prediction", bad("prediction_pre", lambda d: d["boat_assessments"][0].update(win_probability=0.5)))
    assert not ok("prediction", bad("prediction_pre", lambda d: d["boat_assessments"][0].update(assessment="0.5")))


def test_trifecta_and_trio_on_same_boats_are_distinct_and_allowed():
    pre = load("prediction_pre")
    assert {b["bet_type"] for b in pre["bets"]} == {"trifecta", "trio"}
    assert ok("prediction", pre)
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"].append(copy.deepcopy(d["bets"][0]))))


def test_units_have_no_hardcoded_cap_and_bad_units_rejected():
    assert ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(units=500)))
    for units in (0, -1, 1.5):
        assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(units=units)))
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(stake_jpy=100)))


def test_unit_config_is_versioned_and_1u_is_100_jpy():
    cfg = v.load_units_config()
    assert cfg["config_version"] and cfg["unit_value_jpy"] == 100


# --- risk gate: approve/reject complete proposal only
def test_risk_approval_checks_full_stake():
    pre, risk = load("prediction_pre"), load("risk_decision")
    assert v.risk_errors(risk, pre) == []
    assert v.risk_errors(bad("risk_decision", lambda d: d.update(total_stake_jpy=200)), pre)  # partial
    assert v.risk_errors(bad("risk_decision", lambda d: d.update(total_stake_jpy=600)), pre)
    assert v.risk_errors(bad("risk_decision", lambda d: d.update(config_version="9.9.9")), pre)
    assert v.risk_errors(risk, bad("prediction_pre", lambda d: d["bets"][0].update(units=1)))  # prediction changed
    assert v.risk_errors(risk, load("prediction_wait"))


def test_risk_cannot_reduce_or_carry_executions():
    assert not ok("risk_decision", bad("risk_decision", lambda d: d.update(decision="reduced")))
    assert not ok("risk_decision", bad("risk_decision", lambda d: d.update(executions=[])))
    assert not ok("risk_decision", bad("risk_decision", lambda d: d.pop("total_stake_jpy")))


def test_rejection_has_reason_and_no_stake():
    rej = load("risk_decision_rejected")
    assert ok("risk_decision", rej)
    assert not ok("risk_decision", bad("risk_decision_rejected", lambda d: d.pop("reason")))
    assert not ok("risk_decision", bad("risk_decision_rejected", lambda d: d.update(total_stake_jpy=500)))


def test_risk_applies_to_bet_decision_but_not_skip_decision():
    dec = load("decision_bet")
    risk = bad("risk_decision", lambda d: d.update(subject_type="decision", subject_id=dec["decision_id"], subject_sha256=v.canonical_sha256(dec), total_stake_jpy=300))
    assert v.risk_errors(risk, dec) == []
    skip = load("decision_skip")
    risk_skip = {**risk, "subject_id": skip["decision_id"], "subject_sha256": v.canonical_sha256(skip)}
    assert v.risk_errors(risk_skip, skip)


# --- WAIT revision
def decision_errors(decision=None, baseline=None, evidence=None):
    return v.decision_errors(load("task"), baseline or load("prediction_wait"), evidence or load("evidence_exhibition"), decision or load("decision_bet"))


def test_decision_derives_final_bets_from_immutable_baseline():
    assert decision_errors() == []
    assert decision_errors(load("decision_skip")) == []
    # changing the baseline invalidates the reference
    assert decision_errors(baseline=bad("prediction_wait", lambda d: d["bets"][0].update(units=9)))
    assert decision_errors(baseline=load("prediction_pre"))


def test_decision_may_keep_baseline_unchanged():
    wait = load("prediction_wait")
    keep = bad("decision_bet", lambda d: d.update(changes=[], final_bets=wait["bets"]))
    assert ok("decision", keep) and decision_errors(keep) == []


def test_decision_supports_add_remove_and_change_units():
    assert {c["action"] for c in load("decision_bet")["changes"]} == {"add", "remove", "change_units"}
    assert decision_errors(bad("decision_bet", lambda d: d.update(final_bets=d["final_bets"][:1])))
    assert decision_errors(bad("decision_bet", lambda d: d["changes"].pop()))
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][2].update(combination=[1, 3, 4])))  # remove missing
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][0].update(combination=[1, 2, 4])))  # add existing
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][1].update(units=2)))  # no-op change


def test_each_change_needs_evidence_and_reason_and_units_rules():
    for key in ("evidence_ids", "reason"):
        assert not ok("decision", bad("decision_bet", lambda d: d["changes"][0].pop(key)))
    assert not ok("decision", bad("decision_bet", lambda d: d["changes"][0].update(evidence_ids=[])))
    assert not ok("decision", bad("decision_bet", lambda d: d["changes"][0].pop("units")))
    assert not ok("decision", bad("decision_bet", lambda d: d["changes"][2].update(units=1)))
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][0].update(evidence_ids=["nope"])))


def test_decision_skip_requires_analysis_and_no_bets():
    assert ok("decision", load("decision_skip"))
    assert not ok("decision", bad("decision_skip", lambda d: d.pop("skip_analysis")))
    assert not ok("decision", bad("decision_skip", lambda d: d.update(final_bets=load("decision_bet")["final_bets"])))
    assert not ok("decision", bad("decision_skip", lambda d: d["changes"].append(load("decision_bet")["changes"][0])))
    assert not ok("decision", bad("decision_bet", lambda d: d.pop("final_bets")))
    assert not ok("decision", bad("decision_bet", lambda d: d.update(final="MAYBE")))


def test_decision_timing_and_leakage():
    assert decision_errors(bad("decision_bet", lambda d: d.update(locked_at="2000-01-01T10:00:00+09:00")))
    assert decision_errors(bad("decision_bet", lambda d: d.update(locked_at="2000-01-01T14:55:00+09:00")))
    late = bad("evidence_exhibition", lambda d: d["observations"][0].update(observed_at="2000-01-01T15:30:00+09:00"))
    assert decision_errors(evidence=late)


def test_wait_plan_may_change_units():
    assert load("prediction_wait")["wait_plan"][0]["expected_change"]["change_units"]


# --- results, payouts, settlement
def review_errors(review=None):
    pre = load("prediction_pre")
    return v.review_errors(review or load("review"), pre, risk=load("risk_decision"))


def test_review_computed_from_locked_prediction_and_settled_separately():
    assert review_errors() == []
    assert review_errors(bad("review", lambda d: d["outcome"]["bet_results"][0].update(hit=True)))
    assert v.review_errors(load("review"), bad("prediction_pre", lambda d: d["bets"][0].update(units=1)))
    assert "settlement" in load("review") and "settlement" not in load("review")["outcome"]


def test_settlement_uses_official_payouts():
    assert review_errors(bad("review", lambda d: d["settlement"].update(profit_jpy=0)))
    assert review_errors(bad("review", lambda d: d["settlement"]["lines"][1].update(returned_jpy=380)))
    assert review_errors(bad("review", lambda d: d["result"]["payouts"].pop(1)))  # hit but no payout
    assert not ok("review", bad("review", lambda d: d["result"]["payouts"][0].pop("payout_per_100_jpy")))
    assert not ok("review", bad("review", lambda d: d["result"].pop("payouts")))


def test_refund_payout_returns_stake():
    def refund(d):
        d["result"]["payouts"].append({"bet_type": "trifecta", "combination": [1, 2, 4], "kind": "refund"})
        line = d["settlement"]["lines"][0]
        line.update(status="refund", returned_jpy=300)
        d["settlement"].update(total_returned_jpy=1060, profit_jpy=560)

    doc = bad("review", refund)
    assert ok("review", doc) and review_errors(doc) == []
    assert not ok("review", bad("review", lambda d: d["result"]["payouts"].append({"bet_type": "trio", "combination": [1, 2, 4], "kind": "refund", "payout_per_100_jpy": 100})))


def test_settlement_requires_matching_approved_risk_decision():
    assert v.review_errors(load("review"), load("prediction_pre"), risk=None)
    assert v.review_errors(load("review"), load("prediction_pre"), risk=load("risk_decision_rejected"))


def test_review_of_wait_decision_uses_final_bets():
    pre_like_wait, dec = load("prediction_wait"), load("decision_bet")
    risk = bad("risk_decision", lambda d: d.update(subject_type="decision", subject_id=dec["decision_id"], subject_sha256=v.canonical_sha256(dec), total_stake_jpy=300))
    order = [4, 1, 2]
    rev = bad("review", lambda d: d.update(
        prediction_id=pre_like_wait["prediction_id"], prediction_sha256=v.canonical_sha256(pre_like_wait),
        decision_id=dec["decision_id"], decision_sha256=v.canonical_sha256(dec)))
    rev["result"].update(finish_order=order, payouts=[
        {"bet_type": "trifecta", "combination": [4, 1, 2], "kind": "win", "payout_per_100_jpy": 2500},
        {"bet_type": "trio", "combination": [1, 2, 4], "kind": "win", "payout_per_100_jpy": 380}])
    rev["outcome"]["bet_results"] = [{"bet_type": b["bet_type"], "combination": b["combination"], "hit": True} for b in dec["final_bets"]]
    rev["settlement"] = {"risk_decision_id": risk["risk_decision_id"], "risk_decision_sha256": v.canonical_sha256(risk),
        "lines": [{"bet_type": "trio", "combination": [1, 2, 4], "units": 1, "stake_jpy": 100, "status": "win", "returned_jpy": 380},
                  {"bet_type": "trifecta", "combination": [4, 1, 2], "units": 2, "stake_jpy": 200, "status": "win", "returned_jpy": 5000}],
        "total_stake_jpy": 300, "total_returned_jpy": 5380, "profit_jpy": 5080}
    assert ok("review", rev)
    assert v.review_errors(rev, pre_like_wait, dec, risk) == []
    assert v.review_errors(rev, pre_like_wait, None, risk)
    assert not ok("review", bad("review", lambda d: d.update(decision_id="SYN-DEC-BET")))


# --- evidence vocabulary
@pytest.mark.parametrize("field,boat,value", [
    ("entry_course", 1, 1), ("exhibition_time", 2, 6.8), ("exhibition_st", 3, 0.15),
    ("motor_top2_rate", 4, 40.0), ("motor_top3_rate", 5, 60.0),
    ("wind_direction", None, "SW"), ("wind_speed_mps", None, 3), ("wave_height_cm", None, 5),
    ("x-acme:custom_metric", None, "anything"),
])
def test_standard_and_extension_fields_accepted(field, boat, value):
    assert v.field_errors(field, boat, value, True) == []


@pytest.mark.parametrize("field,boat,value", [
    ("entry_course", 1, 7), ("entry_course", None, 1), ("exhibition_time", 1, "6.8"), ("exhibition_time", 1, 60),
    ("exhibition_st", 1, True), ("wind_direction", None, "UP"), ("wind_speed_mps", 1, 3), ("wind_speed_mps", None, -1),
    ("wave_height_cm", None, "high"), ("windspeed", None, 3), ("acme:metric", None, 1), ("x-acme:", None, 1),
])
def test_bad_fields_rejected(field, boat, value):
    assert v.field_errors(field, boat, value, True)


def test_evidence_uses_vocabulary():
    assert v.evidence_errors(bad("evidence", lambda d: d["observations"][2].update(field="windspeed")))
    assert v.evidence_errors(bad("evidence", lambda d: d["observations"][2].update(value=-3)))
    assert v.evidence_errors(bad("evidence_exhibition", lambda d: d["observations"][0].pop("boat")))
    assert not ok("evidence", bad("evidence", lambda d: d["observations"][2].update(field="Wind Speed")))
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0]["observation"].update(field="exhibition time")))
    assert v.prediction_errors(bad("prediction_wait", lambda d: d["wait_plan"][0]["observation"].update(field="unknown_field")))
