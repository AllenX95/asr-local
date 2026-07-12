# asr-local-worker

This package is the local Python worker used by the desktop app.

Model downloads are not handled here.
The worker only reads local model paths from the project-level `config/models.toml`.

Optional inference dependencies are declared under:

```text
pip install .[inference]
```

For the validated native MOSS path, install the pinned runtime extra in an
isolated environment. Qwen and Pyannote use the inference extra:

```text
pip install .[moss-native]
```

The runtime normalizes every imported source to 16 kHz PCM WAV before local
inference. Both local profiles first run Pyannote speaker diarization, merge
nearby turns, and split long turns into safe ASR chunks. The default `mixdown`
strategy creates one high-quality mono stream; `split_stereo` is available only
for separately recorded two-channel speakers. The source file is never replaced.
This supports the common
desktop containers (WAV, MP3, AAC/M4A, FLAC, OGG/Opus and WebM) without relying
on a globally installed ffmpeg. Set `ASR_LOCAL_FFMPEG` to an executable path
only when an operator needs to use a specific ffmpeg binary.

The Electron desktop starts the Workflow Runtime v2 supervisor. It can be
exercised without inference dependencies; `auto` selects native MOSS only when
the model and runtime dependencies are available:

```text
python -m app.main --contract v2
```

The runtime uses the shared contract assets under `contracts/workflow-v2/`. Its default
`auto` mode selects the local production runtime when Qwen3-ASR, Pyannote and
their dependencies are available, and otherwise keeps the fake adapter available
for development and contract testing.

After the Phase 0 dependency gate, the native adapter can be selected explicitly
for a MOSS/CPU smoke path:

```text
python -m app.main --contract v2 --pipeline-mode production
```

The production mode requires the local inference dependencies. Summary and Cloud
ASR bearer credentials use the ephemeral secret broker and trusted desktop
bridge. New local profiles are `pyannote_qwen3_asr` (default) and
`pyannote_moss_asr` (optional). Historical profile names remain readable for
old workflow snapshots but are not emitted by the new desktop UI.

Qwen3-ASR 0.0.6 currently pins Transformers 4.57.6 while the audited MOSS
runtime uses Transformers 5.13.0. Do not force both pins into one environment:
the worker reports `QWEN_RUNTIME_UNAVAILABLE` until a compatible Qwen runtime
is installed or runtime isolation is configured. MOSS's Transformers runtime
must remain intact.

The Electron host accepts `ASR_LOCAL_V2_PIPELINE_MODE=auto|production|fake`.
The default is `auto`: it resolves to production when Qwen3-ASR, Pyannote,
`torch`, the model packages and `soundfile` are available, and otherwise
resolves to the fake adapter. Use `production` to make a missing dependency
fail explicitly during a release gate.

For a source checkout with a separate inference environment, set
`ASR_LOCAL_PYTHON` to that environment's Python executable. The Electron host
uses the canonical `apps/worker-python/.venv` runtime before falling back to
the system Python.
