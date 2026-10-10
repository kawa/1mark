# Stateless session instructions

You have no memory of earlier sessions. Everything needed is in the repository and the task.

1. Read `protocols/prediction-v1.md` (or `review-v1.md`), then `knowledge/validated/`.
2. Read the task and its evidence bundle. Use only that evidence.
3. Write JSON conforming to `schemas/`. Mark fabricated data `"synthetic": true`.
4. Predict first (qualitative assessments and bets in units). Never consider bankroll or stake amounts. For WAIT, never edit the baseline; record post-exhibition changes in a `decision`; if no valid decision exists by the cutoff, record INCOMPLETE (never SKIP).
5. Validate: `python -m onemark.validate <files>` (use `lifecycle_*` fixtures for full cross-document checks) (config in `config/`) and `pytest`.
6. Put learning in `knowledge/proposals/` as proposals. Never edit `knowledge/validated/` or `protocols/`.
7. Open a PR; never merge. No scraping or AI API calls exist yet.
