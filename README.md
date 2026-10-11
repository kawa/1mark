# 1mark

Prediction and evaluation framework for Japanese boat racing (kyotei), designed for stateless AI sessions and a mobile-first workflow.

- `protocols/` versioned procedures; `instructions/` session instructions
- `config/` versioned unit/stake config and evidence vocabulary
- `schemas/` JSON Schemas; `onemark/validate.py` deterministic validation
- `knowledge/validated/` vs `knowledge/proposals/`; `evidence/`; `tasks/`
- `examples/` synthetic data only (`*.synthetic.json`)

BOAT RACE racelist PoC: `python -m onemark.boatrace --date YYYYMMDD --jcd 02 --rno 1` (or the manual "BOAT RACE racelist ingestion" workflow).

Batch workflow: the "BOAT RACE batch ingestion" Action also builds one self-contained batch ZIP (artifact `batch-package-<date>-<jcd>.zip`, uploaded unwrapped; choose the venue by name, leave date blank for today in JST; the run summary links the artifact; GitHub Mobile download is unverified; upload it to a fresh AI conversation). Locally: `python -m onemark.workflow <batch-dir>`; see `instructions/stateless-session.md`.
Validate a revision end to end: `python -m onemark.workflow check-revision --baseline pred.json --revision rev.json [--state state.json]`.

Test: `pip install -r requirements.txt && pytest`
