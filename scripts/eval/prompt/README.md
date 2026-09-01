# Prompt evaluation utilities

These scripts preserve the local summary-prompt evaluation workflow that was
previously stored under the ignored `tmp/` tree.

The runners use Electron `safeStorage` to read the existing ASR Local summary
profile from the current Windows user data directory. They send transcript and
summary content to the configured OpenAI-compatible provider. Run them only
with data and credentials that are authorized for that provider.

Generated manifests, gold checklists, summaries, scores, and reports can
contain private interview material. Keep all generated output under the
ignored `outputs/` directory and never commit it.

Typical commands, run from the repository root:

```powershell
apps\desktop-electron\node_modules\.bin\electron.cmd scripts\eval\prompt\run_prompt_eval.cjs outputs\prompt-eval-run run-label
apps\desktop-electron\node_modules\.bin\electron.cmd scripts\eval\prompt\run_prompt_judge.cjs outputs\prompt-eval-run
apps\desktop-electron\runtime\python\python.exe scripts\eval\prompt\analyze_prompt_eval.py outputs\prompt-eval-run
```

The comparison scripts retain the original July 2026 round paths as historical
defaults. Review those constants before starting a new evaluation campaign.
