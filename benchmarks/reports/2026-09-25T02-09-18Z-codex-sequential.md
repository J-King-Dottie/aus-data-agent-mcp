# Benchmark run: 2026-09-25T02:09:18Z-codex-sequential

Model: **gpt-6-sol** · Reasoning: **high** · Harness: Codex desktop · Started: 2026-09-25T02:09:18.820844+00:00

## Summary

| Metric | Model | Reasoning | Time | Total tokens | Estimated cost |
|---|---|---|---:|---:|---:|
| ABS | gpt-6-sol | high | 39.8s | 869,389 | $0.1907 |
| World Bank | gpt-6-sol | high | 31.0s | 769,462 | $0.1723 |
| IMF | gpt-6-sol | high | 49.4s | 1,046,667 | $0.2265 |
| OECD | gpt-6-sol | high | 38.1s | 933,292 | $0.2017 |
| RBA | gpt-6-sol | high | 39.0s | 950,488 | $0.2036 |
| DCCEEW | gpt-6-sol | high | 38.1s | 965,809 | $0.2076 |
| Pacific Data Hub | gpt-6-sol | high | 41.0s | 1,123,387 | $0.2428 |
| UN Comtrade | gpt-6-sol | high | 34.9s | 1,001,589 | $0.2154 |
| **Total** | **gpt-6-sol** | **high** | **5m 11.3s** | **7,660,083** | **$1.6605** |

Full suite wall time: 6m 21.7s. The total above sums measured case times; the suite wall time can include gaps.

Estimated cost is an API-equivalent model-token estimate, not a subscription charge. Cached input is included in input tokens. Rounded row costs may differ from the total.

## Detail

| Metric | Model | Reasoning | Search | Metadata | Retrieve | Input tokens | Cached input | Output tokens | Total tokens | Correct |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ABS | gpt-6-sol | high | 5.8s | 0.3s | 0.3s | 868,385 | 864,512 | 1,004 | 869,389 | Yes |
| World Bank | gpt-6-sol | high | 0.2s | 0.0s | 0.3s | 768,327 | 764,288 | 1,135 | 769,462 | Yes |
| IMF | gpt-6-sol | high | 0.0s | 0.0s | 10.1s | 1,045,645 | 1,041,664 | 1,022 | 1,046,667 | Yes |
| OECD | gpt-6-sol | high | 0.1s | 0.0s | 1.6s | 932,461 | 928,640 | 831 | 933,292 | Yes |
| RBA | gpt-6-sol | high | 0.0s | 2.0s | 0.5s | 949,717 | 946,432 | 771 | 950,488 | Yes |
| DCCEEW | gpt-6-sol | high | 0.0s | 1.6s | 0.0s | 964,966 | 961,536 | 843 | 965,809 | Yes |
| Pacific Data Hub | gpt-6-sol | high | 0.0s | 1.1s | 0.6s | 1,122,350 | 1,117,952 | 1,037 | 1,123,387 | Yes |
| UN Comtrade | gpt-6-sol | high | 0.0s | 0.0s | 1.8s | 1,000,763 | 996,864 | 826 | 1,001,589 | Yes |

## Run notes

Sequential measurement run in one fresh stdio MCP session (benchmark-measured-367358). Per-case duration measures wall time from search_catalog start through reading the saved retrieval JSON. Tool timing reports actual MCP call time; retrieve is the source-data call alone. Per-case token counts are deltas of recorded Codex turn usage around each case's tool calls, including tool arguments and model reasoning up to the final artifact read, but excluding setup and final grading. Cached input is included in input and reasoning output is included in output. Suite usage is the sum of these attributed case deltas, not the entire Codex turn. Context accumulated through the sequential cases; run order and prior knowledge from the first run affect token use. This is a self-reviewed repeat, not a blind fresh-model test. The sum of case durations is 311.253 seconds; the full suite wall duration of 381.696 seconds also includes gaps between cases.

## Cost basis

Estimated API-equivalent GPT-6 Sol Standard text pricing, checked 2026-09-25: USD 2.00/1M uncached input, USD 0.20/1M cached input, USD 10.00/1M output; https://developers.openai.com/api/docs/models/gpt-6-sol. Formula: (input_tokens - cached_input_tokens)*2/1M + cached_input_tokens*0.20/1M + output_tokens*10/1M. Excludes tool charges and any Codex subscription billing. No recorded request in the run exceeded 272K input tokens.

Full answers, dataset IDs, and retrieval artifact paths are in [results.json](../results.json).
