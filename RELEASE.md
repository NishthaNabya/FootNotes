# FootNotes v0.1 release and installation

## What ships

- `FootNotes-macOS-arm64-0.1.0.zip`: a self-contained macOS app. The bundled
FastAPI runtime runs without a Terminal window and logs to
  `~/Library/Logs/FootNotes/footnotes.log`.
- `footnotes-extension-0.1.0.zip`: a Manifest V3 package containing only the files
  Chrome executes. It is ready to upload to Chrome Web Store; listing review
  and approval remain manual.

v0.1 supports Apple-silicon macOS 12 or newer. Developer source operation can
still work elsewhere, but Windows/Linux public launchers are not packaged yet.

## Install

Unzip the macOS artifact, move `FootNotes.app` to `/Applications`, and open it.
On first run:

1. Choose where your memories live. The default is `~/Documents/FootNotes`.
2. Choose Local AI or No AI. Local AI uses an already-installed Ollama runtime.
   FootNotes detects missing prerequisites and shows download sizes/instructions;
   it never installs software or model weights itself.
3. Read the short local/provider privacy explanation and finish setup.
4. Install FootNotes from Chrome Web Store when published. For prerelease testing,
   unzip the extension artifact and use Chrome's supported **Load unpacked**
   flow on that extracted folder.
5. Save one item and confirm **Saved ✓** in the extension. Press ⌘⇧O for Recall.

Chrome does not allow an app to silently install an extension. FootNotes does not
work around that security boundary.

## Where data and settings live

- Memories: the user-selected folder (default `~/Documents/FootNotes`). Markdown is
  canonical; `ingest.log` in the same folder contains rebuildable metadata and
  embeddings.
- Settings: `~/Library/Application Support/FootNotes/config.json`.
- Logs: `~/Library/Logs/FootNotes/footnotes.log`.

## Upgrade

Quit/stop FootNotes from its status page, replace `FootNotes.app`, and reopen it. Do not
move or delete the selected memory folder or Application Support settings.
Database migrations are additive. Missing/stale embeddings are repaired in
small resumable background batches and never block startup.

An upgraded configuration naming an unsupported provider is migrated to No AI,
so startup, Capture, and lexical Recall remain available. The user can choose
Local AI from FootNotes's setup surface when Ollama is ready. Historical embedding
rows for other providers remain inert derived data; Ollama creates its own rows
incrementally without rewriting Markdown.

To bring an older source checkout vault into the app, choose that existing
`vault/` directory during first-run setup. FootNotes uses the files in place; it
does not copy, rename, or rewrite them merely because they were selected.

## Uninstall

Stop FootNotes and move `/Applications/FootNotes.app` to Trash. Remove the Chrome
extension normally. This intentionally leaves the memory folder, settings,
and derived SQLite data in place so reinstalling is safe.

If the user explicitly wants a complete settings cleanup, they may separately
remove `~/Library/Application Support/FootNotes` and `~/Library/Logs/FootNotes`.

Never remove the chosen memory folder unless the user separately decides to
delete their memories.

## Build from source

```bash
./.venv/bin/pip install -r requirements.txt -r requirements-build.txt
./scripts/build_release.sh
```

The extension packager uses an exact allowlist. The app packager uses
PyInstaller onedir mode so failures are diagnosable and updates do not mutate
user data. Neither artifact includes `.venv`, caches, repo vault data, `.env`,
tests, or API keys. Build the macOS artifact on the oldest supported macOS/CPU
target, then code-sign and notarize it before public distribution.

The local build command intentionally fails to claim signing. A public artifact
must additionally be signed with an Apple Developer ID and notarized; an
unsigned local build may be blocked by Gatekeeper on another Mac.

## Developer setup

Source contributors may continue using `.env` and:

```bash
./.venv/bin/uvicorn server:app --port 8000
```

Developer Chrome loading remains `chrome://extensions` → **Load unpacked** →
select this repository's `extension/` directory.
