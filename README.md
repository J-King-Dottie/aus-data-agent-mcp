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

A new session fetches source catalogues; later searches use its cache. Reused sessions refresh successful catalogues after 24 hours. Failed sources are retried after 60 seconds, with stale or unavailable coverage disclosed. Metadata and observations are separate from catalogue discovery. ABS structures, PDH structures/codelists, and parsed domestic files have bounded one-hour caches; `get_metadata` and `retrieve` can bypass them with `forceRefresh`. Domestic file results disclose `source_cache_hit` and the original `source_fetched_at`, separately from the evidence file's `retrieved_at`. Retrieved files persist under `runtime/sessions/` until removed locally.

Use only this MCP's supported retrieval pathways for data tasks. If they cannot supply a suitable dataset, report that limit and ask before searching websites, other APIs, or separate publications. The server supplies [analyst guidance](AGENT_SYSTEM_PROMPT.md) at initialization and as `ausdata://guide`; MCP tool descriptions define call parameters. [AGENTS.md](AGENTS.md) covers development.


## Source scope and evidence

- ABS retrieves one metadata-selected anchor with other dimensions left open. Saved observations retain source dimension codes, attribute codes/labels, and the response structure.
- RBA supports time-series matrix CSV tables. Use a Series ID from metadata or `all`; dates are stored as ISO dates with their original labels retained.
- DCCEEW supports Australian Energy Statistics Table O. Select an explicit sheet group or sheet from metadata. Estimated years retain their source labels alongside normalized year codes. RBA and DCCEEW return the full selected slice; filter periods in the saved evidence rather than passing unsupported period parameters.
- World Bank, IMF and OECD accept country/area codes and optional year bounds. Geography defaults to Australia (`AUS`); `countries` and `allCountries` are mutually exclusive. A missing requested country is an error, so a comparison cannot silently lose a series.
- OECD rejects ambiguous country-period observations and changing units. It does not select preferred transformations or totals on the agent's behalf.
- Pacific Data Hub validates selected source dimension codes and retains raw missing/suppressed values, units, multipliers, status flags and annotations.
- Comtrade supports goods imports/exports at total, HS 2-digit and HS 4-digit levels. Its cube uses totals across secondary partners, customs procedures and transport modes, with those fixed dimensions disclosed in metadata. Reporter, primary partner, flow, product, frequency and years must be explicit. See the [official API parameters](https://github.com/uncomtrade/comtradeapicall#selection-criteria). The bundled `COMTRADE_METADATA.json` contains reference codes, not observations or a dataset catalogue. Maintainers can refresh it with `python scripts/build_comtrade_metadata.py`; discovery checks live import/export capability separately.

Unknown or unsupported parameters, malformed responses and empty retrievals fail explicitly. Source missing values remain null (including omitted trailing RBA CSV cells), and macro `source_row` fields retain publisher flags and raw values where supplied. Evidence JSON is written atomically; the manifest stays small and contains the absolute path to the complete result.

## Configuration

The server optionally reads a repository-local `.env`, without overriding existing environment variables. Set `PYTHON_DOTENV_DISABLED=1` to ignore it. No model credentials are used.

| Variable | Purpose |
| --- | --- |
| `AUSDATA_RUNTIME_DIR` | Local cache/evidence directory; defaults to this checkout's `runtime/`. |
| `AUSDATA_SESSION_ID` | Optional session to resume. Omit for a fresh session; never share one ID across concurrent server processes. |
| `AUSDATA_CATALOG_TTL_SECONDS` | Successful catalogue lifetime; positive integer, default `86400`. |
| `MACRO_TIMEOUT_SECONDS` | Macro/PDH request timeout, 1–600 seconds, default `120`. |
| `COMTRADE_API_KEY` | Optional Comtrade subscription key. Without it, retrieval uses the public preview API. |
| `ABS_API_BASE`, `WORLDBANK_BASE_URL`, `IMF_BASE_URL`, `OECD_BASE_URL`, `PDH_BASE_URL`, `COMTRADE_BASE_URL` | Optional source endpoint overrides; the Comtrade override applies to authenticated retrieval. |

Retrieved files remain local until removed. The calling agent needs access to the MCP server's filesystem. Source availability and network limits can affect live requests.

## Development and verification

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q ausdata_mcp scripts
ruff check ausdata_mcp scripts tests
ruff format --check ausdata_mcp scripts tests
```

The deterministic suite uses mocked provider responses and includes an actual stdio MCP launch from a temporary working directory without model credentials. Blocking provider work runs in worker threads so independent tool calls can proceed concurrently. CI runs the offline checks on Python 3.11, 3.12 and 3.13.

Opt-in live checks use the same three tools and verify saved artifacts. Run a subset, or all providers together:

```bash
python scripts/smoke_mcp.py --live --live-abs --live-pacific --live-domestic --live-macro
```

`--live` checks World Bank; the other flags check ABS, PDH, RBA/all discovered DCCEEW sheet groups, and IMF/OECD/Comtrade respectively. Live failures may reflect provider/network conditions; they are separate from offline regressions. `python scripts/build_unified_catalog.py` measures a cold discovery refresh and cached search. Analyst evaluation questions remain in [benchmarks/](benchmarks/README.md).
