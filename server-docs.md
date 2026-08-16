# FootNotes local service

`server.py` is FootNotes's local processing and retrieval service. It accepts
captures from the Chrome extension, writes canonical Markdown, and maintains a
rebuildable SQLite index beside the vault.

Public v0.1 has two intelligence modes:

- **Ollama** for optional local enrichment and embeddings
- **No AI** for Capture and lexical Recall without model dependencies

The provider contract lives in `providers.py`. Persistence and retrieval do not
depend on a concrete model implementation.

## Startup

```bash
./.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
```

The packaged macOS launcher starts the same FastAPI application on loopback.
Startup initializes SQLite, checks the selected local intelligence mode, starts
the capture worker, and schedules only a small resumable embedding-reconciliation
batch. Provider availability never blocks the service from becoming usable.

## Capture flow

```text
POST /ingest
    ↓ 202 Accepted
async capture queue
    ↓
source extraction
    ↓
optional local enrichment
    ↓
Markdown durable write
    ↓
best-effort local embedding
```

The worker retries transient processing errors. If enrichment is unavailable,
the original memory is still written with empty generated metadata. Embedding
failure is recorded in derived SQLite state and never rolls back Markdown.

Articles are extracted with `trafilatura`. YouTube captures use metadata from
`yt-dlp` and transcripts from `youtube-transcript-api` when available. Full
video download is off by default and controlled by `FOOTNOTES_DOWNLOAD_VIDEOS=1`.

## Persistence

Each memory is one Markdown file under a type folder:

```text
<vault>/
  tweets/
  articles/
  youtube/
  ingest.log
```

Markdown contains the stable memory ID, source provenance, captured text,
snapshot hash, optional user note, and generated metadata. It is canonical and
human-readable.

`ingest.log` is SQLite derived state containing capture status, deduplication
metadata, and provider/model-specific embedding rows. Current retrieval selects
only rows matching the active provider, model, and vector dimension. Rows from
unsupported historical providers are ignored and cannot prevent Ollama vectors
from being generated.

## Local enrichment

When Ollama and the configured models are ready, FootNotes requests structured
tags, a summary, and key insights through Ollama's local HTTP API. The same
provider supplies document and query embeddings for Recall, Related, and
opt-in resurfacing.

FootNotes never installs Ollama, starts downloads, or hides model downloads from
the user. No AI mode uses empty generated metadata and lexical retrieval.

## Retrieval

- `/search` and `/entries?q=...` use transparent hybrid lexical/semantic Recall.
- `/entries/{entry_id}/related` compares existing local vectors at view time and
  falls back to shared tags when vectors are missing.
- `/resurface` compares bounded ephemeral public-page context with local vectors;
  it does not save the visited page.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest` | Accept and queue a normalized capture |
| `GET` | `/health` | Service, provider, queue, vault, and embedding health |
| `GET` | `/status/{entry_id}` | Capture processing status |
| `GET` | `/stats` | Capture and embedding counts |
| `GET` | `/entries` | List/filter memories, or run Recall with `q` |
| `GET` | `/search` | Explicit Recall endpoint |
| `GET` | `/entries/{entry_id}` | Read a complete memory |
| `PATCH` | `/entries/{entry_id}/note` | Set or clear the user's thought |
| `GET` | `/entries/{entry_id}/related` | Derived related memories |
| `POST` | `/resurface` | Match temporary page context |

FastAPI's local interactive schema is available at
`http://127.0.0.1:8000/docs` during development. The 0.x local API may evolve.

## Maintenance

```bash
./.venv/bin/python backfill.py --dry-run
./.venv/bin/python backfill_embeddings.py --dry-run
```

`backfill.py` backs up the vault before repair. `backfill_embeddings.py` only
creates or repairs derived vector rows and is safe to stop and resume.

See [FOOTNOTES-PROTOCOL.md](FOOTNOTES-PROTOCOL.md) for the canonical format and
[RELEASE.md](RELEASE.md) for packaged setup, upgrades, and data locations.
