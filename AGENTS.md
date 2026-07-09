# Codex Project Instructions

## Project Overview

ASR Local is a local desktop speech-to-text application built around a
Tauri 2 shell, Vue + TypeScript frontend, Rust desktop commands, and Python
worker processes for ASR/transcription workflows.

Key paths:

- `apps/desktop-tauri/`: active desktop application.
- `apps/desktop-tauri/src/`: Vue UI, stores, feature views, and frontend state.
- `apps/desktop-tauri/src-tauri/src/`: Rust commands, worker client, history, logging, and desktop integration.
- `apps/desktop-tauri/src-tauri/capabilities/`: Tauri permission manifests.
- `config/`: runtime configuration.
- `models/`: local model weights; do not commit.
- `outputs/`: generated transcripts, summaries, logs, and local output artifacts; do not scan broadly.
- `docs/`: design and legacy documentation.

## Preflight

Before editing:

- Confirm the working directory is `E:\claude-projects\asr-local`.
- Check current Git status if available. If Git is unavailable or inconsistent, say so and continue from filesystem evidence.
- Identify which layer the task touches: Vue UI, Pinia store, Tauri/Rust commands, Python worker, history/logging, build/release, or model/config.
- Search with `rg`, excluding `outputs`, `models`, `.venv*`, `apps/desktop-tauri/node_modules`, `apps/desktop-tauri/dist`, `apps/desktop-tauri/src-tauri/target`, `tmp`, `rmeta*`, and `__pycache__`.
- For bugs reported from screenshots, inspect logs and the event/command chain before changing UI state.

## Common Commands

Frontend commands from `apps/desktop-tauri`:

```powershell
npm run typecheck
npm run build
npm run tauri:dev
npm run tauri:build
```

Rust checks from `apps/desktop-tauri/src-tauri`:

```powershell
cargo check
cargo test
```

User-facing launch scripts at repo root:

```text
启动听记助手.bat
开发模式启动.bat
构建听记助手.bat
```

When Cargo target permissions are blocked, use a workspace-local isolated
`CARGO_TARGET_DIR` instead of touching existing locked target directories.

## Development Rules

- Keep frontend state changes in the store when they affect worker lifecycle, lane state, history, or summary generation.
- Register event listeners before long initialization work so worker events are not missed.
- Do not scan `outputs` recursively unless the task is specifically about output history; skip build/cache folders inside it.
- For Tauri production issues, distinguish `cargo build --release` from `npm run tauri:build`; production releases must embed frontend assets through the Tauri build flow.
- For Tauri permission errors, update `src-tauri/capabilities` with the smallest required permission.
- Preserve local model/config assumptions; never move or delete model weights.
- GUI layout changes must be verified visually or by DOM/screenshot checks when a dev server or built app is available.

## Task Flow

Use this default flow:

1. For cross-layer bugs, map the full chain first: UI action -> store -> Tauri command -> Rust worker client -> Python worker -> event/log update -> UI state.
2. For broad refactors or performance reviews, do a read-only Top 5 assessment before implementation.
3. For specific UI or worker bugs, implement directly after preflight and run focused frontend/Rust checks.
4. For release fixes, rebuild through Tauri, replace the executable only when required, and verify actual startup rather than only compile success.

## Sub-Agent Use

Use sub-agents for independent read-only investigations:

- UI/store agent: Vue views, Pinia store, event registration, rendering.
- Desktop agent: Tauri commands, Rust permissions, worker process lifecycle.
- Worker/log agent: Python worker protocol, output files, logs, history scanning.

The main thread owns code edits and final integration. Do not let multiple agents edit overlapping frontend/store/Rust files.

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
