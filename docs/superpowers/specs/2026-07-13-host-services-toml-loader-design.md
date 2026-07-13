# Host Services TOML Loader Design

## Goal

Make `HostServices` load TOML successfully both from Electron's compiled output
and when Vitest executes the TypeScript source directly. Preserve the packaged
runtime's copied vendor dependency and remove the test-only module-not-found
failure without adding test setup side effects.

## Module Resolution

`hostServices.ts` will expose no new caller-facing interface. Its internal TOML
loader will resolve the parser in this order:

1. Load `./vendor/toml/toml.js` when that compiled-runtime dependency exists
   beside the executing module.
2. Otherwise load the declared `@iarna/toml` package dependency.

The fallback exists for source execution in Vitest and development tooling. It
does not replace or remove `electron/copyMainRuntimeDeps.cjs`, so packaged
Electron builds continue using the colocated vendor copy.

Only a missing vendor module triggers fallback. Parser errors and other runtime
errors must still propagate normally.

## Validation

The existing failing command is the regression signal:

`npm test -- tests/hostServices.spec.ts`

After it passes, run the full Vitest suite, TypeScript checks, Electron compile,
renderer build, Worker contract tests, and `git diff --check`. The final commit
will include the previously completed credential-grant/task-control work plus
this loader fix and their tests. Existing unrelated untracked files remain
untouched.

## Out of Scope

- Replacing `@iarna/toml`.
- Changing TOML serialization behavior.
- Removing the packaged vendor copy.
- Adding global Vitest aliases or pre-test file-copy steps.
