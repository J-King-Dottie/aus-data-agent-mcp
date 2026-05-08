## Nisaba

Nisaba is an open source MCP data harness for Australian public data, with global macro sources for context and comparison.

It builds on existing open source projects including [mcp-server-abs](https://github.com/seansoreilly/mcp-server-abs) and [openecon-data](https://github.com/hanlulong/openecon-data). We continue to expand it. 

The goal is simple: one catalog, one MCP surface, and source-specific retrieval adapters behind it.

If you are technical, clone the repo, add your API key, and run it locally. If you are not, we have built a simple hosted web app on top of the MCP server. Log in, ask a question, get a grounded answer. That version is not free; we pass through the raw AI cost and add 10% to cover hosting. The repo is fully open source either way.

Here is what is currently plugged into the unified catalog:

| Provider | Datasets |
| --- | ---: |
| ABS | 1,221 |
| DCCEEW | 1 |
| RBA | 71 |
| OECD | 1,464 |
| World Bank | 28,377 |
| IMF | 132 |
| UN Comtrade | 1 |

This product is heavily vibecoded and tested by outcomes rather than code review. It does not work perfectly every time, but it works well most of the time.

We want people to suggest additional integrations so the system can grow into the strongest open source for AI-driven and Australian focused data analysis in the world.

Produced by [Dottie AI Studio](https://dottieaistudio.com.au/).

## What It Does

- Shortlists datasets across ABS, RBA, DCCEEW, OECD, World Bank, IMF, and UN Comtrade.
- Retrieves data through source-specific adapters instead of guessed URLs or fake table keys.
- Stores large results as artifacts, then supports inspection and narrowing before analysis.
- Runs directly as MCP or through the included web app.

## How It Works

Nisaba uses one built catalog snapshot for discovery. `search_catalog` runs local SQLite FTS over that catalog, returns a candidate pool, and the agent chooses the best dataset before source-specific retrieval begins.

The flow is:

```text
catalog FTS -> AI dataset selection -> metadata when needed -> source adapter -> artifact -> inspect/narrow -> analysis
```

The checked-in catalog makes first-run discovery fast and reliable after clone. It is intentionally included, not a raw data mirror.

## Requirements

- Python
- Node.js and npm
- `OPENAI_API_KEY` set in `.env`

Example `.env`:

```env
OPENAI_API_KEY=your_key_here
```

## Direct MCP Use

The repo can be used directly as MCP, not just through the hosted app.

- Unified MCP server: `python -m backend.app.unified_mcp_server`
- Core tool flow: `search_catalog` -> `get_metadata` when needed -> `retrieve` -> `inspect_artifact` -> `narrow_artifact` when needed

If your local MCP client supports a project-scoped `.mcp.json`, the repo includes one at the root with the unified server already defined.

Some catalog and metadata assets are built snapshots and need to be refreshed manually when needed:

- Refresh the unified catalog and FTS index:

```bash
python3 scripts/build_unified_catalog.py
```

- Refresh the local UN Comtrade metadata bundle:

```bash
python3 scripts/build_comtrade_metadata.py
```

## Local Dev

- Frontend dev server with HMR: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:5000`

Use PowerShell with WSL:

Daily use
1. Open terminal 1 and run:
   wsl bash -lc "cd /home/projects/abs-mcp && ./start-backend-wsl.sh"
2. Open terminal 2 and run:
   wsl bash -lc "cd /home/projects/abs-mcp/frontend && npm run dev -- --host 127.0.0.1 --port 3000"

Full reinstall
1. Open terminal 1 and run:
   wsl bash -lc "cd /home/projects/abs-mcp && rm -rf .venv-wsl && python3 -m venv .venv-wsl && source .venv-wsl/bin/activate && python3 -m pip install -r backend/requirements.txt"
2. Open terminal 2 and run:
   wsl bash -lc "cd /home/projects/abs-mcp/frontend && rm -rf node_modules package-lock.json && npm install && npm run dev -- --host 127.0.0.1 --port 3000"
