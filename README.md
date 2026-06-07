# Aus Data Agent MCP

A unified MCP server for AI agents working with Australian public data, plus global macro sources for context and comparison.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).

Built on existing open source work including [seansoreilly/mcp-server-abs](https://github.com/seansoreilly/mcp-server-abs) and [hanlulong/openecon-data](https://github.com/hanlulong/openecon-data).

The project is built for evidence-led variable validation:

```text
user asks for a metric -> search one candidate dataset -> inspect metadata
-> retrieve one source slice -> inspect one raw artifact -> narrow to one exact variable
-> show validation preview -> user approves -> save replayable variable recipe
```

The MCP does data discovery and retrieval. Agent behaviour lives in [AGENT_SYSTEM_PROMPT.md](AGENT_SYSTEM_PROMPT.md), and development guardrails live in [AGENTS.md](AGENTS.md).

## For AI Agents

Start with these files:

- [AGENT_SYSTEM_PROMPT.md](AGENT_SYSTEM_PROMPT.md): the agent system prompt for careful economic/statistical analysis.
- [AGENTS.md](AGENTS.md): project architecture and development rules.
- [backend/app/unified_mcp_server.py](backend/app/unified_mcp_server.py): MCP tool surface and tool descriptions.
- [.mcp.json](.mcp.json): project-scoped MCP config.
- [UNIFIED_CATALOG_FULL.json](UNIFIED_CATALOG_FULL.json): checked-in unified catalog snapshot.

Core tool flow:

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

Runtime artifacts are temporary working files. Validated variables are durable recipes containing the source, dataset, exact retrieval arguments, exact narrowing filters, transformation logic, variable type, and a short recreation summary. Future runs should replay recipes against source data rather than reusing broad old artifacts or discovery transcripts.

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

The repo still includes the app code in `frontend/` and the supporting backend code in `backend/`. The app is being developed as an interface over the MCP/retrieval stack, but the public-ready surface for agents is currently the MCP plus the agent system prompt.

The web app is evolving into an AI-assisted economic modelling workspace:

- Left pane: modelling projects and project switching.
- Centre pane: AI workspace for question definition, discovery, variable construction, model execution, and results.
- Right pane: compact model builder showing validated variables, assumptions, and mathematical links.
- Supabase schema: [supabase_modelling_workspace.sql](supabase_modelling_workspace.sql) creates projects, chat history, validated variables, assumptions, model graph, runs, run joins, and export records.

- Frontend dev server: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:5000`

Daily local development from PowerShell with WSL:

```powershell
wsl bash -lc "cd /home/projects/abs-mcp && ./scripts/dev-backend-wsl.sh"
wsl bash -lc "cd /home/projects/abs-mcp && ./scripts/dev-frontend-wsl.sh"
```

To open two visible terminal windows, one for the frontend and one for the backend, both with auto-reload:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-visible-dev.ps1 -SkipInstall
```

## Smoke Checks

```bash
python3 -m py_compile backend/app/*.py backend/app/storage/*.py scripts/build_unified_catalog.py
npm --prefix frontend run build
```
