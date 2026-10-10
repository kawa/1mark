import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = {p.name.split(".")[0]: json.loads(p.read_text()) for p in (ROOT / "schemas").glob("*.json")}


def load(name):
    return json.loads((ROOT / "examples" / name).read_text())


def valid(kind, doc):
    return not list(Draft202012Validator(SCHEMAS[kind]).iter_errors(doc))


@pytest.mark.parametrize("schema", SCHEMAS.values())
def test_schemas_are_valid(schema):
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", sorted((ROOT / "examples").glob("*.synthetic.json")))
def test_examples_validate_and_are_synthetic(path):
    doc = json.loads(path.read_text())
    kind = path.name.split(".")[0] if "_" not in path.name.split(".")[0] or path.name.startswith("learning") else path.name.split("_")[0]
    kind = "learning_proposal" if kind == "learning" else kind
    assert doc["synthetic"] is True
    assert valid(kind, doc)


def test_pre_requires_finalized_bets_and_units():
    doc = load("prediction_pre.synthetic.json")
    for mutate in (
        lambda d: d.pop("risk_management"),
        lambda d: d["risk_management"].update(finalized=False),
        lambda d: d["risk_management"].update(bets=[]),
        lambda d: d["risk_management"]["bets"][0].pop("units"),
        lambda d: d["risk_management"]["bets"][0].update(units=0),
    ):
        bad = copy.deepcopy(doc)
        mutate(bad)
        assert not valid("prediction", bad)


def test_wait_requires_baseline_and_conditions():
    doc = load("prediction_wait.synthetic.json")
    for key in ("prediction", "change_conditions"):
        bad = copy.deepcopy(doc)
        del bad[key]
        assert not valid("prediction", bad)
    bad = copy.deepcopy(doc)
    bad["prediction"].pop("probability")
    assert not valid("prediction", bad)


@pytest.mark.parametrize("name", ["wait", "skip", "unavailable", "incomplete"])
def test_non_pre_statuses_have_no_risk_management(name):
    bad = load(f"prediction_{name}.synthetic.json")
    bad["risk_management"] = load("prediction_pre.synthetic.json")["risk_management"]
    assert not valid("prediction", bad)


def test_reason_and_missing_required():
    for name, key in (("prediction_skip", "reason"), ("prediction_unavailable", "reason"), ("prediction_incomplete", "missing")):
        bad = load(name + ".synthetic.json")
        del bad[key]
        assert not valid("prediction", bad)


def test_unknown_status_rejected():
    bad = load("prediction_skip.synthetic.json")
    bad["status"] = "MAYBE"
    assert not valid("prediction", bad)
