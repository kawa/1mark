# Stateless session instructions

You have no memory of earlier sessions. Everything needed is in the repository and the task.

1. Read `protocols/prediction-v1.md` (or `review-v1.md`), then `knowledge/validated/`.
2. Read the task and its evidence bundle. Use only that evidence.
3. Write JSON conforming to `schemas/`. Mark fabricated data `"synthetic": true`.
4. Follow the v1.1 decision discipline in the protocol: complete the pre-exhibition assessment for every race, justify PRE/WAIT/SKIP, bet choices and units (no fixed counts), and never present unvalidated thresholds as rules. Predict first (qualitative assessments and bets in units). Never consider bankroll or stake amounts. For WAIT, never edit the baseline; record post-exhibition changes in a `decision`; if no valid decision exists by the cutoff, record INCOMPLETE (never SKIP).
5. Validate: `python -m onemark.validate <files>` (use `lifecycle_*` fixtures for full cross-document checks) (config in `config/`) and `pytest`. Review any advisory quality warnings; they do not fail validation and do not replace judgment against the task and evidence.
6. Put learning in `knowledge/proposals/` as proposals. Never edit `knowledge/validated/` or `protocols/`.
7. Open a PR; never merge. No scraping or AI API calls exist yet.

## Batch PRE / user-controlled WAIT workflow
For a batch ZIP (`python -m onemark.workflow <batch-dir>`), follow its `START_HERE.md`: complete PRE/SKIP for all 12 races first (unprocessed races are reported as failures, never SKIP), the user selects WAIT items (tracked in a `workflow_state`, never in the prediction), and exhibition results produce an `exhibition_revision` referencing the baseline prediction id and sha256.
