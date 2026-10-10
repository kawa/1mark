"""Build a self-contained, stateless prediction package (ZIP) from validated Evidence.

Packaging only: no prediction logic, no AI calls. Reuses repository protocol, schema and knowledge files.
"""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from onemark import validate as v

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "prediction-v1.md"
SCHEMA = ROOT / "schemas" / "prediction.schema.json"
KNOWLEDGE_DIR = ROOT / "knowledge" / "validated"
PACKAGE_VERSION = "1.0.0"
REQUIRED_FILES = ("START_HERE.md", "protocol.md", "knowledge.yaml", "evidence.json", "task.json",
                  "prediction.schema.json", "manifest.json")
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

START_HERE = """# START HERE

You are a stateless AI session. This package is your complete task.

## Rules
1. Treat this package as the complete authoritative context. Do not rely on previous conversations or external memory.
2. Read the files in this order: `protocol.md`, `knowledge.yaml`, `evidence.json`, `task.json`.
3. Separate observations (`evidence.json` observations) from inferences and from decisions.
4. Never invent missing race data. Entries in `missing_data` are missing; leave them missing.
5. Follow the PRE / WAIT / SKIP definitions in `protocol.md` exactly. This file adds no prediction rules.
6. Produce complete combinations and units whenever the protocol requires them.
7. If data is insufficient, report UNAVAILABLE or INCOMPLETE as defined in the protocol. That is not SKIP.
8. Return one JSON document that validates against `prediction.schema.json`
   (protocol_version `{protocol_version}`, knowledge_version `{knowledge_version}`).
9. Explain the decision with evidence references (observation / inference ids from `evidence.json`).
10. Use only evidence observed at or before `prediction_cutoff` in `task.json`.

## Task
Race `{race_id}`, phase `{phase}`. See `task.json` for the objective and output requirements.
Timing: `evidence_cutoff` is the latest information available; it is not the betting deadline. If `betting_deadline` is null/unknown, `execution_allowed` is false: do not treat any output as executable.
Integrity: verify file hashes against `manifest.json`.
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PackageError(Exception):
    pass


def protocol_version(text: str) -> str:
    m = re.search(r"Protocol v(\d+\.\d+\.\d+)", text)
    if not m:
        raise PackageError("cannot determine protocol version from protocol file")
    return m.group(1)


def build_knowledge():
    """Return (knowledge.yaml text, version). No validated knowledge yet -> explicit 'none'."""
    files = sorted(p for p in KNOWLEDGE_DIR.glob("*") if p.is_file() and p.name != ".gitkeep")
    if not files:
        return "knowledge_version: none\nentries: []\n", "none"
    if len(files) == 1 and files[0].suffix in (".yaml", ".yml"):
        text = files[0].read_text(encoding="utf-8")
        m = re.search(r"^knowledge_version:\s*(\S+)", text, re.M)
        if not m:
            raise PackageError(f"{files[0].name} lacks knowledge_version")
        return text, m.group(1).strip("\"'")
    raise PackageError("unsupported knowledge layout: expected a single YAML file with knowledge_version")


def derive_evidence_cutoff(evidence):
    """Latest observation (collection) timestamp. Observations keep their original timestamps."""
    observed = [o["observed_at"] for o in evidence["observations"]]
    if not observed:
        raise PackageError("evidence has no observations; cannot derive evidence_cutoff")
    return max(observed, key=v.parse_ts)


def build_task(evidence, evidence_cutoff, betting_deadline, phase, scheduled_start=None):
    race_id = evidence["race_id"]
    task = {
        "schema_version": "1.0.0",
        "task_id": f"TASK-{race_id}-{phase}",
        "task_type": "predict",
        "race_id": race_id,
        "prediction_cutoff": evidence_cutoff,
        "evidence_cutoff": evidence_cutoff,
        "betting_deadline": betting_deadline,
        "execution_allowed": betting_deadline is not None,
        "synthetic": bool(evidence["synthetic"]),
        "prediction_phase": phase,
        "objective": f"Produce one prediction for race {race_id} following protocol.md, using only evidence.json.",
        "output_requirements": {
            "format": "single JSON document",
            "schema": "prediction.schema.json",
            "task_id": f"TASK-{race_id}-{phase}",
            "evidence_id": evidence["evidence_id"],
            "evidence_references": "cite observation/inference ids from evidence.json",
        },
    }
    if scheduled_start is not None:
        task["scheduled_start"] = scheduled_start
    return task


def _json(doc) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_files(evidence_path, evidence_cutoff=None, betting_deadline=None, phase="pre_race", scheduled_start=None):
    """Return {name: bytes}. Raises PackageError on any invalid or missing input."""
    try:
        raw = Path(evidence_path).read_bytes()
        evidence = json.loads(raw)
    except (OSError, ValueError) as e:
        raise PackageError(f"cannot read evidence: {e}")
    errors = v.schema_errors("evidence", evidence)
    if errors:
        raise PackageError("evidence schema errors: " + "; ".join(errors))
    errors = v.evidence_errors(evidence)
    if errors:
        raise PackageError("evidence errors: " + "; ".join(errors))
    evidence_cutoff = evidence_cutoff or derive_evidence_cutoff(evidence)
    task = build_task(evidence, evidence_cutoff, betting_deadline, phase, scheduled_start)
    errors = v.schema_errors("task", task) or v.leak_errors(task, evidence)
    if not errors and betting_deadline and v.parse_ts(evidence_cutoff) > v.parse_ts(betting_deadline):
        errors.append("evidence_cutoff is after betting_deadline")
    if errors:
        raise PackageError("task errors: " + "; ".join(errors))
    protocol = PROTOCOL.read_bytes()
    pver = protocol_version(protocol.decode("utf-8"))
    knowledge, kver = build_knowledge()
    files = {
        "protocol.md": protocol,
        "knowledge.yaml": knowledge.encode("utf-8"),
        "evidence.json": raw,
        "task.json": _json(task),
        "prediction.schema.json": SCHEMA.read_bytes(),
        "START_HERE.md": START_HERE.format(protocol_version=pver, knowledge_version=kver,
                                           race_id=task["race_id"], phase=phase).encode("utf-8"),
    }
    observed = [o["observed_at"] for o in evidence["observations"]]
    manifest = {
        "package_version": PACKAGE_VERSION,
        "race_id": task["race_id"],
        "prediction_phase": phase,
        "evidence_cutoff": evidence_cutoff,
        "betting_deadline": betting_deadline or "unknown",
        "execution_allowed": betting_deadline is not None,
        "protocol_version": pver,
        "knowledge_version": kver,
        "evidence_id": evidence["evidence_id"],
        "evidence_schema_version": evidence["schema_version"],
        "vocabulary_version": evidence["vocabulary_version"],
        "evidence_observed_at": {"earliest": min(observed), "latest": max(observed)} if observed else None,
        "sources": {
            "protocol": "protocols/prediction-v1.md",
            "schema": "schemas/prediction.schema.json",
            "knowledge": "knowledge/validated/",
            "evidence_urls": sorted({o["source_url"] for o in evidence["observations"]}),
        },
        "files": {name: {"sha256": sha256(data), "bytes": len(data)} for name, data in sorted(files.items())},
    }
    files["manifest.json"] = _json(manifest)
    return files


def write_zip(files, out_path):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])


def validate_zip(path):
    """Return a list of problems (empty if the package is complete and consistent)."""
    problems = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        missing = [n for n in REQUIRED_FILES if n not in names]
        if missing:
            return [f"missing files: {missing}"]
        data = {n: z.read(n) for n in names}
    try:
        manifest = json.loads(data["manifest.json"])
        evidence = json.loads(data["evidence.json"])
        task = json.loads(data["task.json"])
    except ValueError as e:
        return [f"malformed JSON: {e}"]
    for name in REQUIRED_FILES:
        if name == "manifest.json":
            continue
        entry = manifest.get("files", {}).get(name)
        if not entry or entry["sha256"] != sha256(data[name]) or entry["bytes"] != len(data[name]):
            problems.append(f"hash mismatch or missing manifest entry: {name}")
    if set(manifest.get("files", {})) != set(REQUIRED_FILES) - {"manifest.json"}:
        problems.append("manifest file list mismatch")
    if manifest.get("protocol_version") != protocol_version(data["protocol.md"].decode("utf-8")):
        problems.append("protocol version mismatch")
    if manifest.get("race_id") != task.get("race_id") or task.get("race_id") != evidence.get("race_id"):
        problems.append("race_id mismatch")
    problems += v.schema_errors("evidence", evidence) + v.schema_errors("task", task)
    problems += v.leak_errors(task, evidence)
    if b"knowledge_version: " + manifest.get("knowledge_version", "").encode() not in data["knowledge.yaml"]:
        problems.append("knowledge version mismatch")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence", required=True, help="validated Evidence JSON")
    ap.add_argument("--evidence-cutoff", help="ISO-8601 with offset; default: latest observed_at in Evidence")
    ap.add_argument("--betting-deadline", help="official deadline, ISO-8601 with offset; if absent it is 'unknown' and execution is prohibited")
    ap.add_argument("--scheduled-start", help="optional official start, ISO-8601 with offset")
    ap.add_argument("--out", required=True, help="output ZIP path")
    a = ap.parse_args(argv)
    try:
        files = build_files(a.evidence, a.evidence_cutoff, a.betting_deadline, scheduled_start=a.scheduled_start)
        write_zip(files, a.out)
    except PackageError as e:
        print(f"ERROR package: {e}", file=sys.stderr)
        return 2
    problems = validate_zip(a.out)
    if problems:
        print("ERROR package validation:\n" + "\n".join(problems), file=sys.stderr)
        return 3
    print(f"OK package {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
