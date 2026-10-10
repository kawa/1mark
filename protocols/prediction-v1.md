# 1mark Prediction Protocol v1.1.0

Domain: Japanese boat racing (kyotei). Every race has exactly six boats, numbered 1-6.
Bet types: trifecta (3連単, ordered 1st-2nd-3rd) and trio (3連複, any order, stored ascending).

Version 1.1.0 adds decision-quality rules (see "Protocol v1.1 decision discipline"). Records with `protocol_version` below 1.1.0 (including all historical predictions) remain valid and are never modified.

## Layers (never mix)
| Layer | Location | Changed by |
|---|---|---|
| Protocol | `protocols/` | human PR only |
| Knowledge | `knowledge/validated/` | human PR only |
| Evidence | one bundle per race (`schemas/evidence.schema.json`) | the session, pre-race only |
| Task | one race (`schemas/task.schema.json`) | the requester |

A session uses only these four inputs. It must not rely on earlier conversations.

## Statuses
Prediction decisions (v0.1 assessments are qualitative: `strong`/`moderate`/`weak`; no numerical probabilities):
- **PRE**: final. Six `boat_assessments` and finalized `bets` (bet_type, combination, integer `units`). Needs no risk approval.
- **WAIT**: made before exhibition data exists. A complete baseline (`boat_assessments` and `bets` with units) plus a `wait_plan`. Each step names the specific observation (standard field), a decision threshold and the expected change (add/remove bets, change units). The baseline is **immutable**.
- **SKIP**: a deliberate decision after analysis. All six assessments and `skip_analysis` (reason, evidence ids). No bets.

Operational states (not predictions; no assessments or bets):
- **UNAVAILABLE**: race or data cannot be obtained (`unavailable_reason`).
- **INCOMPLETE**: session could not finish (`missing`).

## WAIT revision (post-exhibition decision)
A separate `decision` record (`schemas/decision.schema.json`) references the locked baseline by id and SHA-256 and uses a post-exhibition, pre-race evidence bundle. Final is **BET**, **SKIP** or **INCOMPLETE**.
- The WAIT plan (`expected_operations`) and the decision (`changes`) use one operation vocabulary: `add`, `remove`, `change_units`. They remain separate records: the plan is what was conditionally expected; the changes are what was actually executed.
- Each plan step has a `step_id`. Each executed change lists `plan_step_ids` (empty = explicitly unplanned), observed `evidence_ids` and a `reason`. An empty `changes` list keeps the baseline unchanged.
- BET: `final_bets` must equal the baseline bets with the changes applied (validator recomputes).
- SKIP: `skip_analysis` with reason and evidence; no changes or bets.
- INCOMPLETE: recorded at or after `prediction_cutoff` when no valid BET/SKIP decision exists. It never becomes SKIP, carries no bets, and cannot be risk-approved.
- BET/SKIP must be locked after the baseline and no later than `prediction_cutoff`.

## Bets, units and risk
- Trifecta (3連単) is ordered; trio (3連複) is stored ascending. A trifecta and a trio on the same boats are distinct products and may coexist.
- Units are bankroll-independent integers. Unit value and stake constraints live in versioned config (`config/units-v1.json`; currently 1u = 100 JPY), not in schemas.
- The risk gate (`risk_decision`) is separate and only **approves or rejects the complete proposal** (a PRE prediction or a BET decision). It never changes combinations or units; partial execution and reduced units are not allowed. Approval records `total_stake_jpy`, which must equal all units x unit value. A prediction is final without it.

## Determinism and versions
- Every record carries `schema_version`, and predictions carry `protocol_version` and `knowledge_version`.
- A prediction is locked by `locked_at <= task.prediction_cutoff <= scheduled_start`. Its identity is the SHA-256 of its canonical JSON (`onemark.validate.canonical_sha256`); risk decisions and reviews cite that hash.
- Rules a schema cannot express (ascending trios, no duplicate bets, WAIT change application, stake from config, hit calculation, settlement, evidence vocabulary and time consistency) are in `onemark/validate.py` and tested. `validate_lifecycle` is the single entry point: it schema-validates every record of a race and runs all cross-document checks (prediction, WAIT decision, risk decision, result, review, settlement, learning proposals). Run it on `examples/lifecycle_*.synthetic.json` via `python -m onemark.validate`; CI does.

## Evidence integrity
- Observations are facts with `source_url`, `observed_at`, `phase`. Inferences are listed separately and must be based on observations.
- Field names, types and units are standardized in `config/evidence-vocabulary-v1.json` (entry course, exhibition time/ST, motor statistics, wind direction/speed, wave height). Ranges and the wind-direction set are **provisional** (pending verification against BOAT RACE data): they only produce warnings. Every observation keeps its `raw_value` exactly as published. Extensions must be namespaced `x-<ns>:<name>`. Timestamps are ISO 8601 with a UTC offset.
- Unobtained data is listed in `missing_data`, never guessed.
- Any `post_race` observation or one observed after the cutoff must not be in a pre-race bundle (validator rejects it).

## Protocol v1.1 decision discipline (`protocol_version` >= 1.1.0)
**Sequence.** Every race first receives a complete pre-exhibition assessment (six `boat_assessments`, `evaluation`, `bets` where a proposition exists). Only then choose the execution timing. Evaluate in this fixed order, recording each in `evaluation`: (1) course advantage, (2) racer performance, (3) motor performance, (4) current-series performance, (5) start-related evidence, (6) missing or contradictory data, (7) plausible scenarios (finish orders). No numerical weights exist; do not invent any. `primary_drivers` must name the evidence ids that most strongly influenced the ranking and how.

**PRE / WAIT / SKIP are chosen by the expected value of additional information**, not by whether exhibition data exists. Missing exhibition data never implies WAIT. There are no target proportions of PRE, WAIT or SKIP.
- PRE requires `timing_rationale`: why further observation would not materially change the baseline.
- WAIT: every `wait_plan` step requires `uncertainty` (a specific unresolved question), the `observation` expected to resolve it, `why_relevant`, `expected_change` (concrete change to the baseline) and `materiality` (why the change matters). Generic rules (e.g. "remove the leading boat if its exhibition time ranks last") are not validated knowledge and are not justification.
- SKIP is a completed analysis ending in a deliberate no-bet decision (`skip_analysis.completed_analysis`), allowed when evidence supports no defensible proposition. It is never used for missing data or unfinished work (use UNAVAILABLE / INCOMPLETE).

**Thresholds.** A numerical threshold in `decision_threshold` needs `threshold_basis`: `protocol_rule` or `validated_knowledge` with a `ref`, or `unvalidated_hypothesis`, which must not be `automatic` and may not be used as an automatic betting rule. Do not invent statistical thresholds. Numerical thresholds without a basis are rejected.

**Bet sizing.** No fixed number of combinations or units. `bet_rationale` must explain each selected combination, the relative allocation, the total units and why alternatives were excluded. Bankroll stays independent of prediction. Do not claim calibrated probabilities or positive expected value without supporting evidence.

**Knowledge.** Keep validated rules, working assumptions and unvalidated hypotheses distinct. Unvalidated heuristics are never added to `knowledge/validated/`; the review and learning lifecycle is unchanged.
