# Codex Project Instructions

## Project Overview

ASR Local is a local desktop speech-to-text application built around an
Electron shell, Vue + TypeScript renderer, TypeScript desktop services, and a
Python Workflow Runtime for ASR/transcription workflows.

Key paths:

- `apps/desktop-electron/`: active Electron desktop application.
- `apps/desktop-electron/src/`: Vue UI, stores, feature views, and frontend state.
- `apps/desktop-electron/electron/`: Electron Main, Preload, desktop services, and Python runtime client.
- `apps/worker-python/`: Python Workflow Runtime and its tests.
- `config/`: runtime configuration.
- `models/`: local model weights; do not commit.
- `outputs/`: generated transcripts, summaries, logs, and local output artifacts; do not scan broadly.
- `docs/`: design and legacy documentation.

## Preflight

Before editing:

- Confirm the working directory is the current repository root that contains this `AGENTS.md`, and report the actual absolute path when it differs across machines.
- Check current Git status if available. If Git is unavailable or inconsistent, say so and continue from filesystem evidence.
- Preserve existing uncommitted changes; do not overwrite unrelated work.
- Identify which layer the task touches: Vue UI, Pinia store, Electron Main/Preload, Python runtime, history/logging, build/release, or model/config.
- Search relevant source paths with `rg`. Exclude `outputs`, `models`, `.venv*`, `node_modules`, `apps/desktop-electron/runtime`, `apps/desktop-electron/dist`, `apps/desktop-electron/dist-electron`, `apps/desktop-electron/release-electron`, `tmp`, and `__pycache__` by default. Inspect excluded paths only when directly relevant, using a narrow scope.
- For screenshot-reported layout bugs, inspect the relevant DOM or rendered view. For incorrect state or interaction behavior, inspect logs and the event/command chain before changing UI state.

## Common Commands

Desktop commands from `apps/desktop-electron`:

```powershell
npm run typecheck
npm run build
npm run electron:dev
npm run electron:package
```

Choose validation by the affected layer:

| Layer | Validation |
| --- | --- |
| Vue / Pinia | `npm run typecheck`; `npm test -- <related-test-path>` for affected behavior |
| Electron Main / Preload | `npx tsc -p tsconfig.electron.json --noEmit`; `npm run electron:compile` when compiled output and runtime dependency copying are needed |
| Python runtime | From the repository root, use the existing test environment: `python -m pytest apps/worker-python/tests/<related-test-file> -q` |
| Release / packaging | `npm run electron:package`, then launch the packaged application and verify the affected workflow |

`npm run build` checks and builds the renderer; it does not compile Main/Preload.
Use `npm test` or, from the repository root, `python -m pytest -q` when the
change warrants broader regression coverage. Python requires 3.11 or newer;
verify that the selected environment has pytest and the required dependencies.
Run focused checks first and broaden them for affected dependencies or unresolved
failures. Documentation-only changes need content/diff review, not application tests.

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

- Read only the code and documentation needed for the task. Scale investigation to the uncertainty and impact of the change.
- For cross-layer bugs, trace the affected chain: UI action -> store -> preload bridge -> Electron Main -> Python runtime -> event/log update -> UI state.
- For broad refactors or performance reviews, first identify the main risks and affected boundaries. Do not force a fixed number of findings. Continue into implementation when the user has authorized it; assessment-only requests remain read-only.
- For specific UI or runtime bugs, implement after focused investigation and run the relevant checks above.
- For release fixes, rebuild through Electron Builder and verify actual startup rather than only compile success.
- Complete authorized implementation through relevant validation and repair of failures caused by the change. Do not stop at the first implementation for routine approval. Report passed, failed, and unverified checks distinctly; if blocked, explain the evidence and the smallest missing input.

## Sub-Agent Use

Use a single agent for simple tasks. Delegate only bounded, independent work
when parallel progress is likely to outweigh coordination overhead.

- Read-only investigation is the default. Split by the question being answered; UI/store, Desktop/IPC, and Python/logs are useful boundaries when relevant.
- Independent implementation may be delegated within the authorized scope when file ownership and acceptance criteria are explicit. Do not let agents edit overlapping files or revert others' changes.
- Each assignment must include the goal, relevant paths, constraints, acceptance criteria, and expected evidence. Return cross-layer dependencies, conflicting evidence, or unclear scope to the main thread.
- When model selection is available, prefer Astra for ambiguous requirements, cross-layer diagnosis, lifecycle/concurrency reasoning, architectural decisions, and integration. Prefer GPT-6 Luna for focused inspection, configuration checks, independent tests, and well-specified local changes. Treat this as a preference, not a required model switch; use supported tools and report actual selection accurately.
- The main thread owns scope, shared interface decisions, review, and final integration. Validate the combined result before claiming completion.

## Handoff Format

Scale the handoff to the task. For small changes, summarize the result, changed
files, and validation. For substantial tasks, include the goal, affected layers,
key decisions, changed files, checks run with results, and unverified areas.
Include `cwd` when it differs from the expected workspace, files read when they
help explain the evidence, and a recommended next step only when work remains.
