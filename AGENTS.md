# Project Agent Guide

This project is an MCP for public-data retrieval and analysis support. The calling agent owns reasoning, calculations and output formats. The web app and its orchestration have been removed.

## Architecture

- `ausdata_mcp/server.py`: three MCP tools, threaded dispatch, parameter validation, protocol instructions, resource and prompt.
- `ausdata_mcp/artifacts.py`: atomic JSON evidence storage and bounded retrieval manifests.
- `ausdata_mcp/unified_catalog.py`: normalized session catalogue, atomic generation checks and one SQLite FTS index.
- `ausdata_mcp/catalog_sources.py`: live source-specific discovery adapters; no dataset observations.
- `ausdata_mcp/catalog_refresh.py`: parallel refresh, expiry, retry and source coverage status.
- `ausdata_mcp/runtime.py`: common runtime/session identity for cache and artifacts.
- `ausdata_mcp/domestic_data.py`: ABS and direct Australian sources, with bounded metadata/file caches.
- `ausdata_mcp/rba_tables.py` and `ausdata_mcp/energy_workbook.py`: in-process official-file parsers; no subprocess or spreadsheet-app dependencies.
- `ausdata_mcp/macro_data.py`: OECD, World Bank, IMF and UN Comtrade retrieval.
- `ausdata_mcp/pacific_data.py`: Pacific Data Hub/SPC live catalogue, SDMX structure/codelists and validated retrieval, adapted from the Pacific Data Hub Agent MCP.
- `ausdata_mcp/data_config.py`: source configuration without model or database dependencies.
- `AGENT_SYSTEM_PROMPT.md`: single source of truth for analyst behavior, evidence standards, calculations, caveats and presentation guidance. Supplied to clients at MCP initialization and through `ausdata://guide`.
- Tool descriptions: single source of truth for call mechanics.
- `.mcp.json`: project-scoped stdio wiring for Claude Code and compatible clients. Codex registration is documented in README.
- `scripts/run_mcp.py`: absolute-path launcher independent of the client's working directory.
- `benchmarks/`: fixed agent test questions and reviewed results; no benchmark logic belongs in MCP tools.

## Development rules

- Keep analyst judgment in `AGENT_SYSTEM_PROMPT.md`, not source adapters or tool routing. Update it in the same change when analysis behavior changes.
- Preserve search -> shortlist selection -> inspect metadata -> retrieve -> agent reads and analyses the saved data. Reuse known metadata and retrieval files where appropriate. Keep only the three core MCP tools: `search_catalog`, `get_metadata`, `retrieve`.
- Never require a particular model vendor, app, account, database, output format or approval ceremony for ordinary data analysis.
- Keep data acquisition inside the MCP's supported pathways unless the user explicitly authorizes another route. If no suitable dataset is retrievable here, the agent should report that specific limitation and ask before seeking workbooks, websites or other APIs; do not imply the publisher has no data.
- Do not add topic-specific report routes or duplicate analyst prompts. `CLAUDE.md` is only a pointer for that harness.
- Prefer live official-source retrieval. Discovery fetches live source lists into a disposable normalized file and FTS cache, not a checked-in catalogue or raw data mirror.
- All providers, including Pacific, share the same session-scoped catalogue and FTS text-match order. Return up to 50 candidates by default without scores or rank labels; the agent selects suitable data after inspecting definitions and coverage. Default sessions start fresh; cache successful source lists for 24 hours, disclose stale/unavailable sources and retry failures after 60 seconds. Keep dataset metadata/codelists separate from catalogue discovery.
- RBA and DCCEEW routing must use live discovered download URLs. Validate file schemas; never accept an unknown layout as valid observations. Comtrade discovery is a supported-cube descriptor validated against live trade flows, not a complete indicator catalogue.
- Preserve PDH dimension codes, UNIT_MULT, UNIT_MEASURE, OBS_STATUS, raw suppressed values and source annotations. Metadata pagination must disclose remaining codes. Dataset IDs are namespaced as `pdh::agency::dataflow::version`.
- Keep Australian custom sources in the domestic catalogue/retrieval flow. Align geography, period, frequency, seasonal treatment, units and definitions before comparisons.
- Tool output must disclose truncation/pagination. Retrieval returns a bounded manifest with an absolute path to complete JSON evidence; it must never inline the full dataset, silently drop requested series or return empty data as successful evidence.
- Agent presentation guidance should prefer in-chat charts and answers and avoid optional PNG, CSV, Excel or report files by default. The MCP's saved retrieval JSON is working evidence, not an automatic deliverable; honor explicit user requests for export files.
- Source adapters must reject invalid requested codes rather than silently dropping them. Keep errors specific enough for an agent to revisit metadata and repair the request; do not make source substitutions inside the MCP.
- Preserve source references and retrieval timestamps in the saved data and manifest. Use unique session-scoped file paths; filtering and derived-output lineage belong to the calling agent.
- Keep stdout exclusively for MCP protocol messages. Diagnostics and subprocess progress go to stderr.
- Independent requests may run concurrently. Keep shared index creation atomic. Explicit session IDs must not be shared by concurrent server processes.

## Verification

- Run `python -m unittest discover -s tests -v` and `python -m compileall -q ausdata_mcp scripts`.
- Check the real stdio MCP path from a different working directory, without model credentials or app packages.
- Run `ruff check ausdata_mcp scripts tests` and `ruff format --check ausdata_mcp scripts tests` with `requirements-dev.txt` installed.
- Keep deterministic regressions offline; use opt-in live smoke checks to distinguish provider/network failures from code failures.
- Confirm README, this file, AGENT_SYSTEM_PROMPT.md, llms.txt, tool descriptions and .mcp.json agree.
- This checkout lives in WSL at `/home/projects/abs-mcp`, accessible on Windows through `\\wsl.localhost\Ubuntu\home\projects\abs-mcp`.
