# Benchmark run: 2026-09-25T04:24:14Z-astra-high-sequential

Model: **gpt-6-astra** · Reasoning: **high** · Harness: Codex desktop; real stdio MCP from /tmp · Started: 2026-09-25T04:24:14.663436+00:00

## Summary

| Metric | Model | Reasoning | Time | Total tokens | Estimated cost |
|---|---|---|---:|---:|---:|
| ABS | gpt-6-astra | high | 49.7s | 611,605 | $0.7576 |
| World Bank | gpt-6-astra | high | 26.6s | 394,197 | $0.4546 |
| IMF | gpt-6-astra | high | 34.2s | 536,805 | $0.5825 |
| OECD | gpt-6-astra | high | 51.5s | 685,817 | $0.7570 |
| RBA | gpt-6-astra | high | 32.7s | 567,316 | $0.6334 |
| DCCEEW | gpt-6-astra | high | 52.8s | 725,745 | $0.7964 |
| Pacific Data Hub | gpt-6-astra | high | 32.4s | 595,144 | $0.6737 |
| UN Comtrade | gpt-6-astra | high | 1m 31.9s | 1,222,295 | $1.3046 |
| **Total** | **gpt-6-astra** | **high** | **6m 11.8s** | **5,338,924** | **$5.9598** |

Full suite wall time: 7m 17.6s. The total above sums measured case times; the suite wall time can include gaps.

Estimated cost is an API-equivalent model-token estimate, not a subscription charge. Cached input is included in input tokens. Rounded row costs may differ from the total.

## Detail

| Metric | Model | Reasoning | Search | Metadata | Retrieve | Input tokens | Cached input | Output tokens | Total tokens | Correct |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ABS | gpt-6-astra | high | 8.4s | 0.4s | 2.3s | 611,128 | 597,504 | 477 | 611,605 | Yes |
| World Bank | gpt-6-astra | high | 0.1s | 0.0s | 0.5s | 393,684 | 389,760 | 513 | 394,197 | Yes |
| IMF | gpt-6-astra | high | 0.1s | 0.0s | 10.0s | 536,347 | 533,760 | 458 | 536,805 | Yes |
| OECD | gpt-6-astra | high | 0.4s | 1.2s | 2.0s | 685,246 | 680,448 | 571 | 685,817 | Yes |
| RBA | gpt-6-astra | high | 0.1s | 2.0s | 0.2s | 566,764 | 562,432 | 552 | 567,316 | Yes |
| DCCEEW | gpt-6-astra | high | 0.0s | 1.8s | 0.1s | 725,041 | 721,024 | 704 | 725,745 | Yes |
| Pacific Data Hub | gpt-6-astra | high | 0.1s | 1.0s | 0.5s | 594,319 | 590,080 | 825 | 595,144 | Yes |
| UN Comtrade | gpt-6-astra | high | 0.1s | 0.0s | 48.3s | 1,221,423 | 1,217,024 | 872 | 1,222,295 | Yes |

## Run notes

Eight cases completed sequentially through search_catalog, get_metadata, retrieve and inspection of full saved JSON, in fresh MCP session benchmark-astra-high-20260925-0424. No source errors or retries. Current task metadata confirmed gpt-6-astra with high reasoning. This is a self-reviewed repeat in the existing cleanup conversation, not a blind fresh-model comparison: prior answers were seen in results.json during setup; answers.json was opened only after all eight new answers were completed and saved. Case durations span first search through checked JSON; tool times sum actual MCP calls, including metadata browsing. Token figures are actual deltas of the latest committed cumulative desktop usage counters sampled at each case boundary; raw snapshots and their event timestamps are retained. Desktop counters commit after tool execution, so these snapshots lag the current invocation: the final evidence-reading invocation is excluded at its end sample and may be counted in the following case. The DCCEEW completion and PDH search shared one orchestration invocation. No turn total was divided or estimated across cases. Treat per-case token/cost attribution as approximate; this run is useful for correctness and operational timing, not a controlled model-efficiency comparison. Input includes cached tokens and output includes reasoning. Setup, pricing lookup and final grading/reporting are outside the measured intervals. The first search includes cold discovery for all sources; subsequent cases reuse that catalogue. UN Comtrade retrieval took 48.342 seconds.

## Cost basis

Estimated API-equivalent GPT-6 Astra Standard text pricing, checked 2026-09-25: USD 10/1M uncached input, USD 1/1M cached input, USD 12.50/1M cache writes, USD 50/1M output. Source: https://developers.openai.com/api/docs/models/gpt-6-astra. Maximum recorded per-request input in this run: 153,425 tokens, below the 272K long-context pricing threshold. This is a Standard-rate comparison estimate, not actual Codex subscription billing or a service-tier charge.

Full answers, dataset IDs, and retrieval artifact paths are in [results.json](../results.json).
