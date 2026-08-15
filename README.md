<div align="center">
  <img src="extension/icons/icon128.png" width="72" alt="Orbit" />
  <h1>Orbit</h1>
  <p><strong>Your own memory for the web.</strong></p>
</div>

You find useful things all day: a post you want to revisit, an article that
changes how you think, a tool you may need later, or a sentence for a project
you have not started yet.

You save it. Then, most of the time, it disappears into bookmarks.

Orbit is an open-source experiment around a simple question:

> **What if the things you chose to remember could come back when they became useful?**

Orbit captures what mattered without asking you to reorganize your browsing,
keeps it as Markdown on your machine, remembers why you cared when you choose
to say, and helps you find it again from an incomplete recollection.

It is early v0.1 software. Trying it, breaking it, challenging the idea, and
contributing retrieval examples are all welcome.

## What Orbit feels like

Bookmark something on X normally, or right-click an article and choose
**Save to Orbit**. That is the complete capture flow.

After the memory is safely saved, you can optionally add one thought:

> **Why are you saving this?**
>
> `might want to volunteer here later`

Ignoring the prompt changes nothing. There are no required folders, tags, or
forms.

Weeks later, you can search:

> `that opportunity where I could help people in my field`

Orbit combines ordinary text matching with local semantic retrieval to find
the memory even when the original title and wording are gone from your head.

If you explicitly enable resurfacing, Orbit can also stay alert for a small
number of unusually relevant memories while you read public webpages. When
nothing is useful, it stays quiet.

The goal is for remembering to happen as a side effect of using the internet
normally—not as another knowledge-management routine to maintain.

## Why this exists

Bookmarks, read-later apps, folders, and notes are good at storing things. The
harder part is what happens afterward.

People rarely remember the exact title, wording, date, or application. They
remember fragments such as:

> “There was that thing about making AI interfaces feel less generic.”

> “I saved something because I thought I could volunteer there.”

Sometimes they do not remember to search at all.

Orbit is built around **Capture → Connect → Recall**. The interesting problem
is not how to store more knowledge. It is how something you once learned can
become useful at the right moment again—without giving up ownership of the
memory itself.

## What works today

### Capture

| Source | How | Saved evidence |
|---|---|---|
| **X / Twitter** | Bookmark a post normally | Full post text, author, original URL, and timestamp |
| **YouTube** | Right-click → Save to Orbit | Transcript when available, title, channel, URL, and upload date |
| **Articles and links** | Right-click → Save to Orbit | Readable extracted text and source provenance |
| **Selected text** | Highlight, then right-click → Save to Orbit | The selected passage and its source |

If the local service is stopped, the extension queues captures and sends them
when Orbit returns. Duplicate URLs are handled safely.

### Personal context

After capture, you can optionally record why the memory mattered:

> `look back for volunteering`

> `could use this for onboarding`

> `considering this because the free tier allows commercial use`

This `user_note` remains separate from AI summaries and tags. It is stored in
Markdown and participates strongly in both lexical and semantic Recall.

### Recall

Open Recall with **Command+Shift+O** on macOS or **Alt+O** on Windows/Linux,
or use the toolbar button. Search using whatever fragments you remember.

Orbit combines exact text signals with local embeddings. Memories without an
embedding still participate fully in lexical search, so Recall continues to
work in No AI mode. The shortcut can be changed at
`chrome://extensions/shortcuts`.

### Related

When you inspect a memory, Orbit can show a few strong semantic neighbors.
Shared tags provide a fallback when vectors are missing. Weak matches,
duplicates, and the memory itself are excluded rather than used to fill space.

### Resurface

Contextual resurfacing is optional and off by default. If enabled, Orbit reads
a bounded amount of ordinary public-page text and compares that temporary
context with local memory vectors.

The current page is not added to your memory. Orbit does not read form values,
operate on private/local/authentication pages, retain browsing history, or
inject an interface into the webpage. It shows at most a few high-confidence
results in the extension popup.

## Your memory is yours

Orbit is local-first on purpose.

Each memory is a normal Markdown file in a folder you choose. The original
source URL, capture time, captured text snapshot, snapshot hash, and optional
personal note remain durable, human-readable data. AI summaries, tags, links,
and embeddings are derived around that evidence; they are not a replacement
for it.

You can open the folder with a text editor, VS Code, Obsidian, or any future
tool that understands Markdown. Obsidian is useful compatibility, not a
requirement or the product's storage layer.

```text
Chrome extension
      ↓
Orbit local service
      ↓
Markdown               ← canonical memory
      ↓
SQLite + vectors        ← derived and rebuildable
```

Public v0.1 uses [Ollama](https://ollama.com/) for optional local enrichment
and semantic retrieval. Orbit does not install Ollama or download model files
silently. Without AI, Capture and lexical Recall continue to work.

- No Orbit account
- No Orbit cloud
- No hosted vector database
- No required cloud AI
- No telemetry

AI may help interpret a memory. It must never become the only place the memory
exists.

## Principles

1. **Saving should feel natural.** Orbit should fit behavior people already
   have instead of creating another inbox to maintain.
2. **The original should survive.** A summary is not the source. Captured
   evidence and provenance remain intact while derived state stays rebuildable.
3. **AI should be optional.** Capture and basic Recall must keep working when
   AI is unavailable.
4. **Local-first means ownership.** The durable copy belongs to the user, not
   merely to an application's offline cache.
5. **Quiet is a feature.** Three strong memories are better than twenty weak
   suggestions. If Orbit has nothing useful to say, it should say nothing.
6. **Memory should not become homework.** Orbit resists mandatory folders,
   graphs, templates, tags, and maintenance rituals.

## What Orbit is not trying to become

- Another Notion or Obsidian
- A generic bookmark manager
- A graph-first knowledge-management system
- “ChatGPT for your bookmarks”
- An AI summary generator with no durable source
- A complete screen or browsing-history recorder
- A mandatory cloud service
- An agent platform with dozens of integrations

The focus is the gap between **encountering something useful** and **having it
become useful again**.

## Questions, not a roadmap

There are broader memory questions worth investigating: whether old information
can indicate that it may no longer be safe to rely on, whether context can act
as a future recall cue, and whether precedent is more useful than surface-level
similarity.

None of those are promised v0.1 features. Orbit does not currently monitor
webpages, track assumptions, detect changes, or generate alerts. They are
research questions to validate before building anything.

## Current status

Orbit is early v0.1 software. The packaged runtime currently targets:

- Apple-silicon macOS 12 or newer
- Chrome
- Ollama for optional local AI

Capture and lexical Recall can run without AI. Windows and Linux can be used
for source development, but public launchers are not packaged. Firefox, Safari,
mobile, sync, accounts, and collaboration are not available.

The Chrome Web Store listing is not published yet. Locally built macOS
artifacts must still be signed and notarized before normal public distribution.

## Try it

For the prerelease flow:

1. Build or obtain `Orbit-macOS-arm64-0.1.0.zip`, unzip it, and move
   `Orbit.app` to Applications.
2. Open Orbit and choose where the Markdown memories should live. The default
   is `~/Documents/Orbit`.
3. Choose Local AI or No AI. Local AI uses an already-installed Ollama runtime;
   Orbit reports missing requirements and download sizes before you act.
4. Unzip `orbit-extension-0.1.0.zip`, open `chrome://extensions`, enable
   Developer mode, choose **Load unpacked**, and select the extracted extension
   folder.
5. Save something. The toolbar popup should show **Saved ✓** and the optional
   “Why are you saving this?” prompt.
6. Open Recall and search however you remember it.

See [RELEASE.md](RELEASE.md) for upgrades, uninstall behavior, data locations,
packaging, and source setup.

### Optional: use the folder as an Obsidian vault

In Obsidian, choose **Open folder as vault** and select Orbit's memory folder.
Orbit's Markdown remains usable without Obsidian.

## How it works

```text
                     ┌────────────────────┐
                     │      the web       │
                     └─────────┬──────────┘
                               │ save normally
                     ┌─────────▼──────────┐
                     │ Chrome extension   │
                     │ capture + retry    │
                     └─────────┬──────────┘
                               │ localhost
                     ┌─────────▼──────────┐
                     │ Orbit local server │
                     │ extract / enrich   │
                     │ embed / retrieve   │
                     └─────────┬──────────┘
                               │ durable write first
                     ┌─────────▼──────────┐
                     │      Markdown      │
                     │  source of truth   │
                     └─────────┬──────────┘
                               │ derived index
                     ┌─────────▼──────────┐
                     │ SQLite + vectors   │
                     │    rebuildable     │
                     └────────────────────┘
```

The extension observes X's `CreateBookmark` network operation and uses a
context menu for webpages, links, YouTube, and selections. The FastAPI service
accepts captures asynchronously, extracts useful source content, optionally
enriches it, writes Markdown, and only then attempts embedding. Provider failure
cannot roll back a durable memory.

Implementation details:

- [ORBIT-PROTOCOL.md](ORBIT-PROTOCOL.md) — canonical data and persistence rules
- [extension-docs.md](extension-docs.md) — Chrome capture and retry layer
- [server-docs.md](server-docs.md) — local processing service
- [RELEASE.md](RELEASE.md) — install, packaging, upgrade, and uninstall

## Developer setup and verification

```bash
./.venv/bin/pip install -r requirements.txt -r requirements-build.txt
./.venv/bin/uvicorn server:app --port 8000
```

Load the development extension from this repository's `extension/` directory,
not the repository root.

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m compileall -q server.py providers.py backfill.py \
  backfill_embeddings.py migrate_to_obsidian.py orbit_app.py orbit_config.py
node --test tests/test_recall_ui.js tests/test_page_context.js
node --check extension/background.js
node --check extension/content.js
node --check extension/interceptor.js
node --check extension/popup.js
node --check extension/recall.js
```

Maintenance commands are deliberately explicit and resumable:

```bash
./.venv/bin/python backfill.py --dry-run
./.venv/bin/python backfill.py
./.venv/bin/python backfill_embeddings.py --dry-run
./.venv/bin/python backfill_embeddings.py
```

`backfill.py` creates a vault backup before making repairs.
`backfill_embeddings.py` changes only the rebuildable SQLite vector index.

## Contributing

You do not need to begin with a large feature or pull request. Useful
contributions include:

- anonymized Recall queries that should have worked but did not
- extraction failures on unusual or dynamic webpages
- accessibility and keyboard-navigation fixes
- non-English retrieval examples
- packaging and platform investigations
- privacy and data-safety reviews
- documentation corrections

For a substantial change, please open an issue first and describe the user
problem—not only the proposed feature. Contributions should preserve local
ownership, portable data, graceful degradation, original-source evidence, and
rebuildable derived AI state.

The project especially welcomes disagreement about its direction. If another
tool solves the problem better, sharing that comparison is useful research.

## Similar projects

Orbit exists in an active space and does not claim to have invented webpage
capture, semantic search, local AI, or resurfacing. Related projects include:

- [Karakeep](https://github.com/karakeep-app/karakeep)
- [Linkwarden](https://github.com/linkwarden/linkwarden)
- [Obsidian Web Clipper](https://github.com/obsidianmd/obsidian-clipper)
- [Khoj](https://github.com/khoj-ai/khoj)

Orbit's narrower question is whether software can make things a person
deliberately chose to remember useful again at the right moment while the
memory remains theirs.

## Known limitations

- X capture depends on X's internal GraphQL response shape and may require an
  interceptor update when X changes it.
- Captures queue while the local service is stopped but cannot be processed
  until it returns.
- Article extraction cannot bypass every paywall, login, or unusual renderer.
- Semantic behavior depends on local embedding availability; lexical Recall is
  the degraded fallback.
- Generated Obsidian wikilinks use exact tag overlap. Semantic Related remains
  derived and is not materialized as a graph.

## License

[MIT](LICENSE). Use it, fork it, break it, and build something stranger with it.
