# Benchmark performance over time

Generated from [results.json](results.json). Each linked run report has a summary table and a detailed timing and token table.

| Run | UTC start | Model | Reasoning | Correct | Total case time | Total tokens | Estimated cost | Suite wall time |
|---|---|---|---|---:|---:|---:|---:|---:|
| [2026-09-25T02:09:18Z-codex-sequential](reports/2026-09-25T02-09-18Z-codex-sequential.md) | 2026-09-25T02:09:18.820844+00:00 | gpt-6-sol | high | 8/8 | 5m 11.3s | 7,660,083 | $1.6605 | 6m 21.7s |
| [2026-09-25T04:24:14Z-astra-high-sequential](reports/2026-09-25T04-24-14Z-astra-high-sequential.md) | 2026-09-25T04:24:14.663436+00:00 | gpt-6-astra | high | 8/8 | 6m 11.8s | 5,338,924 | $5.9598 | 7m 17.6s |

Total case time sums each case's search through checked evidence. Estimated cost uses the model pricing saved with each run; it is not an actual charge. Compare run notes before attributing differences to a model.
