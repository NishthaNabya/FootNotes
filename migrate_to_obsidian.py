"""
migrate_to_obsidian.py — One-time conversion from two append-only files to
one file per entry.

Obsidian's graph view draws an edge for every [[wikilink]] between two
FILES. The original layout — vault/bookmarks.md and vault/transcripts.md,
each holding every entry of its kind appended in sequence — has no files to
link between; Obsidian would show two disconnected dots no matter how much
was captured. This script splits both files into vault/<type>/<slug>.md,
then runs the auto-linker so entries sharing tags are connected from the
start.

Uses backfill.py's legacy parser (unchanged, still targets the old format)
to read the existing files, and server.py's live write path to create the
new ones — so migrated entries are byte-for-byte what a fresh write would
have produced, just backdated to their original id/timestamp/status.

Safe to run only once. It moves the source files aside as it goes, so a
second run finds nothing left to migrate and exits immediately.

Usage:
    python migrate_to_obsidian.py --dry-run     # report, touch nothing
    python migrate_to_obsidian.py               # migrate for real
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the vault to one file per entry.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    args = parser.parse_args()

    import server
    from backfill import parse_vault_file, _as_str, _as_list

    legacy_files = [server.LEGACY_BOOKMARKS_FILE, server.LEGACY_TRANSCRIPTS_FILE]
    if not any(f.exists() for f in legacy_files):
        print("[Migrate] No legacy vault files found — already migrated, or nothing to do.")
        return 0

    raw: list[tuple[dict, str]] = []
    for f in legacy_files:
        parsed = parse_vault_file(f)
        print(f"[Migrate] {f.name}: {len(parsed)} entries parsed")
        raw.extend(parsed)

    if not raw:
        print("[Migrate] Legacy files exist but are empty. Nothing to migrate.")
        return 0

    if args.dry_run:
        for meta, content in raw:
            folder = server.TYPE_FOLDERS.get(_as_str(meta.get("type")), "articles")
            print(f"  -> {folder}/  {_as_str(meta.get('title'))[:50] or meta.get('id')}")
        print(f"\n[Migrate] Dry run — {len(raw)} entries would move. Nothing written.")
        return 0

    written = 0
    for meta, content in raw:
        payload = server.IngestPayload(
            type=_as_str(meta.get("type")),
            source_url=_as_str(meta.get("source_url")),
            source_platform=_as_str(meta.get("source_platform")),
            author=_as_str(meta.get("author")),
            author_handle=_as_str(meta.get("author_handle")),
            title=_as_str(meta.get("title")),
            captured_at=_as_str(meta.get("captured_at")),
            published_at=_as_str(meta.get("published_at")) or None,
            content=content,
        )
        entry_id = _as_str(meta.get("id"))
        content_header = "Transcript" if payload.type == "youtube" and content and not server.is_placeholder_content(content) else "Content"

        # Write directly rather than through write_entry_file: that function
        # computes related links per-write, which would leave earlier
        # entries in this same batch unlinked to later ones. One full
        # rebuild_related_links() pass after every file exists is strictly
        # better here, and cheaper than relinking on every single write.
        path = server.entry_path(payload, entry_id)
        text = server.format_bookmark_entry(
            payload=payload,
            entry_id=entry_id,
            tags=_as_list(meta.get("tags")),
            summary=meta.get("summary") or None,
            key_insights=_as_list(meta.get("key_insights")),
            status=_as_str(meta.get("status")) or "ingested",
            content_header=content_header,
        )
        path.write_text(text, encoding="utf-8")
        written += 1

    print(f"[Migrate] Wrote {written} entries into per-type folders.")

    changed = server.rebuild_related_links()
    print(f"[Migrate] Linked entries by shared tags: {changed} files gained a Related section.")

    for f in legacy_files:
        if f.exists():
            backup = f.with_suffix(f.suffix + ".pre-obsidian.bak")
            f.rename(backup)
            print(f"[Migrate] Moved {f.name} -> {backup.name} (kept as a backup, not read by anything)")

    print(f"\n[Migrate] Done. {written} entries now live under vault/{{tweets,articles,youtube}}/.")
    print("[Migrate] Open the vault/ folder in Obsidian to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
