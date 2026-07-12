# asr-local-worker

This package is the local Python worker used by the desktop app.

Model downloads are not handled here.
The worker only reads local model paths from the project-level `config/models.toml`.

Optional inference dependencies are declared under:

```text
pip install .[inference]
```

Install the single pinned inference runtime used by supervisor, Pyannote, and
Qwen3-ASR:

```text
pip install .[inference]
```

The runtime normalizes every imported source to 16 kHz PCM WAV before local
inference. The local profile first runs Pyannote speaker diarization, merges
nearby turns, and split long turns into safe ASR chunks. The default `mixdown`
strategy creates one high-quality mono stream; `split_stereo` is available only
for separately recorded two-channel speakers. The source file is never replaced.
This supports the common
desktop containers (WAV, MP3, AAC/M4A, FLAC, OGG/Opus and WebM) without relying
on a globally installed ffmpeg. Set `ASR_LOCAL_FFMPEG` to an executable path
only when an operator needs to use a specific ffmpeg binary.

The Electron desktop starts the Workflow Runtime v2 supervisor. It can be
exercised without inference dependencies; `auto` selects the local production
runtime only when Qwen3-ASR, Pyannote, and their dependencies are available:

```text
python -m app.main --contract v2
```

The runtime uses the shared contract assets under `contracts/workflow-v2/`. Its
default `auto` mode selects the local production runtime when Qwen3-ASR,
Pyannote, and their dependencies are available, and otherwise keeps the fake
adapter available for development and contract testing. Explicit production
mode makes a missing dependency fail during a release gate:

```text
python -m app.main --contract v2 --pipeline-mode production
```

The production mode requires the local inference dependencies. Summary and Cloud
ASR bearer credentials use the ephemeral secret broker and trusted desktop
bridge. The only local profile is `pyannote_qwen3_asr`.

Qwen3-ASR 0.0.6 and Pyannote run in the same Python environment. The main runtime uses the pinned Qwen-compatible Transformers version directly, with one local inference runtime and no secondary ASR child process.

The Electron host accepts `ASR_LOCAL_V2_PIPELINE_MODE=auto|production|fake`.
The default is `auto`: it resolves to production when Qwen3-ASR, Pyannote,
`torch`, the model packages and `soundfile` are available, and otherwise
resolves to the fake adapter. Use `production` to make a missing dependency
fail explicitly during a release gate.

For a source checkout, the Electron host uses the canonical
`apps/worker-python/.venv` runtime. `ASR_LOCAL_PYTHON` may point to another
single environment when an operator needs to test a packaged or alternate
Python installation.
