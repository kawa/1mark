# Prediction Protocol v1.0.0

## Layers (never mix)
- **Protocol**: this versioned procedure (`protocols/`). Changes only via PR.
- **Knowledge**: validated lessons (`knowledge/validated/`). Only humans promote items here.
- **Evidence**: sourced, dated facts (`evidence/`, see `schemas/evidence.schema.json`).
- **Task**: the single match/event being predicted (`tasks/`).

## Statuses
- **PRE**: prediction complete and risk management finalized: bets with odds and units > 0, `finalized: true`.
- **WAIT**: a complete baseline prediction exists, plus explicit `change_conditions` (condition -> effect). No bets.
- **SKIP**: deliberately not predicted; `reason` required.
- **UNAVAILABLE**: required data/event cannot be obtained; `reason` required.
- **INCOMPLETE**: session could not finish; `missing` lists what is lacking.

## Rules
1. Prediction (outcome, probability, rationale) is produced first, without reference to stakes.
2. Risk management (`risk_management`: bets, odds, units) is a separate step and only exists for PRE.
3. Cite evidence by id; never invent evidence. Do not use unvalidated proposals as knowledge.
4. Output must validate against `schemas/prediction.schema.json`.
5. Synthetic data must set `synthetic: true`.
