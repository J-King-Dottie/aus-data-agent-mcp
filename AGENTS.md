# Project Agent Guide

This project is a polished, agent-facing MCP product for Australian public data with global macro context. The web app is a separate interface layer in development.

## Architecture

- `backend/app/unified_mcp_server.py` is the MCP entrypoint. Keep it focused on catalogue search, metadata inspection, retrieval, artifact inspection, and artifact narrowing.
- `backend/app/unified_catalog.py` loads the unified catalogue and SQLite FTS index used for shortlist discovery.
- `backend/app/domestic_data.py` contains ABS and Australian domestic retrieval.
- `backend/app/macro_data.py` contains OECD, World Bank, IMF, RBA, UN Comtrade, and related macro retrieval.
- `AGENT_SYSTEM_PROMPT.md` is the single source of truth for agent behavior, query expansion, dataset selection, evidence judgment, calculations, charting, caveats, and response standards.
- MCP tool descriptions are the single source of truth for how to call each MCP tool.
- `.mcp.json` is the project-scoped MCP configuration for agents that clone the repo.
- `frontend/` and the web backend are the app layer. They should sit over the MCP/retrieval stack, not redefine the core analyst workflow.

## Product Shape

Keep the same broad shape as the Pacific Data Hub Agent MCP:

```text
README.md -> agent entrypoint and quick start
AGENTS.md -> project architecture and development guardrails
AGENT_SYSTEM_PROMPT.md -> agent system prompt and evidence standards
.mcp.json -> MCP wiring for agents
MCP server -> source-specific data capabilities
```

The common workflow is:

```text
user question -> AI-written FTS queries -> catalogue shortlist
-> AI picks relevant data -> inspect metadata/structure
-> retrieve real data -> inspect/narrow data -> analyze from evidence
```

## Development Rules

- Do not put analyst judgment into the MCP server. The MCP exposes data capabilities; `AGENT_SYSTEM_PROMPT.md` tells the agent when and why to use them.
- When changing analysis behavior, chart choice, evidence standards, caveats, or response style, update `AGENT_SYSTEM_PROMPT.md` in the same change.
- Keep tool-call mechanics in MCP tool descriptions, not in the agent system prompt.
- Do not create duplicate prompt/guide files unless there is a strong runtime need. If one is added, it must point back to `AGENT_SYSTEM_PROMPT.md` and avoid conflicting instructions.
- Do not add topic-specific report routes. Labour, CPI, trade, housing, energy, population, financial-market, and macro-comparison questions should use the same general workflow.
- Prefer live public-source retrieval where practical. Do not add raw data mirrors unless there is a clear operational reason.
- Keep custom Australian sources inside the same domestic catalogue and retrieval flow where possible.
- Treat catalogue search results as candidate pools. The AI analyst selects relevant datasets, then inspects metadata/structure before retrieval.
- For Australian domestic questions, prefer ABS or another direct Australian official source when it can answer the question. Use macro sources for comparison, context, or when domestic data cannot answer directly.
- Before comparing series, align geography, period, frequency, seasonal treatment, units, and definitions.
- If a change affects MCP usage, check the direct MCP path. If it affects the app, also check the app path.

## Local Demo Terminals

- The repo is a WSL project at `/home/projects/abs-mcp`, exposed to Windows as `\\wsl.localhost\Ubuntu\home\projects\abs-mcp`.
- To open the visible frontend and backend demo terminals, use `restart-visible-dev.ps1` from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\projects\abs-mcp\restart-visible-dev.ps1"
```

- This reset launcher is the default for future agent use. It closes stale AusData/abs-mcp dev terminal wrappers, confirms WSL responds, then runs `start-visible-dev.ps1 -SkipInstall`.
- The visible terminals are expected to auto-refresh on code changes: the frontend script runs Vite dev server with HMR, and the backend script runs Uvicorn with `--reload --reload-dir backend`.
- Local frontend dev should call the backend through the Vite `/api` proxy, matching `dottie-ai-studio`. Do not keep `VITE_API_BASE_URL` set for the visible local frontend terminal, because that bypasses the proxy and causes browser CORS preflights to `:5000`.
- Do not hand-open ad hoc frontend/backend terminal commands unless the reset launcher fails and you have inspected the failure.
- If dependencies need to be reinstalled, run the same reset launcher with `-Install`.
- After launch, verify both endpoints before reporting success:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:3000 -TimeoutSec 5
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:5000 -TimeoutSec 5
```

## App Agent Context

- The chat UI should load Supabase project chat history by `user_id` and `project_id`; do not use `conversation_id` as a history boundary.
- Before an Agents SDK run, hydrate the SDK session from recent Supabase project chat for that `user_id` and `project_id`, then let `_agent_session_items_from_chat_history` keep the latest 5 user/assistant pairs plus recent workflow notes.
- Also inject `project_compact_memory` and `project_model_builder` into the high-level agent input. The model-builder state must include active validated variables, assumptions, nodes, and edges.
- Compact project memory is continuity only, not source evidence. Refresh it after the first useful run and then every 5 completed user/assistant pairs.

## Before Merging MCP Changes

- Confirm `README.md`, `AGENTS.md`, `AGENT_SYSTEM_PROMPT.md`, and `.mcp.json` still describe the same architecture.
- Confirm the MCP tool descriptions match the implemented behavior.
- Confirm no source-specific change bypasses the shortlist, inspect, retrieve, narrow, analyze pattern without a clear reason.
- Run compile checks for touched Python modules.
