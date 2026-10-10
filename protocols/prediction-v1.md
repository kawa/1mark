# 1mark Prediction Protocol v1.0.0

Domain: Japanese boat racing (kyotei). Every race has exactly six boats, numbered 1-6.
Bet types: trifecta (3連単, ordered 1st-2nd-3rd) and trio (3連複, any order, stored ascending).

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
A separate `decision` record (`schemas/decision.schema.json`) references the locked baseline by id and SHA-256 and uses a post-exhibition, pre-race evidence bundle. Final is **BET** or **SKIP**.
- `changes` is a list of `add`, `remove`, `change_units`; each cites evidence ids and a reason. An empty list keeps the baseline unchanged.
- BET: `final_bets` must equal the baseline bets with the changes applied (validator recomputes).
- SKIP: `skip_analysis` with reason and evidence; no changes or bets.
- It must be locked after the baseline and no later than `prediction_cutoff`.

## Bets, units and risk
- Trifecta (3連単) is ordered; trio (3連複) is stored ascending. A trifecta and a trio on the same boats are distinct products and may coexist.
- Units are bankroll-independent integers. Unit value and stake constraints live in versioned config (`config/units-v1.json`; currently 1u = 100 JPY), not in schemas.
- The risk gate (`risk_decision`) is separate and only **approves or rejects the complete proposal** (a PRE prediction or a BET decision). It never changes combinations or units; partial execution and reduced units are not allowed. Approval records `total_stake_jpy`, which must equal all units x unit value. A prediction is final without it.

## Determinism and versions
- Every record carries `schema_version`, and predictions carry `protocol_version` and `knowledge_version`.
- A prediction is locked by `locked_at <= task.prediction_cutoff <= scheduled_start`. Its identity is the SHA-256 of its canonical JSON (`onemark.validate.canonical_sha256`); risk decisions and reviews cite that hash.
- Rules a schema cannot express (ascending trios, no duplicate bets, WAIT change application, stake from config, hit calculation, settlement, evidence vocabulary and time consistency) are in `onemark/validate.py` and tested.

## Evidence integrity
- Observations are facts with `source_url`, `observed_at`, `phase`. Inferences are listed separately and must be based on observations.
- Field names, types and units are standardized in `config/evidence-vocabulary-v1.json` (entry course, exhibition time/ST, motor statistics, wind direction/speed, wave height). Extensions must be namespaced `x-<ns>:<name>`. Timestamps are ISO 8601 with a UTC offset.
- Unobtained data is listed in `missing_data`, never guessed.
- Any `post_race` observation or one observed after the cutoff must not be in a pre-race bundle (validator rejects it).
