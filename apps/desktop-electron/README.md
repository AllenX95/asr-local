# ASR Local Electron Desktop

Electron + Vue 3 + TypeScript desktop host for the Python Workflow Runtime v2.

## Development

```powershell
npm install
npm run electron:dev
```

## Validation

```powershell
npm run typecheck
npm test
npm run electron:build
```

## Windows package

```powershell
npm run electron:package
```

## File picker start folders

The audio picker reads `ASR_LOCAL_AUDIO_DEFAULT_DIR`, and the reference-notes
picker reads `ASR_LOCAL_REFERENCE_DEFAULT_DIR`. If a variable is unset or its
directory does not exist, the picker starts in the current user's Documents
folder. Set these per machine to keep personal folder paths out of source control.

The renderer has no Node.js access. Desktop capabilities are exposed through
the typed preload bridge, while ASR, diarization, summary generation, task
state and recovery remain inside `apps/worker-python`.
