# Australian Public Data MCP

An MCP server for agents to find and retrieve official Australian, Pacific and global public data. Requires Python 3.11+ with SQLite FTS5. No model API key is needed by the server.

## Install

```bash
git clone https://github.com/J-King-Dottie/australian-public-data-mcp.git
cd australian-public-data-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Connect an MCP client

The server uses stdio. Use absolute paths when registering it from another project:

```bash
codex mcp add ausdata -- /absolute/path/australian-public-data-mcp/.venv/bin/python /absolute/path/australian-public-data-mcp/scripts/run_mcp.py
claude mcp add --transport stdio ausdata -- /absolute/path/australian-public-data-mcp/.venv/bin/python /absolute/path/australian-public-data-mcp/scripts/run_mcp.py
```

The included [`.mcp.json`](.mcp.json) also configures project-local clients when the virtual environment is active.

## Use

1. `search_catalog` finds candidate datasets.
2. `get_metadata` shows a dataset's definitions, dimensions and valid codes.
3. `retrieve` saves the complete data as JSON and returns its `artifact_path`.

The agent reads the JSON at `artifact_path` to analyse the data, so it needs access to the server's filesystem. Tool descriptions specify the call parameters. Analyst guidance is supplied at MCP initialization and through `ausdata://guide`.

## Sources

| Source | Coverage |
| --- | --- |
| Australian Bureau of Statistics (ABS) | Australian official statistics |
| Reserve Bank of Australia (RBA) | Australian economic and financial time series |
| DCCEEW Australian Energy Statistics | Australian electricity generation, Table O |
| Pacific Data Hub / SPC | Pacific statistical dataflows |
| World Bank | Global development indicators |
| OECD | International statistical dataflows |
| IMF | Global macroeconomic indicators |
| UN Comtrade | International goods trade |
