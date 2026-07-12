# Qwen-Only Single Runtime Simplification Design

## Status

- Date: 2026-07-12
- Decision: approved
- Migration strategy: one-time hard cutover
- Compatibility policy: no MOSS workflow or profile compatibility

## 1. Background

ASR Local currently exposes two local transcription profiles:
`pyannote_qwen3_asr` and `pyannote_moss_asr`. Supporting both backends forced
Qwen3-ASR into a separate Python environment because Qwen3-ASR 0.0.6 and the
audited MOSS runtime require incompatible Transformers versions.

Qwen3-ASR has already met the user's transcription needs in prior testing, and
no MOSS workflow has completed successfully. The product therefore gains no
practical value from retaining MOSS, its profile, or its compatibility surface.

This design removes MOSS completely and collapses the Python deployment into a
single runtime and virtual environment containing the workflow supervisor,
Pyannote, and Qwen3-ASR.

## 2. Goals

1. Make `pyannote_qwen3_asr` the only local transcription pipeline.
2. Remove all MOSS model, runtime, UI, configuration, contract, and test code.
3. Remove the isolated Qwen subprocess and `.venv-qwen` deployment.
4. Run the supervisor, Pyannote, and Qwen3-ASR from one Python executable.
5. Preserve sequential GPU use: run Pyannote, release it, then load Qwen.
6. Produce one reproducible dependency set and one packaged Python runtime.
7. Keep Cloud ASR behavior unchanged.

## 3. Non-Goals

- Migrating or reopening MOSS workflow history.
- Retaining `moss_transcribe_diarize` or `pyannote_moss_asr` as readable aliases.
- Preserving MOSS model settings or automatically deleting local model weights.
- Comparing Qwen and MOSS transcription quality.
- Redesigning the workflow protocol beyond removing MOSS-specific values.
- Changing summary generation or Cloud ASR behavior.

Model weights under `models/OpenMOSS-Team/MOSS-Transcribe-Diarize` are not
deleted by code or migration scripts. They are user-managed local data and may
be removed manually after the new build is validated.

## 4. Target Architecture

```text
Electron Main
  -> WorkflowRuntimeClient
      -> one Python executable
          -> Workflow Runtime v2 supervisor
              -> Pyannote diarization
              -> release Pyannote GPU resources
              -> Qwen3-ASR transcription
              -> workflow events and artifacts
```

The runtime is selected by `ASR_LOCAL_PYTHON` or the canonical packaged/source
runtime path. `ASR_LOCAL_QWEN_PYTHON` no longer exists. Qwen is imported and
loaded directly by the main worker process.

There is one environment in source development:

```text
apps/worker-python/.venv
```

There is one corresponding bundled runtime in packaged builds. No second
runtime directory, interpreter, environment variable, or JSONL child protocol
is retained.

## 5. Product and Contract Changes

### 5.1 Pipeline profiles

Accepted profiles become:

- `pyannote_qwen3_asr`
- `cloud_asr`

The worker rejects all of the following as unsupported input:

- `pyannote_moss_asr`
- `moss_transcribe_diarize`
- `qwen3_asr_with_pyannote`

The legacy Qwen alias is removed as part of the hard cutover so there is one
canonical local profile throughout UI, TypeScript, JSON schemas, fixtures, and
Python validation.

### 5.2 Capabilities

`runtime.capabilities` advertises only the canonical Qwen local profile and
Cloud ASR. Local readiness is based on:

- Qwen model directory exists;
- Pyannote model directory exists;
- `torch`, `qwen_asr`, `pyannote.audio`, `soundfile`, and required audio
  normalization dependencies import successfully in the main runtime.

Capabilities contain no MOSS model status, runtime status, prompt compiler, or
backend identifier.

### 5.3 Existing workflow data

No migration is provided. A stored workflow whose profile is no longer valid
may remain as raw data on disk, but the application does not promise to list,
open, retry, or render it. This is acceptable because no successful MOSS
workflow needs preservation.

## 6. Configuration and UI Cleanup

### 6.1 Model configuration

Remove `moss_transcribe_diarize` and `active_local_asr_model`. With one local
backend, selecting an active local model is meaningless. The model config keeps
only the Qwen and Pyannote paths plus their required flags and descriptions.

Configuration loading should tolerate an existing MOSS TOML section during one
upgrade only by ignoring unknown keys. The application must not rewrite or
carry that section into newly saved configuration.

### 6.2 Desktop settings

Remove:

- the local ASR model selector;
- the MOSS model path field and readiness indicator;
- MOSS-related IPC request and response fields.

Keep Qwen and Pyannote path configuration. The UI labels the local pipeline as
`Pyannote + Qwen3-ASR` without presenting a backend choice.

### 6.3 Workflow creation

The workflow screen offers:

- Local: Pyannote + Qwen3-ASR;
- Cloud ASR, when available.

Local submission always serializes `pipeline_profile: pyannote_qwen3_asr`.
There is no disabled MOSS option or MOSS readiness branch.

## 7. Python Runtime Changes

### 7.1 Model manager

Reduce `ModelManager` to two local models: Pyannote and Qwen. Remove:

- `MOSS_MODEL_KEY` and MOSS model metadata;
- `MossTranscribeDiarizeAdapter` and parser helpers;
- MOSS path, dtype, batch-size, integrated-diarization, load, and close methods;
- active-model branching.

`get_qwen_model()` directly imports `Qwen3ASRModel` in the current interpreter.
There is no fallback that launches or searches for another interpreter.

### 7.2 Pipeline routing

The local router has one local transcriber. It routes
`pyannote_qwen3_asr` directly and rejects all other local profile values. Where
a router abstraction no longer provides a useful seam, replace it with one
explicit local pipeline dependency rather than retaining a one-entry backend
map.

### 7.3 GPU lifecycle

The job sequence remains:

1. normalize audio;
2. load and run Pyannote;
3. materialize speaker turns and ASR chunks;
4. close the Pyannote pipeline;
5. run garbage collection and empty the CUDA cache;
6. load Qwen in the same process;
7. transcribe chunks;
8. close Qwen at the workflow cleanup boundary.

The existing single local GPU lane remains. The implementation must not keep
Pyannote and Qwen model weights resident on GPU at the same time.

### 7.4 Removed runtime boundary

Delete:

- `app/pipeline/qwen_subprocess.py`;
- `scripts/qwen_segment_worker.py`;
- subprocess protocol tests and recovery branches;
- `ASR_LOCAL_QWEN_PYTHON` discovery and environment reporting;
- `.venv-qwen` build, validation, packaging, and documentation.

Failures from Qwen imports or model loading are reported directly as main
runtime dependency/model errors. There is no broken-pipe, child-exit, or JSONL
protocol error class after the migration.

## 8. Dependency and Environment Design

Replace the broad, conflicting extras with one pinned production inference
dependency set for Pyannote + Qwen. The exact lock is established from a clean
Python 3.12 environment and must include at minimum:

- Qwen3-ASR package and its required Transformers version;
- Pyannote Audio;
- a CUDA-compatible Torch build used by both;
- NumPy, SoundFile, Librosa, Safetensors, and imageio-ffmpeg as required;
- all transitive constraints needed to reproduce the validated install.

The implementation must not guess compatible pins by combining the former
`inference` and `moss-native` extras. It must create a clean `.venv`, install the
Qwen-compatible stack, import both Qwen and Pyannote, and record the resulting
validated pins in the project's chosen lock mechanism.

The dependency gate is:

```powershell
python -c "import torch, transformers, qwen_asr, pyannote.audio, soundfile"
```

It must run from the same interpreter used by the supervisor.

## 9. Build and Release Changes

`scripts/build/build_python_runtime.ps1` becomes a single-runtime builder:

1. accept or locate `apps/worker-python/.venv`;
2. validate all required imports in that interpreter;
3. copy one runtime tree;
4. include Qwen and Pyannote application packages and runtime dependencies;
5. exclude `.venv-qwen` and every MOSS-specific resource;
6. run the supervisor hello/capability smoke from the copied runtime;
7. run a real short Qwen + Pyannote smoke before release acceptance.

Electron Builder resources and runtime path resolution must reference one Python
runtime only. Packaging validation must inspect the installed/portable output,
not merely the source `.venv`.

## 10. Deletion Inventory

Implementation must search the repository after edits and remove or update all
live references in these areas:

- `config/models.toml` and MOSS lock files;
- worker configuration, schemas, model snapshots, environment snapshots, model
  manager, adapters, router, supervisor, prompt compiler selection, probes, and
  tests;
- Electron host services and IPC types;
- workflow and settings Vue components;
- TypeScript workflow profile types, fake runtime, fixtures, and tests;
- Workflow v2 contract schemas, examples, and capability fixtures;
- build scripts and Electron packaging resources;
- launch/readiness diagnostics;
- active README and developer documentation.

Historical design documents and benchmark reports may remain as records, but
they must receive a prominent superseded note linking to this design if they
otherwise appear to describe the current architecture. Generated outputs and
local model directories are not scanned or deleted.

## 11. Implementation Order

Use a tracer-bullet sequence that keeps failures attributable:

1. Establish and lock a clean, single-environment dependency set.
2. Change Python model loading from subprocess Qwen to in-process Qwen.
3. Remove the MOSS adapter, routing, configuration, capability, and prompt
   branches.
4. Collapse profile schemas and workflow contracts.
5. Simplify Electron IPC, settings, and workflow UI.
6. Rewrite the Python runtime builder and packaged resource layout.
7. Update tests, probes, active documentation, and superseded notices.
8. Run source-runtime and packaged-runtime production gates.

Do not start by deleting `.venv-qwen`. Keep it available as a rollback aid until
the single main environment passes the real Qwen smoke. The directory is local
and ignored by Git; remove it manually only after packaged validation succeeds.

## 12. Testing and Acceptance Gates

### Gate 1: static cleanup

- TypeScript typecheck passes.
- Python tests pass.
- Repository search finds no live MOSS identifiers outside explicitly marked
  historical documents.
- Repository search finds no live `.venv-qwen` or
  `ASR_LOCAL_QWEN_PYTHON` references.

### Gate 2: single-interpreter dependency proof

- Supervisor, Qwen, Pyannote, Torch, Transformers, SoundFile, and audio
  normalization dependencies import from one interpreter.
- Runtime capabilities report Qwen local readiness without consulting another
  Python executable.

### Gate 3: focused inference smoke

- A short multi-speaker recording completes Pyannote diarization and Qwen
  transcription.
- Speaker/time boundaries survive chunk transcription and merge.
- Logs prove Pyannote is released before Qwen model loading.
- GPU memory returns near the established idle baseline after workflow cleanup.

### Gate 4: representative recordings

- Previously successful Qwen samples produce equivalent transcripts.
- 10-, 30-, and 90-minute recordings complete without deadlock or OOM.
- Cancellation and retry do not leave a loaded model or locked GPU lane.

### Gate 5: packaged application

- Electron package builds successfully.
- The installed/portable app launches the bundled single runtime.
- Runtime hello and capabilities succeed.
- A real local transcription completes without a source checkout, system
  Python, `.venv-qwen`, or globally installed ffmpeg.
- Application shutdown terminates the one worker process cleanly.

## 13. Rollback

The rollback unit is the complete migration commit series, not runtime profile
selection. If the unified dependency set or packaged runtime fails acceptance,
revert the migration and continue using the existing Pyannote main runtime plus
isolated Qwen child. Do not restore MOSS alone or introduce mixed intermediate
states into a release branch.

## 14. Definition of Done

The migration is complete when:

- the product exposes one local pipeline, `pyannote_qwen3_asr`;
- no executable path can select or load MOSS;
- one Python executable imports and runs the supervisor, Pyannote, and Qwen;
- `.venv-qwen` and `ASR_LOCAL_QWEN_PYTHON` are absent from live code and build
  configuration;
- source and packaged runtime gates pass on real audio;
- no model weights or user output data were moved or deleted;
- current developer documentation describes only the single-runtime design.
