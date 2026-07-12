# Electron migration implementation report

## Result

The desktop application now uses Electron, Vue, and TypeScript. Rust, Cargo,
Tauri commands, permissions, and frontend dependencies have been removed. The
Python workflow runtime remains the transcription and LLM execution core and
communicates with Electron Main over the versioned JSONL v2 contract.

## Runtime and security boundaries

- Electron Main owns process lifecycle, filesystem access, configuration,
  credentials, history discovery, and trusted workflow submission.
- The preload bridge exposes an allowlisted API only. Renderer windows use
  context isolation, sandboxing, and no Node integration.
- The packaged application starts the bundled Python 3.12 runtime and performs
  a version/capability handshake before accepting work.
- Summary credentials are stored with Electron `safeStorage`. Existing Windows
  DPAPI values are migrated with backups and are never returned to the renderer.
- Workflow state persists in the Python-owned SQLite registry. Existing v2
  registries, configuration, model paths, credentials, and output history have
  migration paths.

## Packaging

`npm run electron:dist` builds the portable Python runtime, compiles the Vue and
Electron applications, and produces an NSIS installer. Model weights remain
external and are not copied into the installer.

The current Windows artifact is intentionally not tracked by Git:

- `apps/desktop-electron/release-electron/ASR Local Setup 0.1.0.exe`
- Size: 2,100,883,060 bytes
- SHA-256: `D633FC0A82B8F16F1A9FAF0F6E447BD3C7B1DA4C18BE776D5C6015C77C27F63A`

## Verification

- Vue TypeScript check: passed
- Electron TypeScript compile: passed
- Frontend tests: 6 passed
- Python workflow v2 contract tests: 66 passed
- npm audit: 0 vulnerabilities
- NSIS silent install, packaged startup, bundled-runtime launch, clean shutdown,
  and silent uninstall: passed
- Real MOSS inference smoke test against the bundled Python runtime: passed

## Release follow-ups

The current local artifact is unsigned and ASAR packaging is disabled because
the local Windows packaging environment could not update executable integrity
metadata while antivirus/file locking was active. Configure a production code
signing certificate and repeat the release build in a clean CI runner before
public distribution. The bundled CUDA/PyTorch runtime also makes the installer
large; a later runtime-download or CPU/GPU flavor split can reduce distribution
size without changing the desktop/runtime contract.
