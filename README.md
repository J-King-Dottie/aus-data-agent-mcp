# AusData MCP — Australian public data for AI agents

**AusData** is a local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for finding and retrieving Australian and Pacific statistics with global economic context. It gives agents such as **Codex** and **Claude Code** one searchable catalogue across the **Australian Bureau of Statistics (ABS)**, **Reserve Bank of Australia (RBA)**, **Pacific Data Hub**, **World Bank**, **OECD**, **IMF**, **UN Comtrade**, and Australian energy statistics.

Ask a question in ordinary language. The agent searches live source catalogues, inspects the selected dataset, retrieves the data, then uses the saved evidence to calculate and explain the answer. AusData supplies data capabilities and analyst guidance; the agent chooses the analysis, chart, and response format. No web app, model API key, or database service is required.

## Quick start

Requires Python 3.11+ with SQLite FTS5 and internet access to public sources.

```bash
git clone https://github.com/J-King-Dottie/aus-data-agent-mcp.git
cd aus-data-agent-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Connect the MCP server using absolute paths. For **Codex**:

```bash
codex mcp add ausdata -- /absolute/path/aus-data-agent-mcp/.venv/bin/python /absolute/path/aus-data-agent-mcp/scripts/run_mcp.py
```

For **Claude Code**:

```bash
claude mcp add --transport stdio ausdata -- /absolute/path/aus-data-agent-mcp/.venv/bin/python /absolute/path/aus-data-agent-mcp/scripts/run_mcp.py
```

Claude Code can also use the project [`.mcp.json`](.mcp.json) when started here with the virtual environment active. On Windows, use `.venv\Scripts\python.exe` or launch a WSL checkout through `wsl.exe`; the agent must be able to read paths returned by the server. See the [Codex](https://developers.openai.com/codex/mcp) and [Claude Code](https://code.claude.com/docs/en/mcp) MCP setup guides for client-specific settings.

Example prompt: **“Compare Australian and New Zealand GDP per capita since 2015, chart the trend, and cite the source.”**

## How it works

```text
question → search_catalog → agent chooses a candidate → get_metadata → retrieve
         → agent reads saved JSON → verifies, narrows, calculates, explains
```

| Tool | What it provides |
| --- | --- |
| `search_catalog` | Up to 50 candidates from one live, session-cached SQLite FTS catalogue, ordered by text match without suitability scores |
| `get_metadata` | Source definitions, dimensions, codes, and retrieval guidance for a selected dataset |
| `retrieve` | Complete local JSON evidence plus a compact manifest with path, provenance, coverage, row count, and file size |

The first search in a new session fetches and normalizes the source catalogues. Later searches use the cached index; successful source lists refresh after 24 hours in a resumed session. Catalogue entries identify candidates, **not** guaranteed observation coverage. The agent checks metadata and the actual retrieved data before making claims. A 50 MiB or larger saved JSON is flagged in the manifest so the agent can alert the user and inspect it selectively.

Retrieved JSON stays under `runtime/sessions/<session-id>/artifacts/` until removed locally. Only a small preview is returned to the model; the complete observations remain in the file. The agent should answer and show charts in the conversation by default, creating exports only when requested or technically needed. [Analyst guidance](AGENT_SYSTEM_PROMPT.md) is supplied at MCP initialization and available as `ausdata://guide`.

## Sources and scope

| Source | Available through AusData |
| --- | --- |
| ABS | Official Australian statistics via ABS SDMX dataflows: labour, prices, population, national accounts, trade, and more |
| RBA | Supported statistical-table CSV series, including rates, exchange rates, money, and credit |
| DCCEEW | Latest Australian Energy Statistics Table O electricity generation workbook |
| Pacific Data Hub / SPC | Pacific island SDMX datasets, including population, energy, prices, trade, and SDGs |
| World Bank | Indicator API, including WDI and other identified databases |
| OECD | Data Explorer catalogue discovery and SDMX observation retrieval |
| IMF | DataMapper indicator catalogue and observations |
| UN Comtrade | Goods trade by reporter, partner, flow, period, and product |

This is the supported retrieval scope, not a mirror of every publication from these organizations. If a suitable dataset is unavailable through these pathways, the agent reports that limit and asks before looking for a separate workbook, website, or API. Source outages and incomplete or stale catalogue coverage are disclosed. OECD's SDMX endpoint can be blocked on some networks.

## Develop and evaluate

```bash
python -m unittest discover -s tests -v
python -m compileall -q ausdata_mcp scripts
python scripts/smoke_mcp.py
python scripts/smoke_mcp.py --live  # optional live World Bank retrieval
```

The smoke check uses a real stdio MCP client/server session. See [AGENTS.md](AGENTS.md) for architecture and development rules, [AGENT_SYSTEM_PROMPT.md](AGENT_SYSTEM_PROMPT.md) for evidence standards, and the [agent benchmark](benchmarks/README.md) for eight fixed questions across the sources. The benchmark is run by the agent assigned to the task; it does not launch another model.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).
Built on existing open source work including [seansoreilly/mcp-server-abs](https://github.com/seansoreilly/mcp-server-abs) and [hanlulong/openecon-data](https://github.com/hanlulong/openecon-data).

Also incorporates data capabilities from [Pacific Data Hub Agent MCP](https://github.com/J-King-Dottie/pacific-data-hub-agent-mcp).
