import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from onemark import package as p
from onemark import validate as v

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "examples" / "evidence.synthetic.json"
START, CUTOFF = "2000-01-01T15:00:00+09:00", "2000-01-01T14:50:00+09:00"


def build(tmp_path, ev=EV, cutoff=CUTOFF):
    out = tmp_path / "pkg.zip"
    p.write_zip(p.build_files(ev, START, cutoff), out)
    return out


def test_complete_and_valid(tmp_path):
    out = build(tmp_path)
    assert set(zipfile.ZipFile(out).namelist()) == set(p.REQUIRED_FILES)
    assert p.validate_zip(out) == []


def test_manifest_hashes_and_versions(tmp_path):
    z = zipfile.ZipFile(build(tmp_path))
    m = json.loads(z.read("manifest.json"))
    for name, e in m["files"].items():
        assert e["sha256"] == p.sha256(z.read(name))
    assert z.read("protocol.md") == (ROOT / "protocols/prediction-v1.md").read_bytes()
    assert m["protocol_version"] == "1.0.0" and m["knowledge_version"] == "none"
    assert f"protocol_version `{m['protocol_version']}`" in z.read("START_HERE.md").decode()


def test_tamper_detected(tmp_path):
    out = build(tmp_path)
    files = {n: zipfile.ZipFile(out).read(n) for n in p.REQUIRED_FILES}
    files["protocol.md"] += b"x"
    bad = tmp_path / "bad.zip"
    p.write_zip(files, bad)
    assert any("hash" in e for e in p.validate_zip(bad))


def test_missing_file_detected(tmp_path):
    files = p.build_files(EV, START, CUTOFF)
    del files["task.json"]
    bad = tmp_path / "bad.zip"
    p.write_zip(files, bad)
    assert "missing" in p.validate_zip(bad)[0]


def test_deterministic(tmp_path):
    a, b = build(tmp_path / "a"), build(tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("content", ["not json", "{}", '{"race_id": "x"}'])
def test_bad_evidence(tmp_path, content):
    f = tmp_path / "e.json"
    f.write_text(content)
    with pytest.raises(p.PackageError):
        p.build_files(f, START, CUTOFF)


def test_missing_evidence_file(tmp_path):
    with pytest.raises(p.PackageError):
        p.build_files(tmp_path / "nope.json", START, CUTOFF)


def test_post_cutoff_evidence_rejected(tmp_path):
    with pytest.raises(p.PackageError, match="leaked"):
        p.build_files(EV, START, "1999-12-31T00:00:00+09:00")


def test_post_race_phase_rejected(tmp_path):
    ev = json.loads(EV.read_text())
    ev["observations"][0]["phase"] = "post_race"
    f = tmp_path / "e.json"
    f.write_text(json.dumps(ev))
    with pytest.raises(p.PackageError):
        p.build_files(f, START, CUTOFF)


def test_schema_and_example_compat(tmp_path):
    z = zipfile.ZipFile(build(tmp_path))
    assert v.schema_errors("task", json.loads(z.read("task.json"))) == []
    schema = json.loads(z.read("prediction.schema.json"))
    assert schema == json.loads((ROOT / "schemas/prediction.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    ex = json.loads((ROOT / "examples/prediction_pre.synthetic.json").read_text())
    assert v.schema_errors("prediction", ex) == []


def test_cli(tmp_path, capsys):
    out = tmp_path / "x.zip"
    assert p.main(["--evidence", str(EV), "--scheduled-start", START, "--prediction-cutoff", CUTOFF, "--out", str(out)]) == 0
    assert p.main(["--evidence", str(tmp_path / "no"), "--scheduled-start", START, "--prediction-cutoff", CUTOFF, "--out", str(out)]) == 2
