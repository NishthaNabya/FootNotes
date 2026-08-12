<div align="center">
  <img src="icons/icon128.png" width="72" alt="" />
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

## Requirements

- **macOS or Linux** (Windows should work; untested)
- **Python 3.11+**
- **Google Chrome**
- An **LLM API key** — currently Gemini. Optional: without one, Orbit still
  captures and saves everything, just without tags or summaries.

## Setup

**1. Clone and install the server**

```bash
git clone https://github.com/NishthaNabya/Orbit.git
cd Orbit
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

**2. Add your API key**

```bash
cp .env.example .env
# then edit .env and paste in a key from https://aistudio.google.com/apikey
```

> **Heads up on the free tier.** Google's free Gemini tier allows
> **20 requests per day, per model**. That's about 20 saved items before
> enrichment stops working for the day. Captures still save correctly — they
> just come out untagged, marked `ingested` instead of `enriched` — and
> re-running `backfill.py` the next day fills them in. For real daily use,
> enable billing on your Google Cloud project.

**3. Start the server**

```bash
./.venv/bin/uvicorn server:app --port 8000
```

You should see `[Gemini] Credentials verified — enrichment active`. If you
see a credential error instead, Orbit will still run and still save
everything — it just won't tag anything until the key works.

**4. Load the extension**

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked** → select this folder
4. Click Orbit's toolbar icon — it should say **Connected**

**5. Save something**

Bookmark any tweet on X. Within a few seconds the file appears under
`vault/tweets/`. The toolbar icon flashes a checkmark; if the server isn't
running it shows a red count of captures waiting to send, and they'll flush
automatically once it's back.

**6. Open the vault in Obsidian**

Obsidian → **Open folder as vault** → select `Orbit/vault`. Then open Graph
view.

> The graph starts sparse. Entries are linked by shared tags, and with only a
> handful of enriched entries it's common for nothing to overlap yet. It fills
> in as you save more.

## Everyday use

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
```

`backfill.py` is the repair tool. It re-enriches untagged entries, re-fetches
missing transcripts, recovers tweet authors via X's public oEmbed endpoint,
deduplicates by normalized URL, and rebuilds the `[[wikilinks]]` across the
whole vault. Run it whenever you've been rate-limited, or after any long gap.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Required for enrichment. Everything else works without it. |
| `ORBIT_DOWNLOAD_VIDEOS` | off | Set to `1` to also download full MP4s of saved YouTube videos. Off by default — this costs hundreds of MB per video. |

## How it works

```
  Chrome                          Your machine
┌──────────────────┐          ┌─────────────────────────────┐
│ interceptor.js   │          │ FastAPI  POST /ingest       │
│  patches fetch/  │  ──────▶ │   → async queue (202)       │
│  XHR on x.com    │          │   → dedupe on normalized URL│
│                  │          │   → transcript / article    │
│ background.js    │          │     extraction              │
│  context menu,   │          │   → LLM: tags, summary,     │
│  offline queue   │          │     key insights            │
└──────────────────┘          │   → write vault/<type>/*.md │
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

- **Enrichment is Gemini-only.** Ollama / OpenAI / Anthropic support would
  remove the free-tier ceiling entirely; it isn't built yet.
- **Linking is exact tag-string overlap**, not semantic. Two entries about
  the same idea tagged `pkm` and `personal-knowledge-management` won't link.
- **X capture depends on X's internal GraphQL shape.** If they rename things,
  captures stop until the interceptor is updated. This is inherent to the
  approach.
- **The server must be running to capture.** If it isn't, captures queue in
  the extension and flush when it comes back — nothing is lost, but nothing
  is processed either.
- **Unpacked install only.** Not on the Chrome Web Store yet.

## License

[MIT](LICENSE) — do whatever you want with it.
