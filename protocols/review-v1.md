# 1mark Review Protocol v1.0.0

1. Review a **locked** prediction (cite `prediction_sha256`) against the confirmed result (`finish_order`, source URL, time).
2. Record four things separately (`schemas/review.schema.json`):
   - **outcome**: facts only; which bets of the final proposal (PRE bets, or the decision's `final_bets` when a WAIT baseline was followed by a decision) hit. Computed deterministically.
   - **prediction_error**: how assessments differed from reality, independent of bets.
   - **decision_quality**: was the decision sound with information available at lock time, regardless of result.
   - **protocol_compliance**: checklist of protocol rules followed or broken.
3. Financial **settlement** is separate from hit detection. The result stores official win `payouts` by bet type and combination, and `refund_info`:
   - `confirmed`: complete. Refunded combinations are listed (several allowed), or `race_wide: true` for a race-wide refund; an empty list is a confirmed non-refund. Official `cause` and `affected_boats` are recorded when available, never inferred.
   - `incomplete` / `unavailable`: refund status is not established.
   Settlement per bet: refunded returns the stake; a hit returns payout x stake / 100; a miss returns 0 **only when refund status is confirmed**. Otherwise the line is `unresolved` and the settlement is `unresolved` with no total return or profit. An unknown refund is never counted as a loss. Settlement requires an approved risk decision.
4. Review PRE, WAIT, SKIP, UNAVAILABLE and INCOMPLETE alike.
5. Lessons become learning proposals (`schemas/learning_proposal.schema.json`), each backed by at least one review observation, with `status: proposed`.
6. Neither reviews nor AI sessions modify `knowledge/validated/` or `protocols/`. A human promotes a proposal by PR.
