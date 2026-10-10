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


@pytest.mark.parametrize("key", ["step_id", "observation", "decision_threshold", "expected_operations"])
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


def test_plan_and_decision_share_operation_vocabulary():
    plan_ops = load("prediction_wait")["wait_plan"][0]["expected_operations"]
    changes = load("decision_bet")["changes"]
    assert {o["action"] for o in plan_ops} == {c["action"] for c in changes} == {"add", "remove", "change_units"}
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0]["expected_operations"][0].update(action="rename")))
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0]["expected_operations"][2].update(units=3)))
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0]["expected_operations"][0].pop("units")))
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0].update(expected_operations=[])))


def test_plan_and_executed_changes_are_separate_records():
    wait, dec = load("prediction_wait"), load("decision_bet")
    assert "changes" not in wait and "wait_plan" not in dec
    assert not ok("decision", bad("decision_bet", lambda d: d["changes"][0].pop("plan_step_ids")))
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][0].update(plan_step_ids=["step-9"])))
    assert decision_errors(bad("decision_bet", lambda d: d["changes"][0].update(plan_step_ids=[]))) == []  # unplanned is explicit
    assert v.prediction_errors(bad("prediction_wait", lambda d: d["wait_plan"].append(copy.deepcopy(d["wait_plan"][0]))))
    assert v.prediction_errors(bad("prediction_wait", lambda d: d["wait_plan"][0]["expected_operations"][0].update(combination=[1, 2, 4])))


# --- unresolved WAIT
def test_unresolved_wait_is_incomplete_not_skip():
    inc = load("decision_incomplete")
    assert ok("decision", inc) and decision_errors(inc) == []
    assert "evidence_id" not in inc and not inc["changes"]
    assert not ok("decision", bad("decision_incomplete", lambda d: d.pop("missing")))
    assert not ok("decision", bad("decision_incomplete", lambda d: d.update(final_bets=load("decision_bet")["final_bets"])))
    assert not ok("decision", bad("decision_incomplete", lambda d: d.update(changes=load("decision_bet")["changes"])))
    assert decision_errors(bad("decision_incomplete", lambda d: d.update(locked_at="2000-01-01T14:00:00+09:00")))  # before cutoff


def test_incomplete_decision_allows_no_betting():
    dec = load("decision_incomplete")
    risk = bad("risk_decision", lambda d: d.update(subject_type="decision", subject_id=dec["decision_id"], subject_sha256=v.canonical_sha256(dec), total_stake_jpy=100))
    assert v.risk_errors(risk, dec)


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
    assert review_errors(bad("review", lambda d: d["result"]["payouts"].pop(1)))  # hit, confirmed, but no payout
    assert not ok("review", bad("review", lambda d: d["result"]["payouts"][0].pop("payout_per_100_jpy")))
    assert not ok("review", bad("review", lambda d: d["result"].pop("payouts")))


def test_settlement_requires_matching_approved_risk_decision():
    assert v.review_errors(load("review"), load("prediction_pre"), risk=None)
    assert v.review_errors(load("review"), load("prediction_pre"), risk=load("risk_decision_rejected"))


# --- refunds
def refunded(*combos, **extra):
    return {"status": "confirmed", "race_wide": False, "source_url": "https://example.invalid/r",
            "refunded": [{"bet_type": t, "combination": c} for t, c in combos], **extra}


def settle_for(refund_info, payouts=None):
    pre, review = load("prediction_pre"), load("review")
    review["result"]["refund_info"] = refund_info
    if payouts is not None:
        review["result"]["payouts"] = payouts
    return v.settle(pre, review["result"], v.load_units_config())


WIN = {"bet_type": "trio", "combination": [1, 2, 4], "payout_per_100_jpy": 380}


def test_partial_refund_returns_only_refunded_stake():
    lines, reasons, errors = settle_for(refunded(("trifecta", [1, 2, 4])), [WIN])
    assert (reasons, errors) == ([], [])
    assert [(x["status"], x["returned_jpy"]) for x in lines] == [("refund", 300), ("win", 760)]


def test_race_wide_refund_returns_all_stakes():
    lines, reasons, errors = settle_for(refunded(race_wide=True), [])
    assert (reasons, errors) == ([], [])
    assert [(x["status"], x["returned_jpy"]) for x in lines] == [("refund", 300), ("refund", 200)]


def test_multiple_refunded_combinations():
    lines, *_ = settle_for(refunded(("trifecta", [1, 2, 4]), ("trio", [1, 2, 4])), [])
    assert all(x["status"] == "refund" for x in lines)


def test_confirmed_non_refund_makes_miss():
    lines, reasons, _ = settle_for(refunded(), [WIN])
    assert lines[0]["status"] == "miss" and reasons == []


@pytest.mark.parametrize("status", ["unavailable", "incomplete"])
def test_unknown_refund_status_is_never_a_loss(status):
    info = {"status": status, "race_wide": False, "refunded": []}
    lines, reasons, errors = settle_for(info, [WIN])
    assert errors == [] and reasons
    assert lines[0]["status"] == "unresolved" and "returned_jpy" not in lines[0]
    assert lines[1]["status"] == "win"  # an official payout is certain


def test_incomplete_refund_info_keeps_known_refunds():
    info = {"status": "incomplete", "race_wide": False, "refunded": [{"bet_type": "trifecta", "combination": [1, 2, 4]}]}
    lines, reasons, _ = settle_for(info, [WIN])
    assert lines[0]["status"] == "refund" and reasons == []


def test_refund_info_schema_rules():
    base = load("review")
    assert not ok("review", bad("review", lambda d: d["result"].update(refund_info={"status": "unavailable", "race_wide": True, "refunded": []})))
    assert not ok("review", bad("review", lambda d: d["result"].update(refund_info={"status": "unavailable", "race_wide": False, "refunded": [{"bet_type": "trio", "combination": [1, 2, 4]}]})))
    assert not ok("review", bad("review", lambda d: d["result"]["refund_info"].pop("source_url")))
    assert not ok("review", bad("review", lambda d: d["result"].pop("refund_info")))
    assert not ok("review", bad("review", lambda d: d["result"]["refund_info"].update(affected_boats=[7])))
    assert base["result"]["refund_info"]["status"] == "confirmed"


def test_settlement_unresolved_when_refund_unknown():
    rev = load("review")
    rev["result"]["refund_info"] = {"status": "unavailable", "race_wide": False, "refunded": []}
    # claiming a resolved result with the miss counted is rejected
    assert v.review_errors(rev, load("prediction_pre"), risk=load("risk_decision"))
    doc = load("lifecycle_pre_refund_unavailable_unresolved")
    assert doc["review"]["settlement"]["status"] == "unresolved"
    assert "profit_jpy" not in doc["review"]["settlement"]
    assert v.validate_lifecycle(doc) == []
    resolved = copy.deepcopy(doc)
    resolved["review"]["settlement"].update(status="resolved", total_returned_jpy=760, profit_jpy=260)
    del resolved["review"]["settlement"]["unresolved_reason"]
    resolved["review"]["settlement"]["lines"][0].update(status="miss", returned_jpy=0)
    assert v.validate_lifecycle(resolved)


def test_refund_conflicting_with_payout_rejected():
    refund_and_win = copy.deepcopy(load("review"))
    refund_and_win["result"]["refund_info"] = refunded(("trio", [1, 2, 4]))
    assert v.result_errors(refund_and_win["result"])


# --- single lifecycle entry point
LIFECYCLES = sorted(EX.glob("lifecycle_*.json"), key=lambda p: p.name)


def life(name):
    return load(name)


def test_lifecycle_fixtures_cover_every_path():
    names = {p.name.split(".")[0] for p in LIFECYCLES}
    assert names >= {"lifecycle_pre_win_settled", "lifecycle_wait_bet_partial_refund", "lifecycle_wait_skip",
                     "lifecycle_wait_incomplete", "lifecycle_pre_race_wide_refund",
                     "lifecycle_pre_refund_unavailable_unresolved", "lifecycle_skip"}


@pytest.mark.parametrize("path", LIFECYCLES, ids=lambda p: p.name)
def test_lifecycle_fixtures_pass_single_entry_point(path):
    assert v.validate_lifecycle(json.loads(path.read_text())) == []
    assert v.validate_file(path) == []


def mutated(name, mutate):
    doc = copy.deepcopy(life(name))
    mutate(doc)
    return v.validate_lifecycle(doc)


def test_lifecycle_runs_schema_validation_on_every_record():
    for key in ("task", "prediction", "decision", "risk_decision", "review"):
        name = "lifecycle_wait_bet_partial_refund"
        assert mutated(name, lambda d: d[key].update(schema_version="9")), key
    assert mutated("lifecycle_pre_win_settled", lambda d: d["learning_proposals"][0].update(status="validated"))
    assert mutated("lifecycle_pre_win_settled", lambda d: d["evidence"][0]["observations"][0].pop("source_url"))


def test_lifecycle_runs_cross_document_checks():
    n = "lifecycle_wait_bet_partial_refund"
    assert mutated(n, lambda d: d["prediction"]["bets"][0].update(units=9))  # baseline changed after reference
    assert mutated(n, lambda d: d.pop("decision"))
    assert mutated(n, lambda d: d["decision"]["final_bets"].pop())
    assert mutated(n, lambda d: d["decision"].update(locked_at="2000-01-01T14:55:00+09:00"))
    assert mutated(n, lambda d: d["evidence"][1]["observations"][0].update(phase="post_race"))
    assert mutated(n, lambda d: d["risk_decision"].update(total_stake_jpy=200))
    assert mutated(n, lambda d: d["review"]["settlement"]["lines"][1].update(returned_jpy=1))
    assert mutated(n, lambda d: d["review"].update(decision_sha256="0" * 64))
    assert mutated(n, lambda d: d["task"].update(race_id="20000101-01-02"))
    assert mutated(n, lambda d: d["review"]["result"]["refund_info"].update(refunded=[]))  # partial refund erased
    assert mutated(n, lambda d: d.pop("risk_decision"))
    assert mutated(n, lambda d: d["task"].update(synthetic=False))
    assert mutated("lifecycle_pre_win_settled", lambda d: d["learning_proposals"][0]["evidence"][0].update(review_id="SYN-REV-X"))
    assert mutated("lifecycle_pre_win_settled", lambda d: d["review"].update(learning_proposal_ids=[]))
    assert mutated("lifecycle_pre_win_settled", lambda d: d.update(decision=life("lifecycle_wait_skip")["decision"]))
    assert mutated("lifecycle_skip", lambda d: d.update(risk_decision=life("lifecycle_pre_win_settled")["risk_decision"]))


def test_lifecycle_wait_without_decision_is_not_valid():
    assert any("INCOMPLETE" in e for e in mutated("lifecycle_wait_incomplete", lambda d: d.pop("decision")))


def test_lifecycle_wait_incomplete_has_no_betting():
    doc = life("lifecycle_wait_incomplete")
    assert "risk_decision" not in doc and "review" not in doc
    assert mutated("lifecycle_wait_incomplete", lambda d: d.update(risk_decision=life("lifecycle_pre_win_settled")["risk_decision"]))


def test_main_entry_point_cli(tmp_path, capsys):
    assert v.main([str(p) for p in LIFECYCLES]) == 0
    bad_doc = life("lifecycle_pre_win_settled")
    bad_doc["risk_decision"]["total_stake_jpy"] = 100
    path = tmp_path / "lifecycle_bad.json"
    path.write_text(json.dumps(bad_doc))
    assert v.main([str(path)]) == 1
    assert "total_stake_jpy" in capsys.readouterr().out


# --- evidence vocabulary
@pytest.mark.parametrize("field,boat,value", [
    ("entry_course", 1, 1), ("exhibition_time", 2, 6.8), ("exhibition_st", 3, 0.15),
    ("motor_top2_rate", 4, 40.0), ("motor_top3_rate", 5, 60.0),
    ("wind_direction", None, "SW"), ("wind_speed_mps", None, 3), ("wave_height_cm", None, 5),
    ("x-acme:custom_metric", None, "anything"),
    # provisional ranges never reject a valid observation
    ("entry_course", 1, 7), ("exhibition_time", 1, 60), ("wind_speed_mps", None, -1), ("wind_direction", None, "UP"),
])
def test_standard_and_extension_fields_accepted(field, boat, value):
    assert v.field_errors(field, boat, value, True) == []


@pytest.mark.parametrize("field,boat,value", [
    ("entry_course", None, 1), ("exhibition_time", 1, "6.8"), ("exhibition_st", 1, True),
    ("wind_speed_mps", 1, 3), ("wave_height_cm", None, "high"), ("windspeed", None, 3),
    ("acme:metric", None, 1), ("x-acme:", None, 1),
])
def test_bad_fields_rejected(field, boat, value):
    assert v.field_errors(field, boat, value, True)


def test_provisional_constraints_only_warn_and_raw_value_is_kept():
    assert v.load_vocabulary()["status"] == "provisional"
    assert v.field_warnings("wind_direction", "UP") and v.field_warnings("wind_speed_mps", -1)
    assert v.field_warnings("wind_speed_mps", 3) == [] and v.field_warnings("x-a:b", 1) == []
    odd = bad("evidence", lambda d: d["observations"][2].update(value=-3, raw_value="-3"))
    assert ok("evidence", odd) and v.evidence_warnings(odd)
    assert not ok("evidence", bad("evidence", lambda d: d["observations"][0].pop("raw_value")))
    assert bad("evidence", lambda d: d["observations"][2].update(raw_value="2.0 m/s"))["observations"][2]["raw_value"] == "2.0 m/s"


def test_evidence_uses_vocabulary():
    assert v.evidence_errors(bad("evidence", lambda d: d["observations"][2].update(field="windspeed")))
    assert v.evidence_errors(bad("evidence", lambda d: d["observations"][2].update(value="fast")))
    assert v.evidence_errors(bad("evidence_exhibition", lambda d: d["observations"][0].pop("boat")))
    assert not ok("evidence", bad("evidence", lambda d: d["observations"][2].update(field="Wind Speed")))
    assert not ok("prediction", bad("prediction_wait", lambda d: d["wait_plan"][0]["observation"].update(field="exhibition time")))
    assert v.prediction_errors(bad("prediction_wait", lambda d: d["wait_plan"][0]["observation"].update(field="unknown_field")))


# --- Protocol v1.1 decision discipline
def v11(name, mutate=None):
    d = copy.deepcopy(load(f"prediction_v11_{name}"))
    if mutate:
        mutate(d)
    return d


def v11_errors(d):
    return v.schema_errors("prediction", d) or v.prediction_errors(d)


def test_v11_examples_valid():
    for n in ("pre", "wait", "skip"):
        assert not v11_errors(v11(n))


def test_v11_missing_exhibition_alone_does_not_force_wait():
    pre = v11("pre")
    assert pre["status"] == "PRE" and "wait_plan" not in pre
    assert not v11_errors(pre)
    assert v11_errors(v11("pre", lambda d: d.pop("timing_rationale")))


def test_v11_no_fixed_bet_count_or_units():
    def one(d):
        d["bets"] = d["bets"][:1]
        d["bets"][0]["units"] = 7
        d["bet_rationale"]["selections"] = d["bet_rationale"]["selections"][:1]
        d["bet_rationale"]["total_units"]["units"] = 7

    assert not v11_errors(v11("pre", one))


def test_v11_bet_rationale_must_match_bets():
    assert v11_errors(v11("pre", lambda d: d["bet_rationale"]["total_units"].update(units=99)))
    assert v11_errors(v11("pre", lambda d: d["bet_rationale"]["selections"].clear()))
    assert v11_errors(v11("pre", lambda d: d.pop("bet_rationale")))


@pytest.mark.parametrize("field", ["uncertainty", "why_relevant", "expected_change", "materiality"])
def test_v11_wait_requires_evidence_backed_reasoning(field):
    assert v11_errors(v11("wait", lambda d: d["wait_plan"][0].pop(field)))


def test_v11_unsupported_numeric_threshold_rejected():
    def numeric(d):
        d["wait_plan"][0]["decision_threshold"] = "boat 1 exhibition time slower than 6.90"
        d["wait_plan"][0].pop("threshold_basis")

    assert v11_errors(v11("wait", numeric))


def test_v11_numeric_threshold_basis_rules():
    def basis(b):
        def m(d):
            d["wait_plan"][0]["decision_threshold"] = "slower than 6.90"
            d["wait_plan"][0]["threshold_basis"] = b
        return m

    assert not v11_errors(v11("wait", basis({"kind": "unvalidated_hypothesis", "automatic": False})))
    assert v11_errors(v11("wait", basis({"kind": "unvalidated_hypothesis", "automatic": True})))
    assert v11_errors(v11("wait", basis({"kind": "validated_knowledge"})))
    assert not v11_errors(v11("wait", basis({"kind": "protocol_rule", "ref": "prediction-v1.md#thresholds"})))


def test_v11_skip_requires_completed_analysis_and_no_bets():
    assert v11_errors(v11("skip", lambda d: d["skip_analysis"].pop("completed_analysis")))
    assert v11_errors(v11("skip", lambda d: d.update(bets=[{"bet_type": "trio", "combination": [1, 2, 3], "units": 1}])))


def test_v11_evaluation_required_and_evidence_ids_checked():
    assert v11_errors(v11("pre", lambda d: d.pop("evaluation")))
    assert v11_errors(v11("pre", lambda d: d["evaluation"].pop("scenarios")))
    ev = json.loads((EX / "evidence.synthetic.json").read_text())
    task = load("task")
    bad_pred = v11("pre", lambda d: d["evaluation"]["primary_drivers"][0].update(evidence_id="nope"))
    assert any("unknown evidence id" in e for e in v.prediction_integrity_errors(task, ev, bad_pred))


def test_v10_records_unchanged_by_v11_rules():
    assert not v11_errors(load("prediction_pre"))
    assert not v11_errors(load("prediction_wait"))
