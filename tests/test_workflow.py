import copy
import json
import zipfile
from pathlib import Path

from onemark import validate as v
from onemark import workflow as wf

EX = Path(__file__).resolve().parent.parent / "examples"
load = lambda n: json.loads((EX / f"{n}.synthetic.json").read_text())
BASE = load("prediction_v11_pre")


def mut(name, fn):
    d = copy.deepcopy(load(name))
    fn(d)
    return d


def test_batch_all_12_races_pre_skip_or_failure():
    s = load("batch_summary")
    assert v.schema_errors("batch_summary", s) == [] and wf.batch_summary_errors(s) == []
    assert {r["status"] for r in s["races"] if r["processing"] == "PROCESSED"} <= {"PRE", "SKIP"}
    failed = [r for r in s["races"] if r["processing"] == "FAILED"]
    assert failed and all("status" not in r for r in failed)


def test_batch_failure_is_not_skip_and_missing_race_rejected():
    s = mut("batch_summary", lambda d: d["races"][2].update(status="SKIP"))
    assert any("not SKIP" in e for e in wf.batch_summary_errors(s))
    s = mut("batch_summary", lambda d: d["races"][11].update(race_id="20000101-01-01"))
    assert wf.batch_summary_errors(s)
    s = mut("batch_summary", lambda d: d["races"].pop())
    assert v.schema_errors("batch_summary", s)


def test_batch_summary_matches_predictions():
    s = load("batch_summary")
    assert wf.batch_summary_errors(s, {BASE["race_id"]: BASE}) == []
    other = copy.deepcopy(BASE)
    other["bets"][0]["units"] = 9
    assert wf.batch_summary_errors(s, {BASE["race_id"]: other})


def test_exhibition_time_official_estimated_unknown():
    assert wf.exhibition_time(official="2000-01-01T11:20:00+09:00")["kind"] == "official"
    e = wf.exhibition_time(betting_deadline="2000-01-01T11:00:00+09:00")
    assert e["kind"] == "estimated_from_betting_deadline" and e["offset_minutes"] == wf.DEFAULT_EXHIBITION_OFFSET_MINUTES
    assert v.parse_ts(e["time"]) == v.parse_ts("2000-01-01T10:50:00+09:00")
    assert wf.exhibition_time(betting_deadline="2000-01-01T11:00:00+09:00", offset_minutes=15)["offset_minutes"] == 15
    assert wf.exhibition_time(official="2000-01-01T11:20:00+09:00", betting_deadline="2000-01-01T11:30:00+09:00")["kind"] == "official"
    assert wf.exhibition_time() == {"kind": "unknown"}


def test_exhibition_time_labelling_enforced():
    s = mut("batch_summary", lambda d: d["races"][0]["exhibition_time"].update(time="2000-01-01T10:00:00+09:00"))
    assert any("minus offset" in e for e in wf.batch_summary_errors(s))
    s = mut("batch_summary", lambda d: d["races"][3]["exhibition_time"].update(time="2000-01-01T10:00:00+09:00"))
    assert any("unknown exhibition time" in e for e in wf.batch_summary_errors(s))
    s = mut("batch_summary", lambda d: d["races"][1]["exhibition_time"].pop("time"))
    assert any("required for official" in e for e in wf.batch_summary_errors(s))


def test_wait_does_not_modify_baseline():
    st = load("workflow_state_wait")
    before = json.dumps(BASE, sort_keys=True)
    assert v.schema_errors("workflow_state", st) == [] and wf.workflow_state_errors(st, BASE) == []
    assert json.dumps(BASE, sort_keys=True) == before and BASE["status"] == "PRE"
    changed = copy.deepcopy(BASE)
    changed["bets"][0]["units"] = 4
    assert any("sha256" in e for e in wf.workflow_state_errors(st, changed))
    skip = load("prediction_v11_skip")
    assert any("must be PRE" in e for e in wf.workflow_state_errors(st, skip))


def test_switch_wait_back_to_pre():
    st = mut("workflow_state_wait", lambda d: (d["history"].append({"state": "WAIT_SWITCHED_TO_PRE", "at": "2000-01-01T10:30:00+09:00"}),
                                               d.update(state="WAIT_SWITCHED_TO_PRE")))
    assert wf.workflow_state_errors(st, BASE) == []
    bad = mut("workflow_state_wait", lambda d: d.update(state="UNDECIDED"))
    assert wf.workflow_state_errors(bad)
    direct = mut("workflow_state_wait", lambda d: (d["history"].__setitem__(0, {"state": "PRE_PURCHASED", "at": d["history"][0]["at"]})))
    assert wf.workflow_state_errors(direct)


def test_final_state_requires_revision_id():
    st = mut("workflow_state_wait", lambda d: (d["history"].append({"state": "FINAL_PURCHASED", "at": "2000-01-01T10:30:00+09:00"}),
                                               d.update(state="FINAL_PURCHASED")))
    assert any("revision_id" in e for e in wf.workflow_state_errors(st))


def test_revision_changed_bets_and_units():
    r = load("exhibition_revision_changed")
    assert v.schema_errors("exhibition_revision", r) == [] and wf.revision_errors(r, BASE) == []
    assert r["changes"]["added"] and r["changes"]["units_changed"]
    r["changes"]["added"] = []
    assert any("changes do not match" in e for e in wf.revision_errors(r, BASE))


def test_revision_no_change_retained():
    r = load("exhibition_revision_retained")
    assert wf.revision_errors(r, BASE) == []
    assert not any(r["changes"].values())
    r["prediction"] = BASE
    assert wf.revision_errors(r, BASE)


def test_revised_without_changes_must_explain():
    r = mut("exhibition_revision_changed", lambda d: None)
    p = r["prediction"]
    p["bets"], p["bet_rationale"] = BASE["bets"], BASE["bet_rationale"]
    r["changes"] = wf.diff_bets(wf._bets(BASE), wf._bets(p))
    assert any("unchanged" in e for e in wf.revision_errors(r, BASE))
    r["explanation"]["unchanged"] = "SYNTHETIC: same"
    assert wf.revision_errors(r, BASE) == []


def test_revision_concluding_skip():
    def f(d):
        p = d["prediction"]
        s = copy.deepcopy(load("prediction_v11_skip"))
        s.update(prediction_id="SYN-PRED-REV-SKIP", race_id=BASE["race_id"])
        d["prediction"] = s
        d["changes"] = wf.diff_bets(wf._bets(BASE), {})
    r = mut("exhibition_revision_changed", f)
    assert wf.revision_errors(r, BASE) == [] and len(r["changes"]["removed"]) == 1


def test_revision_reference_and_status_rules():
    r = mut("exhibition_revision_changed", lambda d: d["baseline"].update(prediction_sha256="1" * 64))
    assert any("sha256" in e for e in wf.revision_errors(r, BASE))
    r = mut("exhibition_revision_changed", lambda d: d["prediction"].update(prediction_id=BASE["prediction_id"]))
    assert any("new prediction_id" in e for e in wf.revision_errors(r, BASE))
    r = mut("exhibition_revision_changed", lambda d: d["prediction"].update(status="WAIT"))
    assert wf.revision_errors(r, BASE)


def test_missing_and_ambiguous_exhibition_data():
    r = mut("exhibition_revision_retained", lambda d: d.update(exhibition_observations=[], missing_observations=["exhibition_time"]))
    assert any("at least one exhibition observation" in e for e in wf.revision_errors(r, BASE))
    r = mut("exhibition_revision_retained", lambda d: d["exhibition_observations"].append({"field": "bogus", "value": 1, "source": "SYN"}))
    assert any("unknown field" in e for e in wf.revision_errors(r, BASE))
    c = load("exhibition_revision_clarification")
    assert v.schema_errors("exhibition_revision", c) == [] and wf.revision_errors(c) == []
    assert wf.revision_errors(mut("exhibition_revision_clarification", lambda d: d.pop("candidate_race_ids")))
    assert wf.revision_errors(mut("exhibition_revision_clarification", lambda d: d.update(prediction=BASE)))


def test_historical_predictions_unchanged():
    for n in ("prediction_pre", "prediction_wait", "prediction_skip", "prediction_v11_wait"):
        assert v.validate_file(EX / f"{n}.synthetic.json") == []
    assert "WAIT" in v.SCHEMAS["prediction"]["properties"]["status"]["enum"]


def test_batch_zip(tmp_path):
    from onemark import package as p
    for n in (1, 2):
        d = tmp_path / f"race-{n:02d}"
        d.mkdir()
        p.write_zip(p.build_files(EX / "evidence.synthetic.json"), d / f"prediction-package-x{n}.zip")
    out = tmp_path / "b.zip"
    m = wf.build_batch_zip(tmp_path, out)
    z = zipfile.ZipFile(out)
    assert set(wf.BATCH_FILES) <= set(z.namelist()) and len(m["missing_races"]) == 10
    text = z.read("START_HERE.md").decode()
    assert "Never report it as SKIP" in text and "WAIT_SWITCHED_TO_PRE" in text


def test_direct_pre_purchase_is_terminal_without_revision():
    st = load("workflow_state_pre_purchased")
    assert v.schema_errors("workflow_state", st) == [] and wf.workflow_state_errors(st, BASE) == []
    assert "revision_id" not in json.dumps(st)
    more = copy.deepcopy(st)
    more["history"].append({"state": "WAIT_SELECTED", "at": "2000-01-01T10:10:00+09:00"})
    more["state"] = "WAIT_SELECTED"
    assert any("invalid transition" in e for e in wf.workflow_state_errors(more))


def test_wait_then_revision_then_final():
    st, rev = load("workflow_state_final_purchased"), load("exhibition_revision_changed")
    assert wf.check_revision(BASE, rev, st) == []
    wrong = mut("workflow_state_final_purchased", lambda d: d["history"][-1].update(revision_id="OTHER"))
    assert any("revision_id does not match" in e for e in wf.check_revision(BASE, rev, wrong))
    assert any("no FINAL_" in e for e in wf.check_revision(BASE, rev, load("workflow_state_wait")))
    assert any("prior WAIT_SELECTED" in e for e in wf.check_revision(BASE, rev, mut(
        "workflow_state_final_purchased", lambda d: d["history"].pop(1))) )


def test_check_revision_cli(tmp_path, capsys):
    def f(n, d):
        (tmp_path / n).write_text(json.dumps(d))
        return str(tmp_path / n)

    args = ["check-revision", "--baseline", f("b.json", BASE), "--revision", f("r.json", load("exhibition_revision_changed")),
            "--state", f("s.json", load("workflow_state_final_purchased"))]
    assert wf.main(args) == 0 and "OK" in capsys.readouterr().out
    tampered = mut("exhibition_revision_changed", lambda d: d["changes"]["added"].clear())
    args[4] = f("r2.json", tampered)
    assert wf.main(args) == 1 and "changes do not match" in capsys.readouterr().out
    other = copy.deepcopy(BASE)
    other["bets"][0]["units"] = 9
    args[2] = f("b2.json", other)
    args[4] = f("r.json", load("exhibition_revision_changed"))
    assert wf.main(args) == 1
    assert wf.main(["check-revision", "--baseline", "/nonexistent", "--revision", "x"]) == 1
