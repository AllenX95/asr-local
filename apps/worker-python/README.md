# asr-local-worker

This package is the local Python worker used by the desktop app.

Model downloads are not handled here.
The worker only reads local model paths from the project-level `config/models.toml`.

Optional inference dependencies are declared under:

```text
pip install .[inference]
```
