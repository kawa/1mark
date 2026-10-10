# 1mark Review Protocol v1.0.0

1. Review a **locked** prediction (cite `prediction_sha256`) against the confirmed result (`finish_order`, source URL, time).
2. Record four things separately (`schemas/review.schema.json`):
   - **outcome**: facts only; which bets hit (computed deterministically).
   - **prediction_error**: how assessments differed from reality, independent of bets.
   - **decision_quality**: was the decision sound with information available at lock time, regardless of result.
   - **protocol_compliance**: checklist of protocol rules followed or broken.
3. Review PRE, WAIT, SKIP, UNAVAILABLE and INCOMPLETE alike.
4. Lessons become learning proposals (`schemas/learning_proposal.schema.json`), each backed by at least one review observation, with `status: proposed`.
5. Neither reviews nor AI sessions modify `knowledge/validated/` or `protocols/`. A human promotes a proposal by PR.
