<div align="center">
  <img src="extension/icons/icon128.png" width="72" alt="" />
  <h1>Orbit</h1>
  <p><strong>A second brain that learns as you do.</strong></p>
  <p>Bookmark a tweet. Right-click an article. It lands in your local Obsidian vault — tagged, summarized, and linked to what you already saved.</p>
</div>

---

Orbit is a Chrome extension plus a local Python server. When you bookmark
something on X, the extension intercepts it before X's UI even finishes
animating, and ships it to a server running on your own machine. That server
pulls the full text (or the YouTube transcript), asks an LLM for tags and a
summary, and writes a markdown file into a folder you can open in Obsidian.

Nothing leaves your machine except the content you saved, sent to whichever
LLM provider you configure. There's no Orbit account, no Orbit cloud, no
telemetry. The vault is plain markdown on your disk — if you delete Orbit
tomorrow, everything you saved is still there and still readable.

```
vault/
  tweets/     how-to-do-great-work-a1b2c3d4.md
  articles/   the-memex-method-e5f6a7b8.md
  youtube/    the-science-of-laughter-c9d0e1f2.md
```

Every file carries YAML frontmatter (`tags`, `summary`, `key_insights`,
`source_url`, …) and a trailing `## Related` section of `[[wikilinks]]` to
other entries sharing its tags — which is what turns the folder into an
actual Obsidian graph rather than a pile of notes.

## What it captures

| Source | How | What you get |
|---|---|---|
| **X / Twitter** | Bookmark a tweet, normally | Full text (including >280-char posts), real author handle, timestamp |
| **YouTube** | Right-click → Save to Orbit | Full transcript, real title and channel from yt-dlp, upload date |
| **Any article** | Right-click → Save to Orbit | Clean article text via trafilatura, no nav or ads |
| **Any page** | Right-click a selection → Save to Orbit | Just what you highlighted |

## Install Orbit v0.1

The public v0.1 runtime is currently **macOS Apple silicon only**. It bundles
its own Python runtime; everyday users do not install Python or use Terminal.

1. Download and unzip `Orbit-macOS-arm64-0.1.0.zip`, then move `Orbit.app` to Applications.
2. Open Orbit. Choose where your normal Markdown memory files live (the default is `~/Documents/Orbit`).
3. Choose Local AI or No AI. Local AI integrates with an installed Ollama app; Orbit checks the runtime and tells you the expected download size before you pull its two supported models. Orbit never installs Ollama or model files itself.
4. Install the Chrome Web Store extension when the listing is available. For a local release, unzip `orbit-extension-0.1.0.zip`, open `chrome://extensions`, enable Developer mode, and load that folder.
5. Bookmark a post on X or right-click a page and choose **Save to Orbit**. Open the toolbar popup and look for **Saved ✓**.

Opening Orbit again shows local status and reuses the running service rather
than starting another copy. The extension safely queues captures while Orbit
is stopped. See [RELEASE.md](RELEASE.md) for upgrade, uninstall, data, build,
and developer instructions.

## Everyday use

Bookmark any tweet on X. Within a few seconds the file appears in your chosen
memory folder. The toolbar icon flashes a checkmark; if the service isn't
running it shows a red count of captures waiting to send, and they'll flush
automatically once it's back.

After a successful capture, open the Orbit toolbar popup to optionally add one
short personal thought. Ignoring it changes nothing: the memory is already
saved. A thought is stored as `user_note` in Markdown and becomes part of both
exact and semantic Recall.

Press **Command+Shift+O** on macOS or **Alt+O** on Windows/Linux to open
Recall, then type whatever you remember. Option+O is not used on macOS because
it is a text-input chord (it types `ø` on the standard U.S. layout). You can
remap Orbit at `chrome://extensions/shortcuts`; the toolbar popup also has a
Recall button.

Contextual resurfacing is **off by default**. Enable “Resurface related
memories while browsing” in the toolbar popup and approve public-webpage access.
Orbit then analyzes at most 6,000 characters of readable public-page text and
may send that temporary context to the configured embedding provider. It never
reads form values, saves the visited page, or injects UI into the page. A small
badge appears only when one to three high-confidence memories are available.

### Optional: open the folder in Obsidian

Obsidian → **Open folder as vault** → select the folder you chose during setup.

> The graph starts sparse. Entries are linked by shared tags, and with only a
> handful of enriched entries it's common for nothing to overlap yet. It fills
> in as you save more.

## Developer commands

```bash
# Is it working? What's in the vault?
curl -s localhost:8000/health

# Read your captures as JSON
curl -s 'localhost:8000/entries?limit=10'
curl -s --get localhost:8000/entries --data-urlencode 'q=distributed systems'
curl -s 'localhost:8000/entries?type=youtube&status=enriched'

# Fill in anything that didn't get enriched (safe to re-run; idempotent)
./.venv/bin/python backfill.py --dry-run
./.venv/bin/python backfill.py

# Build/retry the derived semantic index in small resumable batches
./.venv/bin/python backfill_embeddings.py --dry-run
./.venv/bin/python backfill_embeddings.py

# Recall from incomplete wording (hybrid lexical + semantic ranking)
curl -s --get localhost:8000/search \
  --data-urlencode 'q=that tweet about products becoming invisible'

# A few derived neighbors for one memory (no provider call at view time)
curl -s localhost:8000/entries/<memory-id>/related

# Test temporary current-page matching without saving the page
curl -s localhost:8000/resurface -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/article","title":"Calm interfaces","description":"","text":"Readable public article text..."}'
```

`backfill.py` is the repair tool. It re-enriches untagged entries, re-fetches
missing transcripts, recovers tweet authors via X's public oEmbed endpoint,
deduplicates by normalized URL, and rebuilds the `[[wikilinks]]` across the
whole vault. Run it whenever you've been rate-limited, or after any long gap.

`backfill_embeddings.py` only rebuilds derived SQLite vectors; it never rewrites
Markdown. It processes 25 missing, stale, or failed memories per run by default,
so it is safe to stop and rerun. Use `--limit 0` only when you intentionally want
to process the whole remaining vault in one run.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ORBIT_INTELLIGENCE_PROVIDER` | packaged setup / `ollama` in `.env.example` | Active provider. Public v0.1 exposes only Ollama or No AI. |
| `ORBIT_DOWNLOAD_VIDEOS` | off | Set to `1` to also download full MP4s of saved YouTube videos. Off by default — this costs hundreds of MB per video. |
| `ORBIT_OLLAMA_URL` | `http://127.0.0.1:11434` | Advanced override for the local Ollama API. |
| `ORBIT_OLLAMA_EMBEDDING_MODEL` | `embeddinggemma` | Advanced local retrieval-model override. |
| `ORBIT_OLLAMA_ENRICHMENT_MODEL` | `qwen3:1.7b` | Advanced local understanding-model override. |
| `GEMINI_API_KEY`, `ORBIT_EMBEDDING_MODEL` | — | Legacy developer compatibility only; not part of public v0.1 setup. |

## How it works

```
  Chrome                          Your machine
┌──────────────────┐          ┌─────────────────────────────┐
│ extension/       │          │ FastAPI  POST /ingest       │
│  interceptor.js  │          │   → async queue (202)       │
│  patches fetch/  │  ──────▶ │   → dedupe on normalized URL│
│  XHR on x.com    │          │   → transcript / article    │
│                  │          │     extraction              │
│  background.js   │          │   → LLM: tags, summary,     │
│  context menu,   │          │     key insights            │
│  offline queue   │          │   → write vault/<type>/*.md │
└──────────────────┘          │   → embed after durable write│
                              │   → link by shared tags     │
                              └─────────────────────────────┘
```

The X capture works by monkey-patching `window.fetch` and `XMLHttpRequest`
inside the page, caching tweet JSON as it streams past, and firing when it
sees a `CreateBookmark` mutation. That's why bookmarking is the whole
interaction — there's no second button to press.

Deeper writeups: [`ORBIT-PROTOCOL.md`](ORBIT-PROTOCOL.md) (data format, file
layout, linking), [`extension-docs.md`](extension-docs.md) (capture layer),
[`server-docs.md`](server-docs.md) (processing layer).

## Known limitations

- **Public v0.1 Local AI requires Ollama.** Orbit does not bundle or silently
  install Ollama/model weights. When the runtime or a model is unavailable,
  Capture and lexical Recall continue in a clear degraded mode.
- The internal provider boundary retains legacy Gemini compatibility so old
  provider-specific vectors and developer setups remain safe, but Gemini is
  not offered in public v0.1 onboarding.
- **Generated Obsidian wikilinks use exact tag overlap.** Recall's Related
  section is semantic and derived locally, but it is intentionally not
  materialized into Markdown or presented as a graph.
- **X capture depends on X's internal GraphQL shape.** If they rename things,
  captures stop until the interceptor is updated. This is inherent to the
  approach.
- **The server must be running to capture.** If it isn't, captures queue in
  the extension and flush when it comes back — nothing is lost, but nothing
  is processed either.
- **Unpacked install only.** Not on the Chrome Web Store yet.

## License

[MIT](LICENSE) — do whatever you want with it.
