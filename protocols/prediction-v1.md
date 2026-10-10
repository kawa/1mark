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
Prediction decisions:
- **PRE**: final. Six `boat_assessments` and finalized `bets` (bet_type, combination, integer `units`). Needs no risk approval.
- **WAIT**: made before exhibition data exists. Contains a complete baseline (`boat_assessments` and `bets` with units) plus a `wait_plan`. Each step names the specific observation, a decision threshold, and the expected change (bets added/removed). A later PRE supersedes it.
- **SKIP**: a deliberate decision after analysis. Requires all six assessments and `skip_analysis` (reason, evidence ids). No bets.

Operational states (not predictions; carry no assessments or bets):
- **UNAVAILABLE**: race or data cannot be obtained (`unavailable_reason`).
- **INCOMPLETE**: session could not finish (`missing`).

## Prediction vs risk
Units are bankroll-independent. Stake amounts (JPY, multiples of 100) appear only in a separate `risk_decision`, applied after the prediction is locked. It may approve, reduce or reject; it can never edit the prediction, and a prediction is final without it.

## Determinism and versions
- Every record carries `schema_version`, and predictions carry `protocol_version` and `knowledge_version`.
- A prediction is locked by `locked_at <= task.prediction_cutoff <= scheduled_start`. Its identity is the SHA-256 of its canonical JSON (`onemark.validate.canonical_sha256`); risk decisions and reviews cite that hash.
- Rules a schema cannot express (ascending trios, no duplicate bets, hit calculation, stake = units x unit value, evidence/time consistency) are in `onemark/validate.py` and tested.

## Evidence integrity
- Observations are facts with `source_url`, `observed_at`, `phase`. Inferences are listed separately and must be based on observations.
- Unobtained data is listed in `missing_data`, never guessed.
- Any `post_race` observation or one observed after the cutoff must not be in a pre-race bundle (validator rejects it).
