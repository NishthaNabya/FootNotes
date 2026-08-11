"""
backfill.py — Repair and re-enrich existing vault entries.

The vault is the source of truth. This script reads it, fixes what the old
pipeline got wrong, and writes it back. It is idempotent: running it twice
changes nothing the second time.

What it repairs, in order:

1. Deduplicates entries sharing a normalized URL, keeping the richest copy.
2. Re-derives `type` and `source_platform` from the URL, so YouTube videos
   filed as articles become videos — and get routed to transcripts.md.
3. Recovers tweet authors lost by the old interceptor, via X's public oEmbed
   endpoint. No auth required.
4. Re-fetches YouTube transcripts and metadata that the old broken
   list_transcripts() call never got.
5. Re-extracts article text where only a stub was saved.
6. Re-enriches anything with real content and no tags.
7. Rebuilds the SQLite ingest log so deduplication works going forward.

Usage:
    python backfill.py --dry-run     # report what would change, touch nothing
    python backfill.py               # repair in place (writes a .bak first)
    python backfill.py --force       # also re-enrich entries that already have tags

Requires the same environment as server.py — a .env with a working
GEMINI_API_KEY. Without one, steps 1-5 still run and entries are left
'ingested' rather than being falsely marked enriched.
"""

import argparse
import asyncio
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import httpx
import yaml

# Entry starts: a frontmatter fence immediately followed by an id field.
# Entry separators ('---' between entries) and horizontal rules inside tweet
# content can't match this, because they aren't followed by `id:`.
ENTRY_START = re.compile(r"^---\n(?=id:)", re.M)

# Body content sits between the content header and the Context footer.
CONTENT_BLOCK = re.compile(
    r"^## (?:Content|Transcript)\n\n(.*?)\n\n## Context\n", re.S | re.M
)


def parse_vault_file(path: Path) -> list[tuple[dict, str]]:
    """Return (frontmatter_dict, content_text) for every entry in a vault file."""
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    starts = [m.start() for m in ENTRY_START.finditer(text)]
    entries = []

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[start:end]

        fence = block.find("\n---\n", 4)
        if fence == -1:
            continue  # truncated entry (e.g. mid-write crash) — skip, don't die
        fm_raw = block[4:fence]
        body = block[fence + 5:]

        try:
            meta = yaml.safe_load(fm_raw) or {}
        except yaml.YAMLError:
            meta = _naive_frontmatter(fm_raw)
        if not isinstance(meta, dict) or not meta.get("id"):
            continue

        match = CONTENT_BLOCK.search(body)
        entries.append((meta, match.group(1).strip() if match else ""))

    return entries


def _naive_frontmatter(fm_raw: str) -> dict:
    """Line-by-line fallback for frontmatter YAML can't parse. Scalars only."""
    meta = {}
    for line in fm_raw.splitlines():
        if ":" not in line or line.startswith((" ", "-")):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"') or None
    return meta


def _as_str(value) -> str:
    """Coerce YAML-parsed values (datetimes, None, numbers) to plain strings."""
    return "" if value is None else str(value)


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


# ──────────────────────────────────────────────
# Author recovery
# ──────────────────────────────────────────────

UNKNOWN_AUTHORS = {"", "unknown", "@unknown", "none"}

# What trafilatura returns when it's pointed at a YouTube watch page: the site
# footer, not the video. Two entries in the original vault have this as their
# entire body. It is not content, and enriching it yields tags about Google's
# terms of service.
YOUTUBE_CHROME_MARKERS = (
    "About\nPress\nCopyright",
    "How YouTube works",
    "NFL Sunday Ticket",
)


def looks_like_youtube_chrome(content: str) -> bool:
    """True when the body is YouTube's page furniture rather than the video."""
    head = content.strip()[:400]
    return sum(marker in head for marker in YOUTUBE_CHROME_MARKERS) >= 2


async def recover_tweet_author(url: str) -> tuple[str, str]:
    """
    Look up a tweet's real author through X's public oEmbed endpoint.

    The old interceptor cached tweet.legacy without the user object, so every
    captured tweet claims author "unknown". oEmbed is unauthenticated and
    returns both display name and handle, which is enough to repair them.

    Returns ("", "") when the tweet is deleted, private, or the endpoint fails.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                "https://publish.twitter.com/oembed",
                params={"url": url, "omit_script": "1"},
            )
        if r.status_code != 200:
            return "", ""
        data = r.json()
        name = data.get("author_name") or ""
        handle = (data.get("author_url") or "").rstrip("/").split("/")[-1]
        return name, (f"@{handle}" if handle else "")
    except Exception:
        return "", ""


# ──────────────────────────────────────────────
# Repair
# ──────────────────────────────────────────────


def choose_best(duplicates: list[tuple[dict, str]]) -> tuple[dict, str]:
    """
    Pick the copy worth keeping when a URL appears more than once.

    Longest real content wins; a placeholder body never beats a real one.
    Ties break toward the earliest capture, so the original id survives.
    """
    import server

    def score(pair):
        meta, content = pair
        real = 0 if server.is_placeholder_content(content) else 1
        return (real, len(content), -_captured_sort_key(meta))

    return max(duplicates, key=score)


def _captured_sort_key(meta: dict) -> float:
    raw = _as_str(meta.get("captured_at"))
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


async def repair_entry(meta: dict, content: str, force: bool, dry_run: bool) -> tuple:
    """
    Bring one entry up to current standards.

    Returns (payload, content, tags, summary, insights, status, actions) where
    `actions` lists the human-readable repairs applied.
    """
    import server

    actions = []
    url = _as_str(meta.get("source_url"))

    real_type = server.detect_type(url, _as_str(meta.get("type")))
    real_platform = server.detect_platform(url)
    if real_type != _as_str(meta.get("type")):
        actions.append(f"type {meta.get('type')}→{real_type}")

    title = _as_str(meta.get("title"))
    author = _as_str(meta.get("author"))
    handle = _as_str(meta.get("author_handle"))
    published = _as_str(meta.get("published_at")) or None

    # ── Tweets: recover the author the old interceptor dropped ──
    if real_type in ("tweet", "thread") and author.strip().lower() in UNKNOWN_AUTHORS:
        if not dry_run:
            name, screen = await recover_tweet_author(url)
            if name:
                author, handle = name, screen
                actions.append(f"author unknown→{screen or name}")
        else:
            actions.append("author unknown→(oembed lookup)")

    # ── YouTube: transcript + metadata the old code never fetched ──
    if real_type == "youtube":
        video_id = server.extract_video_id(url)
        junk = looks_like_youtube_chrome(content)
        if junk:
            actions.append("discarded page chrome")
        if video_id and (
            junk or server.is_placeholder_content(content) or not content.strip()
        ):
            if dry_run:
                actions.append("fetch transcript")
            else:
                transcript = await server.get_youtube_transcript(video_id)
                if transcript:
                    content = transcript
                    actions.append(f"transcript +{len(transcript):,}c")
                else:
                    # Leave the standard marker so enrichment skips it rather
                    # than tagging YouTube's footer.
                    content = (
                        f"[No transcript available for this video]\n\nURL: {url}"
                    )
                    actions.append("transcript unavailable")

        needs_meta = (
            not author
            or server.is_placeholder_title(title)
        )
        if video_id and needs_meta:
            if dry_run:
                actions.append("fetch metadata")
            else:
                md = await server.extract_youtube_metadata(url)
                if md:
                    if md.get("title") and server.is_placeholder_title(title):
                        title = md["title"]
                        actions.append("title from yt-dlp")
                    if md.get("channel") and not author:
                        author = md["channel"]
                        handle = md.get("channel_id") or author.lower().replace(" ", "_")
                    if md.get("upload_date"):
                        published = server.normalize_upload_date(md["upload_date"])

    # ── Articles: re-extract where only a stub was saved ──
    elif real_type == "article" and (
        server.is_placeholder_content(content) or len(content.strip()) < 200
    ):
        if dry_run:
            actions.append("re-extract article")
        else:
            extracted = await server.extract_article_content(url)
            if extracted and len(extracted) > len(content):
                content = extracted
                actions.append(f"re-extracted +{len(extracted):,}c")

    payload = server.IngestPayload(
        type=real_type,
        source_url=url,
        source_platform=real_platform or _as_str(meta.get("source_platform")),
        author=author,
        author_handle=handle,
        title=title,
        captured_at=_as_str(meta.get("captured_at")),
        published_at=published,
        content=content,
    )

    # ── Enrichment ──
    tags = _as_list(meta.get("tags"))
    summary = meta.get("summary") or None
    insights = _as_list(meta.get("key_insights"))
    status = _as_str(meta.get("status")) or "ingested"

    needs_enrichment = force or not tags
    if server.is_placeholder_content(content) or not content.strip():
        # Never enrich our own "nothing found" marker — that's how entries got
        # marked 'enriched' with empty tags in the first place.
        status = "ingested"
        if needs_enrichment:
            actions.append("skip enrich (no content)")
    elif needs_enrichment:
        if dry_run:
            actions.append("enrich")
        else:
            result = await server.enrich_with_llm(payload)
            if result.get("ok"):
                tags = result["tags"]
                summary = result["summary"]
                insights = result["key_insights"]
                status = "enriched"
                actions.append(f"enriched {len(tags)}t/{len(insights)}i")
            else:
                status = "ingested"
                actions.append(f"enrich failed ({result.get('reason')})")
    elif tags and status != "enriched":
        status = "enriched"

    return payload, content, tags, summary, insights, status, actions


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description="Repair and re-enrich vault entries.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change; touch nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich entries that already have tags.")
    args = parser.parse_args()

    import server

    files = [server.BOOKMARKS_FILE, server.TRANSCRIPTS_FILE]
    raw: list[tuple[dict, str]] = []
    for f in files:
        parsed = parse_vault_file(f)
        print(f"[Backfill] {f.name}: {len(parsed)} entries parsed")
        raw.extend(parsed)

    if not raw:
        print("[Backfill] Nothing to do — vault is empty.")
        return 0

    # ── Deduplicate on normalized URL ──
    groups: dict[str, list] = {}
    for meta, content in raw:
        key = server.normalize_url(_as_str(meta.get("source_url"))) or meta["id"]
        groups.setdefault(key, []).append((meta, content))

    unique = []
    dropped = 0
    for key, members in groups.items():
        if len(members) > 1:
            dropped += len(members) - 1
            print(f"[Backfill] Deduplicating x{len(members)}: {key[:66]}")
        unique.append(choose_best(members))

    print(f"[Backfill] {len(raw)} entries → {len(unique)} unique ({dropped} duplicates dropped)")

    if not args.dry_run:
        await server.verify_gemini_credentials()

    # ── Repair each entry ──
    repaired = []
    for meta, content in sorted(unique, key=lambda pair: _captured_sort_key(pair[0])):
        result = await repair_entry(meta, content, args.force, args.dry_run)
        payload, content, tags, summary, insights, status, actions = result
        repaired.append((meta["id"], payload, tags, summary, insights, status))
        label = (payload.title or payload.source_url)[:46]
        print(f"  {status:9s} {payload.type:8s} {label:48s} {'; '.join(actions) or '(no change)'}")

    if args.dry_run:
        print("\n[Backfill] Dry run — nothing written.")
        return 0

    # ── Back up, then rewrite both vault files from scratch ──
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for f in files:
        if f.exists():
            backup = f.with_suffix(f".{stamp}.bak")
            shutil.copy2(f, backup)
            print(f"[Backfill] Backed up {f.name} → {backup.name}")

    buckets = {server.BOOKMARKS_FILE: [], server.TRANSCRIPTS_FILE: []}
    for entry_id, payload, tags, summary, insights, status in repaired:
        header = (
            "Transcript"
            if payload.type == "youtube" and not server.is_placeholder_content(payload.content)
            else "Content"
        )
        rendered = server.format_bookmark_entry(
            payload=payload, entry_id=entry_id, tags=tags, summary=summary,
            key_insights=insights, status=status, content_header=header,
        )
        target = (
            server.TRANSCRIPTS_FILE if payload.type == "youtube" else server.BOOKMARKS_FILE
        )
        buckets[target].append(rendered)

    for path, entries in buckets.items():
        path.write_text("".join(entries), encoding="utf-8")
        print(f"[Backfill] Wrote {len(entries)} entries → {path.name}")

    # ── Rebuild the ingest log so dedup works from here on ──
    server.init_ingest_log()
    conn = sqlite3.connect(str(server.INGEST_LOG_DB))
    conn.execute("DELETE FROM ingest_log")
    conn.commit()
    conn.close()
    for entry_id, payload, _t, _s, _i, status in repaired:
        server.log_ingest_entry(entry_id, payload.source_url, payload.type, status)
    print(f"[Backfill] Rebuilt ingest log with {len(repaired)} entries")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
