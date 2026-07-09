# Local Model Storage

All large model weights should be stored under this directory and are intentionally excluded from version control.

Recommended layout:

```text
models/
├─ Qwen/
│  └─ Qwen3-ASR-1.7B/
└─ pyannote/
   └─ speaker-diarization-community-1/
```

The runtime does not download models automatically.
It only reads local paths from `config/models.toml`.

If you place the models in different directories, update `config/models.toml` accordingly.
