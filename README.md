# 1mark

Prediction and evaluation framework for Japanese boat racing (kyotei), designed for stateless AI sessions and a mobile-first workflow.

- `protocols/` versioned procedures; `instructions/` session instructions
- `config/` versioned unit/stake config and evidence vocabulary
- `schemas/` JSON Schemas; `onemark/validate.py` deterministic validation
- `knowledge/validated/` vs `knowledge/proposals/`; `evidence/`; `tasks/`
- `examples/` synthetic data only (`*.synthetic.json`)

Test: `pip install -r requirements.txt && pytest`
