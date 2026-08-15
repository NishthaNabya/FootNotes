# Orbit repository map

Orbit is an open, local-first memory layer for the internet. Its loop is **Capture → Connect → Recall**: capture through browsing habits, structure and connect automatically, then recover memories from incomplete recollection.

## Architecture

- `extension/`: the complete unpacked Chrome extension and its only loadable root.
- `extension/interceptor.js` → `extension/content.js` → `extension/background.js`: X bookmark interception, context-menu capture, and the Chrome-local retry queue.
- `extension/page-context-state.js` → `extension/page-context.js` → `POST /resurface`: opt-in, bounded, ephemeral public-page matching.
- `server.py`: FastAPI ingestion, extraction, enrichment, Markdown persistence, SQLite metadata, Related links, and retrieval.
- `providers.py`: provider-neutral health/enrichment/embedding boundary. Public v0.1 uses Ollama; legacy Gemini support is internal compatibility only.
- `orbit_config.py`, `orbit_app.py`, `onboarding/`: packaged local settings/Keychain, lifecycle, and first-run/status UI.
- `scripts/`: allowlisted extension packaging and the macOS app release build.
- `backfill.py`, `backfill_embeddings.py`, `migrate_to_obsidian.py`: safe maintenance and migrations.
- `vault/` (gitignored): user-owned Markdown source of truth plus local SQLite indexes. Never treat it as disposable build output.

## Run and verify

```bash
./.venv/bin/uvicorn server:app --port 8000
./.venv/bin/python -m unittest discover -s tests -v
npm run test:recall-ui
./.venv/bin/python -m compileall -q server.py providers.py orbit_config.py orbit_app.py backfill.py backfill_embeddings.py migrate_to_obsidian.py
node --check extension/background.js && node --check extension/content.js && node --check extension/interceptor.js && node --check extension/popup.js && node --check extension/recall.js && node --check extension/recall-state.js
./.venv/bin/python -m json.tool extension/manifest.json >/dev/null
./.venv/bin/python scripts/package_extension.py
```

Use `python backfill.py --dry-run` before vault repair and `python backfill_embeddings.py --dry-run` before embedding backfill.

## Guardrails

- Preserve existing user data, stable memory IDs, filenames, frontmatter compatibility, and plain-Markdown portability. Back up before destructive maintenance.
- Prefer incremental changes over rewrites. Verify behavior and data flow; compiling alone is not completion.
- Local operation, portable data, no mandatory account, and no mandatory cloud service are product requirements.
- Do not casually introduce AI chat, folders as the primary organization model, graph-first UX, hosted vector databases, mandatory accounts/cloud services, or tight coupling to one AI provider.
