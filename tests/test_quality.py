import copy
import json
from pathlib import Path

from onemark import validate as v

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name):
    return json.loads((EXAMPLES / f"{name}.synthetic.json").read_text())


def prediction_for_race(race_id, prediction_id, reason_prefix):
    pred = copy.deepcopy(load("prediction_v11_pre"))
    pred["race_id"] = race_id
    pred["prediction_id"] = prediction_id
    pred["bet_rationale"]["selections"][0]["reason"] = (
        f"{reason_prefix}: boat 1's course position supports this combination."
    )
    pred["bet_rationale"]["exclusions"][0]["reason"] = (
        f"{reason_prefix}: the outside boats lack comparable current-series evidence."
    )
    pred["bet_rationale"]["allocation"] = (
        f"{reason_prefix}: the main scenario receives more units than the alternative."
    )
    pred["bet_rationale"]["total_units"]["reason"] = (
        f"{reason_prefix}: the total reflects the relative support for its scenarios."
    )
    pred["timing_rationale"] = (
        f"{reason_prefix}: waiting for exhibition data is not expected to change this baseline."
    )
    for assessment in pred["boat_assessments"]:
        assessment["rationale"] = (
            f"{reason_prefix}: boat {assessment['boat']} is assessed from its race-specific evidence."
        )
    for section, details in pred["evaluation"].items():
        if "summary" in details:
            details["summary"] = f"{reason_prefix}: {section} assessment uses this race's evidence."
        elif section == "primary_drivers":
            for driver in details:
                driver["influence"] = f"{reason_prefix}: evidence {driver['evidence_id']} affects the ranking."
    return pred


def test_quality_warnings_are_advisory_and_do_not_mutate_prediction():
    pred = copy.deepcopy(load("prediction_v11_pre"))
    before = copy.deepcopy(pred)
    digest = v.canonical_sha256(pred)

    warnings = v.prediction_quality_warnings(pred)

    assert any("race-specific explanation" in warning for warning in warnings)
    assert pred == before
    assert v.canonical_sha256(pred) == digest
    assert not v.schema_errors("prediction", pred)
    assert not v.prediction_errors(pred)


def test_numeric_claims_are_review_prompts_not_validation_failures():
    pred = copy.deepcopy(load("prediction_v11_pre"))
    pred["bet_rationale"]["total_units"]["reason"] = (
        "A score of 8.5 and a weight of 2.5 give this scenario priority."
    )
    pred["timing_rationale"] = "Choose PRE only above a threshold of 75."

    warnings = v.prediction_quality_warnings(pred)

    assert any("numerical scoring" in warning for warning in warnings)
    assert any("PRE/SKIP threshold" in warning for warning in warnings)
    assert not v.prediction_errors(pred)


def test_cross_race_warnings_do_not_apply_to_historical_protocol_versions():
    first = prediction_for_race("20000101-01-01", "PRED-1", "same rationale")
    second = prediction_for_race("20000101-01-02", "PRED-2", "same rationale")
    first["protocol_version"] = second["protocol_version"] = "1.0.0"

    assert v.batch_prediction_quality_warnings([("first.json", first), ("second.json", second)]) == []


def test_identical_rationales_across_races_are_flagged():
    first = prediction_for_race("20000101-01-01", "PRED-1", "Toda race 1")
    second = prediction_for_race("20000101-01-02", "PRED-2", "Toda race 2")
    generic = "The combination is supported by the available evidence."
    first["bet_rationale"]["selections"][0]["reason"] = generic
    second["bet_rationale"]["selections"][0]["reason"] = generic

    warnings = v.batch_prediction_quality_warnings([("first.json", first), ("second.json", second)])

    assert len(warnings) == 2
    assert all("identical explanation wording" in warning for _, warning in warnings)
    assert {source for source, _ in warnings} == {"first.json", "second.json"}


def test_race_specific_rationales_and_similar_bets_are_not_flagged():
    first = prediction_for_race("20000101-01-01", "PRED-1", "Toda race 1")
    second = prediction_for_race("20000101-01-02", "PRED-2", "Toda race 2")
    assert len(first["bets"]) == len(second["bets"])
    assert [bet["units"] for bet in first["bets"]] == [bet["units"] for bet in second["bets"]]

    warnings = v.batch_prediction_quality_warnings([("first.json", first), ("second.json", second)])

    assert warnings == []
    assert not v.prediction_errors(first)
    assert not v.prediction_errors(second)


def test_validator_cli_emits_quality_warnings_without_failing(tmp_path, capsys):
    first = copy.deepcopy(load("prediction_v11_pre"))
    second = copy.deepcopy(first)
    second["race_id"] = "20000101-01-02"
    second["prediction_id"] = "SYN-PRED-OTHER-RACE"
    for pred in (first, second):
        pred["bet_rationale"]["selections"][0]["reason"] = (
            "The combination is supported by the available evidence."
        )
    first_path = tmp_path / "prediction-1.json"
    second_path = tmp_path / "prediction-2.json"
    first_path.write_text(json.dumps(first))
    second_path.write_text(json.dumps(second))

    assert v.main([str(first_path), str(second_path)]) == 0
    captured = capsys.readouterr()
    assert "warning: human review: identical explanation wording" in captured.err
    assert captured.out == ""
