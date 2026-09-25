# Australian Public Data MCP

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for AI agents to search live catalogues and retrieve official Australian, Pacific, and global public data. Sources include the Australian Bureau of Statistics (ABS), Reserve Bank of Australia (RBA), DCCEEW's Australian Energy Statistics, Pacific Data Hub/SPC, World Bank, OECD, IMF, and UN Comtrade. No model API key is required.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).
Built on existing open source work including [seansoreilly/mcp-server-abs](https://github.com/seansoreilly/mcp-server-abs) and [hanlulong/openecon-data](https://github.com/hanlulong/openecon-data).

## For agents

Install with Python 3.11+ and SQLite FTS5 support:

```bash
git clone https://github.com/J-King-Dottie/australian-public-data-mcp.git
cd australian-public-data-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the server over stdio with `python scripts/run_mcp.py`. The project [`.mcp.json`](.mcp.json) works for clients launched here with the virtual environment active. From another project, use absolute paths; for example:

```bash
codex mcp add ausdata -- /absolute/path/australian-public-data-mcp/.venv/bin/python /absolute/path/australian-public-data-mcp/scripts/run_mcp.py
claude mcp add --transport stdio ausdata -- /absolute/path/australian-public-data-mcp/.venv/bin/python /absolute/path/australian-public-data-mcp/scripts/run_mcp.py
```

The workflow has three tools:

1. `search_catalog` searches one live, normalized catalogue through SQLite FTS. Results are up to 50 **candidates** in text-match order, not suitability-ranked recommendations. Check source freshness and choose for the question's measure, geography, frequency, and recency.
2. `get_metadata` inspects the chosen dataset's definitions, dimensions, and valid codes before retrieval.
3. `retrieve` saves complete source data as local JSON and returns a compact manifest with `artifact_path`, coverage, provenance, row count, and file size. Read the file to verify and analyse observations; the preview is not the full dataset. A 50 MiB or larger file is flagged so the agent can tell the user.

A new session fetches source catalogues; later searches use its cache. Reused sessions refresh successful catalogues after 24 hours. Metadata and observations are separate from catalogue discovery. Retrieved files persist under `runtime/sessions/` until removed locally.

Use only this MCP's supported retrieval pathways for data tasks. If they cannot supply a suitable dataset, report that limit and ask before searching websites, other APIs, or separate publications. The server supplies [analyst guidance](AGENT_SYSTEM_PROMPT.md) at initialization and as `ausdata://guide`; MCP tool descriptions define call parameters. [AGENTS.md](AGENTS.md) covers development.
