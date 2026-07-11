# asr-local-worker

This package is the local Python worker used by the desktop app.

Model downloads are not handled here.
The worker only reads local model paths from the project-level `config/models.toml`.

Optional inference dependencies are declared under:

```text
pip install .[inference]
```

For the validated native MOSS path, install the pinned runtime extra in an
isolated environment:

```text
pip install .[moss-native]
```

The native extra includes `imageio-ffmpeg`, which provides the audio decoder
used to normalize every imported source to 16 kHz PCM WAV before MOSS
inference. The default `mixdown` strategy creates one high-quality mono stream;
the MOSS-only `split_stereo` strategy preserves left and right channels as two
independent mono streams and merges their timestamped results with channel
labels. Use `split_stereo` only when the two channels contain separately
recorded speakers. The source file is never replaced. This supports the common
desktop containers (WAV, MP3, AAC/M4A, FLAC, OGG/Opus and WebM) without relying
on a globally installed ffmpeg. Set `ASR_LOCAL_FFMPEG` to an executable path
only when an operator needs to use a specific ffmpeg binary.

The existing desktop integration still starts the v1 worker by default. The
independent v2 supervisor can be exercised without inference dependencies; its
default `auto` mode selects native MOSS only when the model and runtime
dependencies are available:

```text
python -m app.main --contract v2
```

v2 uses the shared contract assets under `contracts/workflow-v2/`. Its default
`auto` mode selects the native MOSS adapter when the pinned runtime and local
model are available, and otherwise keeps the fake adapter available for
development and contract testing.

After the Phase 0 dependency gate, the native adapter can be selected explicitly
for a MOSS/CPU smoke path:

```text
python -m app.main --contract v2 --pipeline-mode production
```

The production mode requires the MOSS inference dependencies. Summary and Cloud
ASR bearer credentials use the ephemeral secret broker and trusted desktop
bridge. The legacy Qwen+pyannote path is available as an explicit v2 pipeline
for compatibility, while the desktop default remains MOSS.

The Tauri v2 client accepts `ASR_LOCAL_V2_PIPELINE_MODE=auto|production|fake`.
The default is `auto`: it resolves to production when the MOSS model plus
`torch`, `transformers`, and `soundfile` are available, and otherwise resolves
to the fake adapter. Use `production` to make a missing dependency fail
explicitly during a release gate.

For a source checkout with a separate inference environment, set
`ASR_LOCAL_PYTHON` to that environment's Python executable. The Tauri client
also recognizes the repository-local `.venv-moss313` smoke environment before
falling back to the system Python.
