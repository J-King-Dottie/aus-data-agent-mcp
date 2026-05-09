# Aus Data Agent MCP

A unified MCP server for AI agents working with Australian public data, plus global macro sources for context and comparison.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).

The project is built for evidence-led analysis:

```text
question -> AI-written FTS queries over the unified catalog
-> shortlist candidate datasets -> inspect metadata when needed
-> retrieve source data -> inspect/narrow artifacts -> analyze
```

The MCP does data discovery and retrieval. Analyst behavior lives in [MCP_ANALYST.md](MCP_ANALYST.md), and development guardrails live in [AGENTS.md](AGENTS.md).

## For AI Agents

Start with these files:

- [MCP_ANALYST.md](MCP_ANALYST.md): how to use the MCP as a careful economic/statistical analyst.
- [AGENTS.md](AGENTS.md): project architecture and development rules.
- [backend/app/unified_mcp_server.py](backend/app/unified_mcp_server.py): MCP tool surface.
- [.mcp.json](.mcp.json): project-scoped MCP config.
- [UNIFIED_CATALOG_FULL.json](UNIFIED_CATALOG_FULL.json): checked-in unified catalog snapshot.

Core tool flow:

```text
search_catalog -> get_metadata when needed -> retrieve -> inspect_artifact -> narrow_artifact
```

Search results are candidate pools. The AI analyst should choose datasets from the shortlist, inspect structure, retrieve real data, and only then answer.

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

The checked-in catalog is a discovery index, not a raw data mirror. Large retrieved results are stored as runtime artifacts so agents can inspect and narrow before analysis.

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

The repo still includes the app code in `frontend/` and the supporting backend code in `backend/`. The app is being developed as an interface over the MCP/retrieval stack, but the public-ready surface for agents is currently the MCP plus the analyst instructions.

- Frontend dev server: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:5000`

Daily local development from PowerShell with WSL:

```powershell
wsl bash -lc "cd /home/projects/abs-mcp && ./start-backend-wsl.sh"
wsl bash -lc "cd /home/projects/abs-mcp/frontend && npm run dev -- --host 127.0.0.1 --port 3000"
```

## Smoke Checks

```bash
python3 -m py_compile backend/app/*.py
```
