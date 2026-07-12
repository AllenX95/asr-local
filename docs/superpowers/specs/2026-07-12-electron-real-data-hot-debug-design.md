# Electron Real-Data Hot Debugging Design（MOSS 部分已取代）

> MOSS-specific debugging paths in this historical design are superseded by the
> Qwen-only runtime design: `docs/superpowers/specs/2026-07-12-qwen-only-single-runtime-design.md`.

## Status

Approved direction: real-data hot debugging with an isolated-mode fallback.

This design addresses two connected problems:

1. A production MOSS workflow can remain at the persisted `transcribing` 8%
   checkpoint with no observable activity.
2. Rebuilding the approximately 2 GB NSIS installer is too slow for ordinary
   diagnosis and iteration.

The current live incident is `wf_556de266b299`. Its registry sequence remained
at 4 while the Python process accumulated no CPU time and the GPU remained at
0% utilization. The process was still alive. This proves that the displayed
8% is a real backend checkpoint, not a Vue rendering error, but the current
system does not expose which blocking MOSS phase is waiting.

## Goals

- Start the latest Electron, Vue, TypeScript, and Python source in seconds,
  without Electron Builder, NSIS, or copying the bundled Python runtime.
- Reproduce issues against the same configuration, credential store, workflow
  registry, output history, model paths, and Python dependencies as the
  installed application.
- Persist enough structured diagnostics to distinguish slow work from a hung
  runtime without exposing credentials.
- Show runtime exits and protocol failures in the UI instead of leaving a task
  silently at its last workflow checkpoint.
- Report activity heartbeats and named MOSS phases without inventing a false
  completion percentage.
- Preserve an isolated debug mode for destructive or synthetic testing.

## Non-goals

- Replacing the workflow v2 contract.
- Estimating model completion from elapsed wall-clock time.
- Making blocking model inference forcibly cancellable inside native code.
- Changing ASR or summary model behavior.
- Optimizing installer size or enabling automatic updates.
- Packaging a new NSIS installer during ordinary development.

## Chosen approach

### Real-data hot debug command

Add `npm run electron:debug` and a root launcher named
`快速调试听记助手.bat`.

The command will:

- compile Electron Main and preload TypeScript;
- start Vite with HMR;
- launch Electron against the Vite URL;
- force `ASR_LOCAL_V2_PIPELINE_MODE=production`;
- use the already-built `apps/desktop-electron/runtime/python/python.exe` by
  default, falling back to `apps/worker-python/.venv/Scripts/python.exe` only
  when the portable runtime is absent;
- set config and registry paths to `%APPDATA%/ASR Local`, matching the installed
  application;
- set outputs to `%USERPROFILE%/Documents/ASR Local/outputs`;
- write Electron Main and Python worker logs beneath
  `%APPDATA%/ASR Local/logs`;
- keep the terminal open so compilation failures and live stderr remain visible.

The installed application must be closed before starting this mode. The
launcher will detect another ASR Local process and fail with an actionable
message instead of opening the same SQLite registry from two runtimes.

### Isolated fallback

Add `npm run electron:debug:isolated`. It uses a repository-local ignored root,
`tmp/electron-debug`, for config, registry, logs, and outputs. It still uses the
real production Python pipeline and real model paths. This mode is intended for
fixture testing, retries, and experiments that must not mutate real workflow
history.

The two modes use the same launcher implementation and differ only through an
explicit data-profile argument. There will be no implicit environment-dependent
switch between real and isolated data.

### Fast packaged-directory validation

Keep NSIS as a release-only operation. Add `npm run electron:package:fast` for
the occasional check that must run from `win-unpacked`. It will reuse the
existing portable Python runtime and local Electron distribution, build the
frontend/Main code, and target only `--win dir`. It must not call
`runtime:build` or create an installer.

## Runtime observability

### Persistent logs

Electron Main will create a writable log directory before starting the worker.
It will set `ASR_LOCAL_WORKER_LOG` for Python and retain a separate Main/runtime
stdio log. `get_app_info` will return the actual log paths instead of `null`.

Log records must include timestamps, severity, process ID, workflow ID and
attempt ID when known. API keys, authorization headers, credential payloads,
full prompts, and transcript content must never be logged.

Files will use size-based rotation with a small bounded retention so repeated
debug sessions cannot grow AppData indefinitely.

### MOSS phase instrumentation

Instrument the production MOSS path at these boundaries:

1. `audio_normalizing`
2. `dependency_importing`
3. `model_loading`
4. `processor_loading`
5. `model_moving_to_device`
6. `feature_extracting`
7. `generating`
8. `formatting_transcript`

Each phase logs start, success, failure, duration, process memory, selected
device, and dtype where applicable. It does not log user audio or generated
text.

The transcriber interface will accept an optional progress reporter owned by
the workflow supervisor. Production and fake adapters may ignore unsupported
fields, but all calls remain compatible with existing tests.

### Heartbeats

While a blocking transcription phase runs, the supervisor emits a heartbeat at
most once every 10 seconds. A heartbeat updates:

- current named phase;
- elapsed phase time;
- `updated_at` and workflow sequence;
- human-readable detail.

The heartbeat keeps `overall_ratio` at the last truthful checkpoint, normally
8%, unless a real phase transition has a defined ratio. The UI displays
“仍在运行 · 最后心跳 N 秒前” and the phase name. It must not advance a fake
linear progress bar.

Heartbeat writes stop immediately when transcription returns, fails, the
workflow is cancelled, or the runtime exits. Sequence updates continue to use
the registry's existing monotonic ordering rules.

## Runtime failure propagation

Electron Main will translate these host-side conditions into a dedicated,
allowlisted runtime-status event:

- Python spawn failure;
- protocol parse failure;
- unexpected worker exit;
- runtime handshake failure;
- stderr tail associated with a fatal exit.

The renderer stores runtime health separately from workflow state. It must not
fabricate a workflow transition that Python did not persist. For a running task,
the task card shows a diagnostic banner explaining that the runtime is
unavailable and links to the log location.

Only a bounded, redacted stderr summary may reach the renderer. Complete logs
remain on disk.

## Workflow watcher

Add a read-only command, `npm run workflow:watch`, backed by a small Python
script. It accepts an optional workflow ID and otherwise watches the newest
workflow in the selected registry.

It prints only on state change or heartbeat and includes:

- workflow ID;
- status and stage;
- sequence;
- overall and stage ratios;
- detail/current phase;
- persisted `updated_at`;
- age since the last heartbeat.

The script opens SQLite read-only, never edits workflow state, and exits cleanly
when the workflow reaches a terminal state. A `--once` option provides a fast,
deterministic probe suitable for tests and support reports.

## Data flow

```text
fast debug launcher
  -> explicit real/isolated environment
  -> Vite + Electron Main
  -> existing portable Python runtime
  -> workflow v2 JSONL protocol
  -> MOSS phase reporter
  -> supervisor heartbeat + SQLite registry
  -> Electron workflow/runtime-status events
  -> Vue task card and on-disk logs

workflow:watch
  -> read-only SQLite connection
  -> terminal diagnostics
```

## Error handling

- Missing portable runtime: use the project virtual environment if valid;
  otherwise fail before opening Electron and print both checked paths.
- Installed application already running in real-data mode: abort before
  touching the registry and instruct the user to close it.
- Log directory creation failure: fail startup with the attempted path; do not
  silently discard diagnostics.
- Heartbeat persistence failure: stop the heartbeat, log the error, and allow
  the main transcription result or exception to decide the workflow outcome.
- Worker exit: reject pending IPC requests, clear the child reference, publish
  runtime unavailable, and retain the exit code/signal in logs.
- Blocking cancellation: show “正在等待原生模型调用返回”; application shutdown
  may still terminate the worker process after the existing grace period.

## Testing strategy

### Unit tests

- Debug environment resolution for real and isolated profiles.
- Runtime selection order and missing-runtime errors.
- Log redaction and rotation boundaries.
- Worker spawn/error/exit cleanup and runtime-status event emission.
- MOSS phase reporter ordering and failure reporting.
- Heartbeat monotonic sequence updates and cleanup on success/failure/cancel.
- Watcher `--once` output against a temporary SQLite registry.
- Renderer runtime-status reducer and stale-heartbeat display.

### Integration tests

- Start Electron debug mode against an isolated temporary data root and verify
  v2 handshake plus health check.
- Run a fake/short workflow and verify phase/heartbeat events do not regress
  final workflow state.
- Run the bundled-runtime MOSS smoke fixture and verify logs contain all relevant
  phase boundaries without credential or transcript leakage.
- Confirm real-data mode resolves the same config, registry, outputs and runtime
  paths as the installed application.

### Manual acceptance

1. Close installed ASR Local.
2. Run `快速调试听记助手.bat`.
3. Application opens within the normal TypeScript/Vite startup time without
   Electron Builder or NSIS.
4. Submit a real MOSS workflow.
5. Task card shows the named active phase and a heartbeat timestamp while the
   percentage remains truthful.
6. `npm run workflow:watch -- --once` reports the same persisted state.
7. Python and Electron logs appear in `%APPDATA%/ASR Local/logs`.
8. Force-stop the worker and confirm the UI reports runtime unavailable and
   points to the logs.
9. Restart debug mode after a source change without rebuilding the installer.

## Rollout and compatibility

All new commands are additive. The installed application's configuration,
credentials, model paths, output files, and workflow v2 registry remain in their
current formats. No migration is required.

NSIS remains the final release gate. Ordinary changes are validated with hot
debug mode; `electron:package:fast` is used only for packaged-directory checks;
`electron:dist` is reserved for a release candidate.
