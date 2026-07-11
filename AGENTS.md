# Codex Project Instructions

## Project Overview

ASR Local is a local desktop speech-to-text application built around an
Electron shell, Vue + TypeScript renderer, TypeScript desktop services, and a
Python Workflow Runtime for ASR/transcription workflows.

Key paths:

- `apps/desktop-electron/`: active Electron desktop application.
- `apps/desktop-electron/src/`: Vue UI, stores, feature views, and frontend state.
- `apps/desktop-electron/electron/`: Electron Main, Preload, desktop services, and Python runtime client.
- `config/`: runtime configuration.
- `models/`: local model weights; do not commit.
- `outputs/`: generated transcripts, summaries, logs, and local output artifacts; do not scan broadly.
- `docs/`: design and legacy documentation.

## Preflight

Before editing:

- Confirm the working directory is the current repository root that contains this `AGENTS.md`, and report the actual absolute path when it differs across machines.
- Check current Git status if available. If Git is unavailable or inconsistent, say so and continue from filesystem evidence.
- Identify which layer the task touches: Vue UI, Pinia store, Electron Main/Preload, Python runtime, history/logging, build/release, or model/config.
- Search with `rg`, excluding `outputs`, `models`, `.venv*`, `apps/desktop-electron/node_modules`, `apps/desktop-electron/dist`, `apps/desktop-electron/dist-electron`, `apps/desktop-electron/release-electron`, `tmp`, and `__pycache__`.
- For bugs reported from screenshots, inspect logs and the event/command chain before changing UI state.

## Common Commands

Frontend commands from `apps/desktop-electron`:

```powershell
npm run typecheck
npm run build
npm run electron:dev
npm run electron:package
```

User-facing launch scripts at repo root:

```text
启动听记助手.bat
开发模式启动.bat
构建听记助手.bat
```

## Development Rules

- Keep frontend state changes in the store when they affect worker lifecycle, lane state, history, or summary generation.
- Register event listeners before long initialization work so worker events are not missed.
- Do not scan `outputs` recursively unless the task is specifically about output history; skip build/cache folders inside it.
- For Electron production issues, distinguish the Vite renderer build from `npm run electron:package`; releases must include renderer, Main, Preload and external Python runtime resources.
- Preserve local model/config assumptions; never move or delete model weights.
- GUI layout changes must be verified visually or by DOM/screenshot checks when a dev server or built app is available.

## Task Flow

Use this default flow:

1. For cross-layer bugs, map the full chain first: UI action -> store -> preload bridge -> Electron Main -> Python runtime -> event/log update -> UI state.
2. For broad refactors or performance reviews, do a read-only Top 5 assessment before implementation.
3. For specific UI or runtime bugs, implement directly after preflight and run focused frontend/Python checks.
4. For release fixes, rebuild through Electron Builder and verify actual startup rather than only compile success.

## Sub-Agent Use

Use sub-agents for independent read-only investigations:

- UI/store agent: Vue views, Pinia store, event registration, rendering.
- Desktop agent: Electron Main/Preload, IPC permissions, worker process lifecycle.
- Worker/log agent: Python worker protocol, output files, logs, history scanning.

The main thread owns code edits and final integration. Do not let multiple agents edit overlapping renderer/Main/Python files.

## Handoff Format

End substantial tasks with:

- `cwd`:
- Goal:
- Layer(s) touched:
- Files read:
- Files changed:
- Commands run:
- Validation result:
- Unverified areas:
- Key decisions:
- Recommended next prompt:
