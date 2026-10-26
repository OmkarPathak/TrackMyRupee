# docs/

Design notes for contributors. Operational material (runbooks, hosting, incident history, interview notes) is kept
outside the repository in `docs/private/`, which is gitignored.

| File | What it is |
|---|---|
| `DECISIONS_AND_TRADEOFFS.md` | Why the code looks the way it does: decisions, alternatives rejected, trade-offs, a dated changelog and an entry template. Add to it with each significant change. |
| `double_entry_explainer.md` | Plain-English explanation of double-entry bookkeeping. |
| `double_entry_rollout_plan.md` | The design and phased plan for adding the ledger without changing the user workflow. |
| `insights-engine-plan.md` | Audit and design of the Insights Engine. |
| `insights-rules.md` | Generated catalogue of every insight rule (`python scripts/generate_insights_rules_doc.py`). |
| `insights-tone.md` | How insights should sound. |
