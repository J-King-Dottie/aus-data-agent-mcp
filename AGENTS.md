# Project Agent Guide

This project is a polished, agent-facing MCP product for Australian public data with global macro context. The web app is a separate interface layer in development.

## Architecture

- `backend/app/unified_mcp_server.py` is the MCP entrypoint. Keep it focused on catalogue search, metadata inspection, retrieval, artifact inspection, and artifact narrowing.
- `backend/app/unified_catalog.py` loads the unified catalogue and SQLite FTS index used for shortlist discovery.
- `backend/app/domestic_data.py` contains ABS and Australian domestic retrieval.
- `backend/app/macro_data.py` contains OECD, World Bank, IMF, RBA, UN Comtrade, and related macro retrieval.
- `skills.md` is the single source of truth for analyst behavior, query expansion, dataset selection, evidence judgment, calculations, charting, caveats, and response standards.
- `.mcp.json` is the project-scoped MCP configuration for agents that clone the repo.
- `frontend/` and the web backend are the app layer. They should sit over the MCP/retrieval stack, not redefine the core analyst workflow.

## Product Shape

Keep the same broad shape as the Pacific Data Hub Agent MCP:

```text
README.md -> agent entrypoint and quick start
AGENTS.md -> project architecture and development guardrails
skills.md -> analyst behavior and evidence standards
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

- Do not put analyst judgment into the MCP server. The MCP exposes data capabilities; `skills.md` tells the AI analyst how to use them.
- When changing analysis behavior, chart choice, evidence standards, caveats, or response style, update `skills.md` in the same change.
- Do not create duplicate prompt/guide files unless there is a strong runtime need. If one is added, it must point back to `skills.md` and avoid conflicting instructions.
- Do not add topic-specific report routes. Labour, CPI, trade, housing, energy, population, financial-market, and macro-comparison questions should use the same general workflow.
- Prefer live public-source retrieval where practical. Do not add raw data mirrors unless there is a clear operational reason.
- Keep custom Australian sources inside the same domestic catalogue and retrieval flow where possible.
- Treat catalogue search results as candidate pools. The AI analyst selects relevant datasets, then inspects metadata/structure before retrieval.
- For Australian domestic questions, prefer ABS or another direct Australian official source when it can answer the question. Use macro sources for comparison, context, or when domestic data cannot answer directly.
- Before comparing series, align geography, period, frequency, seasonal treatment, units, and definitions.
- If a change affects MCP usage, check the direct MCP path. If it affects the app, also check the app path.

## Before Merging MCP Changes

- Confirm `README.md`, `AGENTS.md`, `skills.md`, and `.mcp.json` still describe the same architecture.
- Confirm the MCP tool descriptions match the implemented behavior.
- Confirm no source-specific change bypasses the shortlist, inspect, retrieve, narrow, analyze pattern without a clear reason.
- Run compile checks for touched Python modules.
