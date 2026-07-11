# Worker Runtime and MOSS Environment Design

## Goal

Make the desktop worker use a project-local Python runtime, keep optional
inference dependencies from crashing the v2 worker at startup, and replace
the obsolete Qwen smoke environment only after the new MOSS-first runtime is
validated.

## Runtime selection

The canonical worker interpreter is
`apps/worker-python/.venv/Scripts/python.exe`. The desktop client already
selects this path before PATH-based Python fallbacks. `ASR_LOCAL_PYTHON`
remains an explicit operator override.

The new `.venv` is created from a supported local Python installation and is
populated from the project's MOSS-native dependency set. It is ignored by
Git. No model directory or existing output data is modified.

## Dependency boundaries

The v2 server must not import native MOSS, Legacy Qwen/pyannote, or cloud
production adapters until it has resolved `pipeline_mode` as `production`.
In `auto` mode, missing optional inference dependencies select the existing
fake runtime and allow protocol handshake and UI recovery to work.

When `production` is explicitly selected and a required dependency is
missing, the server returns a structured, actionable request error naming the
missing dependency. It must not terminate its stdout stream.

## Migration and cleanup

The new runtime must pass package imports, MOSS model discovery, v2 handshake,
`auto` resolution to production, and CUDA availability checks before cleanup.
Only then delete the old `.venv-qwen-smoke` environment and the empty
`.venv-hf` and `.venv-hf-download` environments. This preserves a rollback
path until the replacement is proven working.

## Validation

Add a focused test that simulates unavailable native imports and verifies that
the fake v2 server can still start. Run focused Python tests, the direct v2
handshake, and proportionate TypeScript/Rust checks after the code changes.
