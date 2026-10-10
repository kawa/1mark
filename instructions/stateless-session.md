# Instruction package for stateless AI sessions

Each session starts with no memory. Do this:
1. Read `protocols/prediction-v1.md` (or `review-v1.md`), then `knowledge/validated/`.
2. Read the task in `tasks/` and its evidence in `evidence/`.
3. Produce JSON matching the schema in `schemas/`; save under the task's folder or `examples/` if synthetic.
4. Run `pytest` before opening a PR. Open a PR; never merge it.
5. Do not scrape or call AI APIs (not implemented yet). Do not edit `knowledge/validated/`.
