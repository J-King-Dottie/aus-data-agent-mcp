# Nisaba / Aus Data Agent MCP

Nisaba is an AI-assisted economic modelling workspace built over a unified MCP server for Australian public data, with global macro sources for context and comparison.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).

Built on existing open source work including [seansoreilly/mcp-server-abs](https://github.com/seansoreilly/mcp-server-abs) and [hanlulong/openecon-data](https://github.com/hanlulong/openecon-data).

The project began as an MCP MVP and has been elevated into a structured agent system for data discovery, variable validation, durable replay, and executable model-building. It uses the Agent SDK to let the AI write and run code where useful, but wraps that ability in a controlled workflow: public-source retrieval, exact narrowing, user validation, compact saved data, executable refresh code, project-scoped calculations, and a notebook-style model canvas.

The core workflow is evidence-led variable validation:

```text
user asks for a metric -> search candidate public data -> inspect metadata
-> retrieve source data -> inspect raw artifact -> narrow to one exact variable
-> show validation preview -> user approves
-> save compact data plus executable refresh code
-> use validated variables in executable project models
```

The MCP does data discovery and retrieval. Agent behaviour lives in [AGENT_SYSTEM_PROMPT.md](AGENT_SYSTEM_PROMPT.md), and development guardrails live in [AGENTS.md](AGENTS.md).

## For AI Agents

Start with these files:

- [AGENT_SYSTEM_PROMPT.md](AGENT_SYSTEM_PROMPT.md): the agent system prompt for careful economic/statistical analysis.
- [AGENTS.md](AGENTS.md): project architecture and development rules.
- [backend/app/unified_mcp_server.py](backend/app/unified_mcp_server.py): MCP tool surface and tool descriptions.
- [.mcp.json](.mcp.json): project-scoped MCP config.
- [UNIFIED_CATALOG_FULL.json](UNIFIED_CATALOG_FULL.json): checked-in unified catalog snapshot.

Core MCP validation flow:

```text
one variable request -> search_catalog -> choose one dataset -> get_metadata
-> retrieve -> inspect_artifact -> narrow_artifact
-> validation summary -> save_validated_variable
```

Search results are candidate pools. The AI analyst chooses one dataset for one variable, inspects structure, retrieves real source data, narrows it to the minimum exact slice, and asks the user to approve before saving.

The MCP tools are intentionally single-call and serial:

- no batch catalog searches
- no batch metadata inspection
- no batch retrieval
- no batch artifact inspection
- no batch or parallel artifact narrowing

If a user asks for two columns, such as dwelling completions for houses and apartments, the app should validate them as two variables. Each variable gets its own exact narrowed recipe. After approval, later analysis can replay both recipes and join them into one table or spreadsheet.

Runtime artifacts are temporary working files. Validated variables are durable records containing compact approved data and executable refresh code, with enough source and transformation context to recreate the variable without repeating the full discovery process. Future runs should use saved data by default, and refresh only when the user asks to rerun the approved recipe from source.

## Product Architecture

Nisaba has three connected layers:

- **Data MCP:** catalogue search, metadata inspection, source retrieval, artifact inspection, and artifact narrowing across ABS, Australian domestic sources, and global macro sources.
- **Agent SDK orchestration:** project-hydrated sessions, progress reporting, code interpreter use, web search for research-derived variables, MCP tool calls, trace capture, retry policy, compact memory, and typed tools for saved variables and model graphs.
- **Modelling workspace:** Supabase-authenticated multi-tenant projects, validated-variable library, chat history, project memory, executable graph nodes, calculated-series cache, chart previews, and Excel exports.

The durable modelling loop is:

```text
validated variables -> named calculation/output nodes -> project calculated cache
-> chart/model inspection -> refreshed from saved data and executable logic when needed
```

Saved validated variables are source evidence. Project `node_data` stores chart-ready data for each visible model node; it is derived inspection data, not source evidence.

## Runtime and Scaling Shape

- Supabase Auth and RLS scope projects, chat messages, usage, and validated variables by `user_id`.
- The backend accepts chat requests asynchronously and runs generation in background tasks.
- One conversation has one active generation at a time; separate conversations/projects can run concurrently.
- Each Agent SDK run gets its own code container, MCP subprocess, runtime artifact directory, and trace file.
- Tool calls inside a single agent run are intentionally serial for evidence control: discover, inspect, retrieve, narrow, then validate.
- Heavy retrieval artifacts are cleared after variable validation; compact project memory and compact model state carry continuity forward.
- Validated-variable refresh uses saved executable code. Model-node charting reuses project calculated cache when current, and recomputes from saved upstream data when needed.

Current production hardening priorities are operational rather than architectural: external worker queues for multi-instance deployment, explicit per-user/project rate limits, stronger observability dashboards, and formal migration management around the evolving Supabase schema.

## Data Coverage

The checked-in unified catalog currently includes `31,309` entries:

| Provider | Catalog entries | Typical use |
| --- | ---: | --- |
| World Bank | 28,413 | Global macro, development, population, poverty, education, health, trade, energy, climate, finance, governance |
| OECD | 1,470 | International comparisons, regions, labour, education, health, productivity, government, transport, income, prices |
| ABS | 1,221 | Australian official statistics: labour, population, CPI, national accounts, business, trade, housing, census, health, agriculture, industry |
| IMF | 132 | Macro-financial context: debt, money, credit, trade categories, AI preparedness, financial openness |
| Reserve Bank of Australia | 71 | Rates, exchange rates, money, credit, bank balance sheets, payments, financial markets |
| DCCEEW | 1 | Australian Energy Statistics Table O: electricity generation by fuel type |
| UN Comtrade | 1 | Goods imports/exports by partner and HS commodity code |

Useful discovery keywords:

`ABS`, `Australian Bureau of Statistics`, `labour force`, `employment`, `unemployment`, `CPI`, `inflation`, `wages`, `population`, `migration`, `births`, `deaths`, `national accounts`, `GDP`, `household spending`, `retail`, `building approvals`, `housing`, `lending`, `business indicators`, `international trade`, `exports`, `imports`, `census`, `agriculture`, `energy generation`, `electricity by fuel`, `RBA`, `cash rate`, `exchange rates`, `interest rates`, `credit`, `financial institutions`, `OECD`, `World Bank`, `WDI`, `IMF`, `Comtrade`, `HS code`, `partner country`.

## Architecture

- `backend/app/unified_mcp_server.py`: unified MCP server.
- `backend/app/unified_catalog.py`: catalog loading and SQLite FTS search.
- `backend/app/domestic_data.py`: ABS and Australian domestic retrieval.
- `backend/app/macro_data.py`: OECD, World Bank, IMF, RBA, UN Comtrade, and related macro retrieval.
- `scripts/build_unified_catalog.py`: rebuilds the unified catalog and FTS index.
- `frontend/`: app layer being developed on top of the MCP.

The checked-in catalog is a discovery index, not a raw data mirror. Retrieved results are runtime artifacts used only to inspect and narrow one variable. Raw retrieved artifacts are inspect-only; only narrowed artifacts may be used for calculation or validation previews.

## Quick Start

```bash
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
python3 -m pip install -r backend/requirements.txt
```

Run the MCP server:

```bash
python -m backend.app.unified_mcp_server
```

Example project MCP config:

```json
{
  "mcpServers": {
    "ausdata": {
      "command": "python",
      "args": ["-m", "backend.app.unified_mcp_server"],
      "cwd": "."
    }
  }
}
```

## Refreshing Catalog Assets

Refresh the unified catalog and FTS index:

```bash
python3 scripts/build_unified_catalog.py
```

Refresh the local UN Comtrade metadata bundle:

```bash
python3 scripts/build_comtrade_metadata.py
```

## App Layer

The repo includes the app code in `frontend/` and the supporting backend code in `backend/`. The app is the interactive workspace over the MCP/retrieval stack:

- Left pane: modelling projects and project switching.
- Centre pane: AI workspace for question definition, discovery, variable construction, model execution, and results.
- Right pane: notebook-style model canvas showing validated-variable nodes, calculation/output nodes, node descriptions, dependency arrows, and chart previews.
- Supabase schema: [supabase_modelling_workspace.sql](supabase_modelling_workspace.sql) creates projects, chat history, AI usage, validated variables, active project-variable links, model graph state, calculated model cache, and export records.

The app should stay a thin interface over the same disciplined agent workflow. It should not bypass the MCP discovery/retrieval path for official data, and it should not duplicate validated source data into model calculations except as explicit project-level derived cache.

- Frontend dev server: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:5000`

Fast local development from PowerShell with WSL opens two visible terminal windows, one for the frontend and one for the backend, both with auto-reload:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-visible-dev.ps1 -SkipInstall
```

If running from outside the repo root, use the UNC path:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\projects\abs-mcp\start-visible-dev.ps1" -SkipInstall
```

Then verify both endpoints:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:3000 -TimeoutSec 5
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:5000 -TimeoutSec 5
```

If stale terminals or stuck ports need cleanup, run the reset wrapper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\projects\abs-mcp\restart-visible-dev.ps1"
```

If dependencies need to be reinstalled, omit `-SkipInstall` on `start-visible-dev.ps1` or pass `-Install` to `restart-visible-dev.ps1`.

Manual one-terminal-per-command fallback:

```powershell
wsl bash -lc "cd /home/projects/abs-mcp && ./scripts/dev-backend-wsl.sh"
wsl bash -lc "cd /home/projects/abs-mcp && ./scripts/dev-frontend-wsl.sh"
```

## Smoke Checks

```bash
python3 -m py_compile backend/app/*.py backend/app/storage/*.py scripts/build_unified_catalog.py
npm --prefix frontend run build
```
