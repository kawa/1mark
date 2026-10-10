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


def ok(kind, doc):
    return not v.schema_errors(kind, doc) and not (kind == "prediction" and v.prediction_errors(doc))


@pytest.mark.parametrize("schema", v.SCHEMAS.values(), ids=list(v.SCHEMAS))
def test_schemas_are_valid(schema):
    Draft202012Validator.check_schema(schema)


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


def test_duplicate_bets_and_bad_units_and_types():
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"].append(copy.deepcopy(d["bets"][0]))))
    for units in (0, -1, 1.5, 101):
        assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(units=units)))
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(bet_type="exacta")))


def test_prediction_has_no_stake_amounts():
    assert not ok("prediction", bad("prediction_pre", lambda d: d["bets"][0].update(stake_jpy=100)))


@pytest.mark.parametrize("stake", [0, 50, 150, 100.5])
def test_stake_multiple_of_100(stake):
    assert not ok("risk_decision", bad("risk_decision", lambda d: d["executions"][0].update(stake_jpy=stake)))


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
def test_risk_matches_locked_prediction():
    pre, risk = load("prediction_pre"), load("risk_decision")
    assert v.risk_errors(risk, pre) == []
    assert v.risk_errors(bad("risk_decision", lambda d: d["executions"][0].update(units=4, stake_jpy=400)), pre)
    assert v.risk_errors(bad("risk_decision", lambda d: d["executions"][0].update(stake_jpy=300)), pre)
    assert v.risk_errors(risk, bad("prediction_pre", lambda d: d["bets"][0].update(units=1)))
    assert v.risk_errors(risk, load("prediction_wait"))


def test_rejected_risk_decision_has_no_executions():
    assert not ok("risk_decision", bad("risk_decision", lambda d: d.update(decision="rejected")))


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
def test_review_computed_from_locked_prediction():
    pre, rev = load("prediction_pre"), load("review")
    assert v.review_errors(rev, pre) == []
    assert v.review_errors(bad("review", lambda d: d["outcome"]["bet_results"][0].update(hit=True)), pre)
    assert v.review_errors(rev, bad("prediction_pre", lambda d: d["bets"][0].update(units=1)))


@pytest.mark.parametrize("key", ["outcome", "prediction_error", "decision_quality", "protocol_compliance"])
def test_review_separates_four_assessments(key):
    assert not ok("review", bad("review", lambda d: d.pop(key)))


def test_learning_proposals_need_evidence_and_stay_proposed():
    assert not ok("learning_proposal", bad("learning_proposal", lambda d: d.update(evidence=[])))
    assert not ok("learning_proposal", bad("learning_proposal", lambda d: d.update(status="validated")))


def test_validated_knowledge_and_protocols_untouched_by_examples():
    assert [p.name for p in (ROOT / "knowledge" / "validated").iterdir()] == [".gitkeep"]
