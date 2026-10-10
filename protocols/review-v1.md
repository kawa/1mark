# Review Protocol v1.0.0

1. Review only after the outcome is known; record it in `schemas/review.schema.json` format.
2. Assess the prediction (calibration, reasoning, evidence use) separately from risk (staking, units).
3. Do not judge by result alone; WAIT/SKIP/UNAVAILABLE/INCOMPLETE are reviewed too.
4. Lessons go to `knowledge/proposals/` as learning proposals (`status: proposed`). They are never written to `knowledge/validated/` by the AI; a human promotes them via PR.
