# 1mark

Prediction and evaluation framework for Japanese boat racing (kyotei), designed for stateless AI sessions and a mobile-first workflow.

- `protocols/` versioned procedures; `instructions/` session instructions
- `config/` versioned unit/stake config and evidence vocabulary
- `schemas/` JSON Schemas; `onemark/validate.py` deterministic validation
- `knowledge/validated/` vs `knowledge/proposals/`; `evidence/`; `tasks/`
- `examples/` synthetic data only (`*.synthetic.json`)

BOAT RACE racelist PoC: `python -m onemark.boatrace --date YYYYMMDD --jcd 02 --rno 1` (or the manual "BOAT RACE racelist ingestion" workflow).

Batch workflow: `python -m onemark.workflow <batch-dir>` builds one batch ZIP (PRE for all races, user-selected WAIT, exhibition revisions); see `instructions/stateless-session.md`.

Test: `pip install -r requirements.txt && pytest`
