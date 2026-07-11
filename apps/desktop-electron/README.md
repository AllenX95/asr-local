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

The renderer has no Node.js access. Desktop capabilities are exposed through
the typed preload bridge, while ASR, diarization, summary generation, task
state and recovery remain inside `apps/worker-python`.
