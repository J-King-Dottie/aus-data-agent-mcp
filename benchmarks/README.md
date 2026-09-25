# Agent benchmark

This benchmark evaluates the agent in the current conversation. It does **not** launch another model through a CLI or API. Select the model for this task, then ask the agent to run the benchmark. To compare another model, change the model assigned to this task and ask it to run the same suite again.

[cases.json](cases.json) contains one fixed question for each of the eight AusData sources: ABS, World Bank, IMF, OECD, RBA, DCCEEW, Pacific Data Hub and UN Comtrade. The agent should treat each question as a fresh user request: search the live unified catalogue, choose a candidate, inspect metadata, retrieve through the MCP, read the saved JSON, calculate the answer and cite the source. Read [rubric.json](rubric.json) only after answering; its reference datasets and checks are for grading. No other data API or publication workbook is allowed as a substitute.

For each case, record the start and end time around the agent's actual work, the answer, selected dataset, retrieval artifact path, and whether the answer passes the rubric's checks. Grade `pass`, `partial`, `fail` or `not_assessable`, with a short reason. A provider outage or blocked MCP call is `not_assessable`; a confident answer without verifiable retrieval is not a pass. Record source revisions or other relevant conditions in notes.

Save each completed suite in [results.json](results.json) as a new run. Include the model label selected in the task UI, the client or harness name, UTC timestamps, total elapsed time, and per-case elapsed time. If the host exposes reliable per-run input, cached-input and output token counts, record them and calculate cost using a documented pricing source and date. If it only exposes usage for the whole turn, put it at the suite level. If it exposes no reliable token or billing data, leave those fields `null`; do not infer them from account limits or fabricate a cost. Any estimated API-equivalent cost must be labelled as an estimate, not an actual subscription charge.

Each object in `runs` uses this shape (one case object per question):

```json
{
  "run_id": "UTC timestamp or unique label",
  "model": "model selected for this task",
  "harness": "MCP client or agent harness",
  "started_at": "UTC timestamp",
  "finished_at": "UTC timestamp",
  "duration_seconds": 0,
  "usage": {
    "input_tokens": null,
    "cached_input_tokens": null,
    "output_tokens": null
  },
  "estimated_cost_usd": null,
  "cost_basis": null,
  "cases": [
    {
      "case_id": "abs_youth_unemployment",
      "duration_seconds": 0,
      "answer": "...",
      "dataset_id": "...",
      "artifact_path": "...",
      "correctness": "pass",
      "review_notes": "..."
    }
  ]
}
```

For fair comparisons, keep the questions unchanged and use a fresh MCP session for each suite. Run on the same machine and network when practical. Report unavailable providers separately from analytical errors. Live observations may be revised, so grade against the retrieval saved for that run rather than a frozen number. A complete suite has eight reviewed cases; repeat suites if you want to measure consistency.
