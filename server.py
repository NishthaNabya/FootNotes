"""
server.py — FootNotes V2 Processing Layer
FastAPI server with async queue, file locking, and background worker.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import signal
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import aiofiles
import trafilatura
import yaml
import yt_dlp
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from providers import (
    NoAIProvider,
    OllamaProvider,
    OLLAMA_EMBEDDING_DIMENSIONS,
)
from footnotes_config import (
    FootNotesConfig,
    load_config,
    save_config,
)
from youtube_transcript_api import YouTubeTranscriptApi

# Load environment variables from .env file
load_dotenv()
PRODUCT_MODE = os.getenv("FOOTNOTES_PRODUCT_MODE", "").lower() in ("1", "true", "yes")
PRODUCT_CONFIG = load_config() if PRODUCT_MODE else None

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

VAULT_DIR = (
    Path(os.getenv("FOOTNOTES_VAULT_DIR", "")).expanduser()
    if os.getenv("FOOTNOTES_VAULT_DIR", "").strip()
    else PRODUCT_CONFIG.resolved_vault_path
    if PRODUCT_CONFIG is not None
    else Path(__file__).parent / "vault"
)
INGEST_LOG_DB = VAULT_DIR / "ingest.log"

# One markdown file per entry, grouped by type into subfolders. This is the
# Obsidian-compatible layout: Obsidian's graph view draws an edge for every
# [[wikilink]] between two FILES, so two giant append-only files (the old
# bookmarks.md / transcripts.md) rendered as two disconnected dots no matter
# how much content they held. See rebuild_related_links() below for how
# entries actually get linked to each other.
TYPE_FOLDERS = {
    "tweet": "tweets",
    "thread": "tweets",
    "youtube": "youtube",
    "article": "articles",
    "podcast": "articles",  # no extraction path produces this yet; safe fallback
}

# Pre-migration paths. Nothing in the live pipeline writes here anymore;
# these exist only so migrate_to_obsidian.py can find the old files once.
LEGACY_BOOKMARKS_FILE = VAULT_DIR / "bookmarks.md"
LEGACY_TRANSCRIPTS_FILE = VAULT_DIR / "transcripts.md"

MAX_WORKER_CONCURRENCY = 3
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

# Downloading full MP4s is off by default: it costs hundreds of MB per video and
# only buys the multimodal path, which the transcript usually covers. Opt in with
# FOOTNOTES_DOWNLOAD_VIDEOS=1. When enabled the download is awaited (see the worker),
# because enrichment cannot analyze a file that hasn't finished writing.
DOWNLOAD_VIDEOS = os.getenv("FOOTNOTES_DOWNLOAD_VIDEOS", "").lower() in ("1", "true", "yes")

# Long transcripts are mostly redundant for tag extraction. Bound the provider
# input so local enrichment remains responsive and memory use is predictable.
MAX_ENRICHMENT_CHARS = 100_000

VAULT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("footnotes")

# ──────────────────────────────────────────────
# Pydantic Models
# ──────────────────────────────────────────────


class IngestPayload(BaseModel):
    type: str = Field(..., description="tweet, thread, youtube, article, podcast")
    source_url: str = Field(..., description="Canonical URL of the source")
    source_platform: str = Field(..., description="x, youtube, medium, substack, other")
    author: str = Field(default="", description="Display name")
    author_handle: str = Field(default="", description="Normalized handle")
    title: str = Field(default="", description="Headline or first line")
    captured_at: str = Field(..., description="ISO 8601 timestamp of capture")
    published_at: Optional[str] = Field(default=None, description="Original publish date")
    content: str = Field(default="", description="Raw text content")
    selection: Optional[str] = Field(default=None, description="User-selected text if any")
    user_note: Optional[str] = Field(default=None, max_length=500, description="Optional user-authored context")


class UserNoteUpdate(BaseModel):
    user_note: str = Field(default="", max_length=500)


class SetupUpdate(BaseModel):
    vault_path: str = Field(..., max_length=4096)
    provider: str = Field(default="ollama", pattern="^(none|ollama)$")


class PageContext(BaseModel):
    url: str = Field(..., max_length=2048)
    title: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=6000)


class EnrichmentResult(BaseModel):
    """Provider-neutral structured enrichment result."""
    tags: list[str] = Field(default_factory=list, description="3-7 lowercase topical tags")
    summary: str = Field(default="", description="One-sentence summary of the core idea")
    key_insights: list[str] = Field(default_factory=list, description="2-5 standalone insights")


# ──────────────────────────────────────────────
# Async resources
# ──────────────────────────────────────────────

# Async queue for non-blocking ingestion
ingest_queue: asyncio.Queue = asyncio.Queue()

# Serializes vault writes. Each entry gets its own uniquely-named file, so
# this isn't preventing two writers from colliding on one file (that can't
# happen); it's cheap insurance around find_related()'s vault-wide scan in
# write_entry_file(), in case a maintenance script ever runs concurrently
# with the live server.
vault_write_lock = asyncio.Lock()

# Provider concurrency semaphore avoids overwhelming cloud or local runtimes.
llm_semaphore = asyncio.Semaphore(MAX_WORKER_CONCURRENCY)

def build_intelligence_provider(provider_name: str):
    if provider_name == "ollama":
        config = load_config()
        return OllamaProvider(
            base_url=os.getenv("FOOTNOTES_OLLAMA_URL", "http://127.0.0.1:11434"),
            embedding_model=os.getenv("FOOTNOTES_OLLAMA_EMBEDDING_MODEL", config.ollama_embedding_model),
            enrichment_model=os.getenv("FOOTNOTES_OLLAMA_ENRICHMENT_MODEL", config.ollama_enrichment_model),
            dimensions=OLLAMA_EMBEDDING_DIMENSIONS,
        )
    return NoAIProvider()


def selected_provider_name(config: Optional[FootNotesConfig]) -> str:
    override = os.getenv("FOOTNOTES_INTELLIGENCE_PROVIDER", "").strip().lower()
    if override in {"ollama", "none"}:
        return override
    if override:
        return "none"
    if config is not None:
        return config.provider if config.provider in {"ollama", "none"} else "none"
    return "none"

# Retrieval and persistence depend only on the small provider contract in
# providers.py. Tests and future local providers can replace this object
# without changing the vault or search implementation.
ACTIVE_PROVIDER_NAME = selected_provider_name(PRODUCT_CONFIG)
intelligence_provider = build_intelligence_provider(ACTIVE_PROVIDER_NAME)
embedding_provider = intelligence_provider
provider_health = {
    "provider": ACTIVE_PROVIDER_NAME,
    "runtime_available": False,
    "embedding_ready": False,
    "enrichment_ready": False,
    "missing_models": [],
    "message": "Not checked yet.",
}

# Ephemeral only: keys and results disappear on process restart and are never
# written to SQLite or Markdown. This avoids repeated provider calls for an
# unchanged page without turning browsing into stored history.
resurface_cache: dict[str, tuple[float, dict]] = {}
embedding_reconciliation = {
    "running": False,
    "processed_this_session": 0,
    "last_result": "idle",
}


def configure_runtime(vault_path: Path, provider: str) -> None:
    """Apply first-run settings without rewriting any existing memory.

    This is used only by the local setup surface.  New work immediately uses
    the selected folder; Markdown and its adjacent derived SQLite index move
    only when the user explicitly selects an existing folder.
    """
    global VAULT_DIR, INGEST_LOG_DB, LEGACY_BOOKMARKS_FILE
    global LEGACY_TRANSCRIPTS_FILE, VIDEOS_DIR, embedding_provider
    global intelligence_provider, provider_health, ACTIVE_PROVIDER_NAME

    resolved = Path(vault_path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    VAULT_DIR = resolved
    INGEST_LOG_DB = VAULT_DIR / "ingest.log"
    LEGACY_BOOKMARKS_FILE = VAULT_DIR / "bookmarks.md"
    LEGACY_TRANSCRIPTS_FILE = VAULT_DIR / "transcripts.md"
    VIDEOS_DIR = VAULT_DIR / "videos"
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    provider = provider if provider in {"ollama", "none"} else "none"
    ACTIVE_PROVIDER_NAME = provider
    intelligence_provider = build_intelligence_provider(provider)
    embedding_provider = intelligence_provider
    provider_health = {
        "provider": provider, "runtime_available": False,
        "embedding_ready": False, "enrichment_ready": False,
        "missing_models": [], "message": "Not checked yet.",
    }
    resurface_cache.clear()
    init_ingest_log()


# ──────────────────────────────────────────────
# URL Normalization (the basis of deduplication)
# ──────────────────────────────────────────────

# Tracking junk that changes the URL string without changing the content behind it.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src",
    "ref_url", "s", "t", "pp", "si", "feature",
}


def normalize_url(url: str) -> str:
    """
    Reduce a URL to a stable identity so the same content captured twice
    (once from the timeline, once from the permalink) dedupes to one entry.

    Strips tracking params, the fragment, a trailing slash, and 'www.'.
    YouTube is special-cased: only the video id identifies the content, so
    youtu.be/ID and youtube.com/watch?v=ID&t=42 collapse to the same key.
    """
    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    # YouTube: the video id is the whole identity.
    video_id = extract_video_id(url)
    if video_id and ("youtube" in host or "youtu.be" in host):
        return f"youtube.com/watch?v={video_id}"

    # x.com and twitter.com are the same site; the handle in a status URL is
    # cosmetic (X redirects any handle to the right tweet), so key on the id.
    if host in ("x.com", "twitter.com", "mobile.x.com", "mobile.twitter.com"):
        match = re.search(r"/status/(\d+)", parts.path)
        if match:
            return f"x.com/status/{match.group(1)}"
        host = "x.com"

    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in TRACKING_PARAMS]
    )
    path = parts.path.rstrip("/") or "/"

    return urlunsplit(("", host, path, query, "")).lstrip("/") or host


# ──────────────────────────────────────────────
# Ingest Log (SQLite — metadata only)
# ──────────────────────────────────────────────


def init_ingest_log():
    """Initialize SQLite metadata log for tracking ingest status and retries."""
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_log (
            id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            normalized_url TEXT,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            retries INTEGER DEFAULT 0,
            user_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Migration: older databases predate normalized_url. Add it, then backfill,
    # so an existing vault starts deduplicating without a manual reset.
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(ingest_log)")}
    if "normalized_url" not in columns:
        logger.info("[DB] Migrating ingest_log: adding normalized_url")
        cursor.execute("ALTER TABLE ingest_log ADD COLUMN normalized_url TEXT")
        for row_id, url in cursor.execute(
            "SELECT id, source_url FROM ingest_log"
        ).fetchall():
            cursor.execute(
                "UPDATE ingest_log SET normalized_url = ? WHERE id = ?",
                (normalize_url(url), row_id),
            )
    if "user_note" not in columns:
        logger.info("[DB] Migrating ingest_log: adding user_note")
        cursor.execute("ALTER TABLE ingest_log ADD COLUMN user_note TEXT")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_normalized_url ON ingest_log(normalized_url)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            entry_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER,
            content_hash TEXT NOT NULL,
            vector_json TEXT,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entry_id, provider, model)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_embedding_status "
        "ON memory_embeddings(status)"
    )
    conn.commit()
    conn.close()


def log_ingest_entry(
    entry_id: str,
    source_url: str,
    entry_type: str,
    status: str,
    retries: int = 0,
):
    """Insert or update an ingest log entry."""
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO ingest_log
            (id, source_url, normalized_url, type, status, retries, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            status = excluded.status,
            retries = excluded.retries,
            updated_at = excluded.updated_at
        """,
        (entry_id, source_url, normalize_url(source_url), entry_type, status, retries, now, now),
    )
    conn.commit()
    conn.close()


def find_existing_entry(url: str) -> Optional[tuple]:
    """
    Return (id, status) for a previously seen URL, or None.

    This is what makes re-bookmarking idempotent. The old protocol claimed the
    UUID guaranteed deduplication, but a random UUIDv4 minted per ingest can't
    dedupe anything — the normalized URL is the real identity.
    """
    normalized = normalize_url(url)
    if not normalized:
        return None

    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.cursor()
    row = cursor.execute(
        """
        SELECT id, status FROM ingest_log
        WHERE normalized_url = ?
        ORDER BY CASE status WHEN 'enriched' THEN 0 WHEN 'ingested' THEN 1 ELSE 2 END
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    conn.close()
    return row


def set_ingest_user_note(entry_id: str, user_note: str) -> bool:
    """Persist a note against an accepted capture, including while it is queued."""
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.execute(
        "UPDATE ingest_log SET user_note = ?, updated_at = ? WHERE id = ?",
        (user_note, datetime.now(timezone.utc).isoformat(), entry_id),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_ingest_user_note(entry_id: str) -> Optional[str]:
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    row = conn.execute(
        "SELECT user_note FROM ingest_log WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ──────────────────────────────────────────────
# Markdown Template Engine
# ──────────────────────────────────────────────


def format_bookmark_entry(
    payload: IngestPayload,
    entry_id: str,
    tags: list = None,
    summary: str = None,
    key_insights: list = None,
    status: str = "ingested",
    content_header: str = "Content",
    related: list = None,
) -> str:
    """
    Render one entry — YAML frontmatter, body, and (if any) a trailing
    Related section — as the complete contents of its own file.

    Frontmatter is serialized by PyYAML rather than f-string interpolation.
    Hand-rolled quoting broke on any title containing a backslash, a leading
    '@', or an embedded quote — and those failures were silent, producing a
    file that only revealed itself as corrupt when something tried to parse it.

    `related` must be the last thing in the file — see strip_related_section().
    """
    frontmatter = {
        "id": entry_id,
        "type": payload.type,
        "source_url": payload.source_url,
        "source_platform": payload.source_platform,
        "author": payload.author or "",
        "author_handle": payload.author_handle or "",
        "title": _single_line(payload.title),
        "captured_at": payload.captured_at,
        "published_at": payload.published_at,
        "content_hash": captured_content_hash(payload.content),
        "user_note": payload.user_note.strip() if payload.user_note else None,
        "tags": tags or [],
        "summary": summary or None,
        "key_insights": key_insights or [],
        "status": status,
    }

    yaml_block = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # don't hard-wrap long titles into invalid continuations
    )

    return (
        f"---\n{yaml_block}---\n\n"
        f"## {content_header}\n\n"
        f"{payload.content}\n\n"
        "## Context\n\n"
        f"- **Original URL:** {payload.source_url}\n"
        f"- **Captured:** {payload.captured_at}\n"
        f"- **Platform:** {payload.source_platform}"
        f"{render_related_section(related or [])}\n"
    )


def _single_line(value: Optional[str]) -> str:
    """Collapse newlines — a title spanning lines breaks the frontmatter block."""
    if not value:
        return ""
    return " ".join(value.split())


def captured_content_hash(content: str) -> str:
    """Stable fingerprint of the original text snapshot stored in Markdown."""
    return hashlib.sha256((content or "").strip().encode("utf-8")).hexdigest()


# Bodies we wrote ourselves because extraction found nothing. They are not
# content, and sending them to the LLM produces a confidently useless
# "enrichment" of our own error message — an entry marked `enriched` with
# empty tags, which is precisely the state this pipeline exists to avoid.
PLACEHOLDER_PREFIXES = (
    "[Failed to extract content from",
    "[No transcript available",
)


def is_placeholder_content(content: str) -> bool:
    """True when the body is our own 'nothing found' marker rather than content."""
    return content.strip().startswith(PLACEHOLDER_PREFIXES)


# ──────────────────────────────────────────────
# File Naming
# ──────────────────────────────────────────────


def slugify(text: str, max_len: int = 60) -> str:
    """Turn arbitrary text into a filesystem- and Obsidian-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def entry_path(payload: IngestPayload, entry_id: str) -> Path:
    """
    Compute the on-disk path for a new entry: vault/<type-folder>/<slug>-<id8>.md.

    The id suffix guarantees uniqueness even when two captures produce the
    same slug (e.g. two tweets with the same first line); it's the first 8
    hex characters of the full UUID kept in frontmatter, not a new identity.
    """
    base = slugify(payload.title) if payload.title.strip() else slugify(
        urlsplit(payload.source_url).netloc or payload.source_url
    )
    folder_name = TYPE_FOLDERS.get(payload.type, "articles")
    folder = VAULT_DIR / folder_name
    folder.mkdir(exist_ok=True)
    return folder / f"{base}-{entry_id[:8]}.md"


# ──────────────────────────────────────────────
# Content Type Detection
# ──────────────────────────────────────────────


def detect_platform(url: str) -> str:
    """Classify a URL's origin. Mirrors extractPlatform() in extension/background.js."""
    if not url:
        return "other"
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return "other"

    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    if "x.com" in host or "twitter" in host:
        return "x"
    if "medium" in host:
        return "medium"
    if "substack" in host:
        return "substack"
    return "other"


def detect_type(url: str, declared: str) -> str:
    """
    Decide the content type server-side.

    The context menu hardcodes type="article" for everything it captures, which
    is how four YouTube videos ended up filed as articles with the title
    "YouTube". The URL is more trustworthy than the caller's claim, so platform
    detection wins for the cases we can identify with certainty.
    """
    platform = detect_platform(url)
    if platform == "youtube":
        return "youtube"
    if platform == "x" and "/status/" in url:
        return declared if declared in ("tweet", "thread") else "tweet"
    return declared or "article"


# ──────────────────────────────────────────────
# Article Content Extraction
# ──────────────────────────────────────────────


async def extract_article_content(url: str) -> Optional[str]:
    """
    Fetch a URL and extract clean article text using trafilatura.

    Runs in a thread pool to avoid blocking the async event loop,
    since trafilatura is a synchronous library.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _trafilatura_extract, url)


def _trafilatura_extract(url: str) -> Optional[str]:
    """Synchronous wrapper for trafilatura extraction."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            logger.warning(f"[Extractor] Failed to fetch URL: {url}")
            return None

        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            output_format="txt",
        )

        if extracted and len(extracted.strip()) > 50:
            logger.info(f"[Extractor] Extracted {len(extracted)} chars from {url}")
            return extracted.strip()
        else:
            logger.warning(f"[Extractor] Extraction returned empty/short text for {url}")
            return None
    except Exception as e:
        logger.error(f"[Extractor] Error extracting {url}: {e}")
        return None


# ──────────────────────────────────────────────
# YouTube Processing Pipeline
# ──────────────────────────────────────────────

VIDEOS_DIR = VAULT_DIR / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

# Titles browsers report before the SPA has set the real one. Treat as absent
# so yt-dlp's metadata is allowed to overwrite them.
PLACEHOLDER_TITLES = {"", "youtube", "youtube.com", "watch", "x", "twitter", "untitled"}


def is_placeholder_title(title: str) -> bool:
    """
    True when a title is browser chrome rather than the content's real name.

    Covers both the bare "YouTube" a tab reports before the SPA settles, and
    the "<search terms> - YouTube" form you get when the capture happened on a
    results page. Either way yt-dlp's title is strictly better.
    """
    clean = _single_line(title).lower()
    return clean in PLACEHOLDER_TITLES or clean.endswith("- youtube")


def extract_video_id(url: str) -> Optional[str]:
    """Extract the YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


async def extract_youtube_metadata(url: str) -> dict:
    """Use yt-dlp to extract real metadata from a YouTube video."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yt_dlp_metadata, url)


def _yt_dlp_metadata(url: str) -> dict:
    """Synchronous wrapper for yt-dlp metadata extraction."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", ""),
                "channel": info.get("channel", info.get("uploader", "")),
                "channel_id": info.get("uploader_id", ""),
                "upload_date": info.get("upload_date", ""),
                "duration": info.get("duration", 0),
                "description": info.get("description", ""),
                "view_count": info.get("view_count", 0),
            }
    except Exception as e:
        logger.error(f"[YouTube] yt-dlp metadata extraction failed for {url}: {e}")
        return {}


def normalize_upload_date(raw: str) -> Optional[str]:
    """yt-dlp returns YYYYMMDD; the protocol wants ISO 8601."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except (ValueError, TypeError):
        return raw or None


async def get_youtube_transcript(video_id: str) -> Optional[str]:
    """Fetch the full transcript for a YouTube video."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_transcript, video_id)


def _fetch_transcript(video_id: str) -> Optional[str]:
    """
    Synchronous wrapper for transcript fetching.

    youtube-transcript-api 1.0 replaced the static YouTubeTranscriptApi
    .list_transcripts() with an instance .list(), and fetch() now returns
    snippet objects rather than dicts. The old call raised AttributeError on
    every video; the exception was caught and logged as "no transcript
    available", so the pipeline reported a YouTube limitation instead of a
    library upgrade. That's why every video in the vault has an empty body.
    """
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # Prefer manually created transcripts, fall back to auto-generated,
        # then to whatever exists in any language.
        transcript = None
        for finder in (
            transcript_list.find_manually_created_transcript,
            transcript_list.find_generated_transcript,
        ):
            try:
                transcript = finder(["en", "en-US", "en-GB"])
                break
            except Exception:
                continue

        if transcript is None:
            available = list(transcript_list)
            if not available:
                logger.warning(f"[YouTube] No transcripts listed for {video_id}")
                return None
            transcript = available[0]
            logger.info(f"[YouTube] Falling back to '{transcript.language_code}' transcript")

        fetched = transcript.fetch()
        full_text = "\n".join(snippet.text for snippet in fetched)

        if full_text.strip():
            logger.info(
                f"[YouTube] Transcript fetched for {video_id} ({len(full_text)} chars)"
            )
            return full_text
        return None
    except Exception as e:
        logger.warning(
            f"[YouTube] No transcript available for {video_id}: "
            f"{type(e).__name__}: {e}"
        )
        return None


async def download_video_locally(url: str, video_id: str) -> Optional[str]:
    """Download the best quality MP4 of a YouTube video to vault/videos/."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yt_dlp_download, url, video_id)


def _yt_dlp_download(url: str, video_id: str) -> Optional[str]:
    """Synchronous wrapper for yt-dlp video download."""
    output_template = str(VIDEOS_DIR / "%(id)s_%(title).100s.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # prepare_filename reports the pre-merge extension; after a merge to
            # mp4 the real file on disk is the .mp4 sibling.
            if filepath and not Path(filepath).exists():
                merged = Path(filepath).with_suffix(".mp4")
                filepath = str(merged) if merged.exists() else None
            if filepath:
                logger.info(f"[YouTube] Video saved to {filepath}")
                return filepath
            return None
    except Exception as e:
        logger.error(f"[YouTube] Video download failed for {video_id}: {e}")
        return None


# ──────────────────────────────────────────────
# Provider-neutral enrichment
# ──────────────────────────────────────────────

ENRICHMENT_PROMPT = """You are a research librarian organizing someone's personal knowledge graph. Analyze this content and extract:

- 3-7 lowercase topical tags (single words or hyphenated phrases, no spaces)
- A one-sentence summary capturing the core idea in the author's voice
- 2-5 standalone insights — each should be readable on its own, without needing the original

Be concrete. Avoid filler like "discusses" or "talks about". If the content is thin, return fewer tags and insights rather than padding."""


async def verify_provider() -> bool:
    global provider_health
    try:
        provider_health = await intelligence_provider.check_health()
    except Exception as exc:
        provider_health = {
            "provider": intelligence_provider.name,
            "runtime_available": False,
            "embedding_ready": False,
            "enrichment_ready": False,
            "missing_models": [],
            "message": f"Provider unavailable: {type(exc).__name__}",
        }
    level = logger.info if provider_health.get("enrichment_ready") else logger.warning
    level(f"[Provider:{intelligence_provider.name}] {provider_health.get('message', 'Unavailable')}")
    return bool(provider_health.get("embedding_ready") and provider_health.get("enrichment_ready"))


async def enrich_with_llm(payload: IngestPayload, video_path: Optional[str] = None) -> dict:
    """Enrich through the active provider without coupling persistence to it."""
    if not intelligence_provider.enrichment_available:
        return _empty_enrichment(ok=False, reason=f"{intelligence_provider.name}_unavailable")

    async with llm_semaphore:
        try:
            content_for_analysis = payload.content or payload.title or payload.source_url
            if not content_for_analysis.strip():
                return _empty_enrichment(ok=False, reason="no_content")
            if len(content_for_analysis) > MAX_ENRICHMENT_CHARS:
                content_for_analysis = content_for_analysis[:MAX_ENRICHMENT_CHARS]
            result = await intelligence_provider.enrich(
                content_for_analysis,
                ENRICHMENT_PROMPT,
                EnrichmentResult,
                video_path=video_path,
            )
            return {
                "ok": True,
                "tags": result.tags,
                "summary": result.summary or None,
                "key_insights": result.key_insights,
            }
        except Exception as exc:
            logger.error(
                f"[Provider:{intelligence_provider.name}] Enrichment failed: "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )
            return _empty_enrichment(ok=False, reason=type(exc).__name__)


def _empty_enrichment(ok: bool = False, reason: str = "") -> dict:
    return {"ok": ok, "tags": [], "summary": None, "key_insights": [], "reason": reason}


# ──────────────────────────────────────────────
# Vault Reading
# ──────────────────────────────────────────────
#
# One file per entry means one entry per file — the parser here is simpler
# than the old multi-entry-per-file version it replaced, since there's no
# longer a need to find where one entry ends and the next begins.
#
# backfill.py has its own copy of this parsing shape, with extra fallback
# branches for repairing malformed frontmatter. That duplication is
# deliberate — this path serves live reads and should stay simple and fast;
# backfill's is a maintenance script that runs rarely and needs to be
# tolerant of exactly the damage this one has no reason to expect.

# Body content sits between the content header and the Context footer. Must
# not match "## Related", which — when present — comes after Context.
_CONTENT_BLOCK = re.compile(
    r"^## (?:Content|Transcript)\n\n(.*?)\n\n## Context\n", re.S | re.M
)


def _coerce_str(value) -> str:
    """YAML parses some scalars (dates, None) as non-strings; flatten them."""
    return "" if value is None else str(value)


def _coerce_list(value) -> list:
    return [str(v) for v in value if v] if isinstance(value, list) else []


def iter_entry_files():
    """Every entry file across all type folders, as an iterator of Paths."""
    for folder_name in sorted(set(TYPE_FOLDERS.values())):
        folder = VAULT_DIR / folder_name
        if folder.exists():
            yield from sorted(folder.glob("*.md"))


def parse_entry_file(path: Path) -> Optional[dict]:
    """
    Parse one entry file into a dict. Returns None for anything that isn't a
    valid entry — a stray non-FootNotes .md file dropped into the folder, or one
    truncated by a crash mid-write — rather than raising, since a single bad
    file shouldn't take down a whole vault listing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    if not text.startswith("---\n"):
        return None
    fence = text.find("\n---\n", 4)
    if fence == -1:
        return None

    try:
        meta = yaml.safe_load(text[4:fence]) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict) or not meta.get("id"):
        return None

    body = text[fence + 5:]
    match = _CONTENT_BLOCK.search(body)
    content = match.group(1).strip() if match else ""

    return {
        "id": _coerce_str(meta.get("id")),
        "type": _coerce_str(meta.get("type")),
        "source_url": _coerce_str(meta.get("source_url")),
        "source_platform": _coerce_str(meta.get("source_platform")),
        "author": _coerce_str(meta.get("author")),
        "author_handle": _coerce_str(meta.get("author_handle")),
        "title": _coerce_str(meta.get("title")),
        "captured_at": _coerce_str(meta.get("captured_at")),
        "published_at": _coerce_str(meta.get("published_at")) or None,
        # Legacy Markdown remains compatible: expose a stable hash without
        # rewriting the user's file merely to add newer provenance metadata.
        "content_hash": (
            _coerce_str(meta.get("content_hash")) or captured_content_hash(content)
        ),
        "user_note": _coerce_str(meta.get("user_note")),
        "tags": _coerce_list(meta.get("tags")),
        "summary": meta.get("summary") or None,
        "key_insights": _coerce_list(meta.get("key_insights")),
        "status": _coerce_str(meta.get("status")) or "ingested",
        "content": content,
        "vault_file": path.name,
        "vault_path": str(path.relative_to(VAULT_DIR)),
    }


def load_vault_entries() -> list[dict]:
    """Every valid entry across the vault."""
    entries = []
    for path in iter_entry_files():
        parsed = parse_entry_file(path)
        if parsed:
            entries.append(parsed)
    return entries


# ──────────────────────────────────────────────
# Local Embedding Index
# ──────────────────────────────────────────────

MAX_EMBEDDING_CHARS = 50_000


def embedding_document(entry: dict) -> str:
    """Build the provider-neutral text representation of one memory."""
    parts = [
        f"User note: {entry.get('user_note', '')}",
        f"Title: {entry.get('title', '')}",
        f"Author: {entry.get('author', '')} {entry.get('author_handle', '')}",
        f"Source: {entry.get('source_platform', '')} {entry.get('source_url', '')}",
        f"Tags: {' '.join(entry.get('tags') or [])}",
        f"Summary: {entry.get('summary') or ''}",
        "Insights: " + " ".join(entry.get("key_insights") or []),
        f"Content: {entry.get('content', '')}",
    ]
    return "\n".join(parts).strip()


def canonical_embedding_input(entry: dict) -> str:
    """Exactly the canonical provider input, including its size bound."""
    return embedding_document(entry)[:MAX_EMBEDDING_CHARS]


def embedding_content_hash(entry: dict) -> str:
    """Hash only the canonical text actually sent to the provider."""
    document = canonical_embedding_input(entry)
    prepare = getattr(embedding_provider, "prepare_document", None)
    if callable(prepare):
        document = prepare(document, entry.get("title", ""))
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _embedding_row(entry_id: str) -> Optional[tuple]:
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    row = conn.execute(
        "SELECT provider, model, dimensions, content_hash, vector_json, status, error "
        "FROM memory_embeddings WHERE entry_id = ? AND provider = ? AND model = ?",
        (entry_id, embedding_provider.name, embedding_provider.model),
    ).fetchone()
    conn.close()
    return row


def _save_embedding_state(
    entry_id: str,
    content_hash: str,
    status: str,
    vector: Optional[list[float]] = None,
    error: Optional[str] = None,
) -> None:
    init_ingest_log()
    now = datetime.now(timezone.utc).isoformat()
    vector_json = json.dumps(vector, separators=(",", ":")) if vector else None
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    conn.execute(
        """
        INSERT INTO memory_embeddings
            (entry_id, provider, model, dimensions, content_hash, vector_json,
             status, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id, provider, model) DO UPDATE SET
            dimensions = excluded.dimensions,
            content_hash = excluded.content_hash,
            vector_json = excluded.vector_json,
            status = excluded.status,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            entry_id,
            embedding_provider.name,
            embedding_provider.model,
            len(vector) if vector else embedding_provider.dimensions,
            content_hash,
            vector_json,
            status,
            error,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    resurface_cache.clear()


async def ensure_entry_embedding(
    entry: dict, force: bool = False, _superseded_attempts: int = 0
) -> str:
    """
    Ensure one memory has a current vector. Safe and idempotent.

    Failure is recorded for resumable backfill and never mutates the Markdown
    memory. A ready vector is reused only when provider, model, and content
    hash all still match.
    """
    content_hash = embedding_content_hash(entry)
    existing = _embedding_row(entry["id"])
    if (
        not force
        and existing
        and existing[0] == embedding_provider.name
        and existing[1] == embedding_provider.model
        and existing[2] == embedding_provider.dimensions
        and existing[3] == content_hash
        and existing[5] == "ready"
        and existing[4]
    ):
        return "unchanged"

    try:
        document = canonical_embedding_input(entry)
        prepare = getattr(embedding_provider, "prepare_document", None)
        if callable(prepare):
            document = prepare(document, entry.get("title", ""))
        vector = await embedding_provider.embed_document(
            document, title=entry.get("title", "")
        )
        if not vector or any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("invalid embedding vector")

        # The user may edit their note while a provider request is in flight.
        # Never let that older result overwrite the newer canonical state.
        current = next(
            (candidate for candidate in load_vault_entries() if candidate["id"] == entry["id"]),
            None,
        )
        if current and embedding_content_hash(current) != content_hash:
            logger.info(f"[Embedding] Discarding superseded vector for {entry['id']}")
            if _superseded_attempts >= 2:
                return "stale"
            return await ensure_entry_embedding(
                current,
                force=force,
                _superseded_attempts=_superseded_attempts + 1,
            )
        _save_embedding_state(entry["id"], content_hash, "ready", vector=vector)
        logger.info(f"[Embedding] Indexed {entry['id']} ({len(vector)} dimensions)")
        return "embedded"
    except Exception as exc:
        current = next(
            (candidate for candidate in load_vault_entries() if candidate["id"] == entry["id"]),
            None,
        )
        if current and embedding_content_hash(current) != content_hash:
            logger.info(f"[Embedding] Discarding superseded failure for {entry['id']}")
            if _superseded_attempts >= 2:
                return "stale"
            return await ensure_entry_embedding(
                current,
                force=force,
                _superseded_attempts=_superseded_attempts + 1,
            )
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        _save_embedding_state(entry["id"], content_hash, "failed", error=error)
        logger.warning(f"[Embedding] Deferred {entry['id']}: {error}")
        return "failed"


def embedding_state(entry: dict) -> str:
    """Return ready, missing, stale, or failed for the active provider/model."""
    row = _embedding_row(entry["id"])
    if not row:
        return "missing"
    if row[2] != embedding_provider.dimensions or row[3] != embedding_content_hash(entry):
        return "stale"
    if row[5] != "ready" or not row[4]:
        return "failed"
    return "ready"


def load_embedding_vectors(entry_ids: set[str]) -> dict[str, list[float]]:
    """Load ready vectors for a candidate set; malformed rows are ignored."""
    if not entry_ids:
        return {}
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    placeholders = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"SELECT entry_id, vector_json FROM memory_embeddings "
        f"WHERE status = 'ready' AND provider = ? AND model = ? AND dimensions = ? "
        f"AND entry_id IN ({placeholders})",
        (
            embedding_provider.name,
            embedding_provider.model,
            embedding_provider.dimensions,
            *tuple(entry_ids),
        ),
    ).fetchall()
    conn.close()
    vectors = {}
    for entry_id, raw in rows:
        try:
            vector = [float(value) for value in json.loads(raw)]
            if vector:
                vectors[entry_id] = vector
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return vectors


def load_current_embedding_vectors(entries: list[dict]) -> dict[str, list[float]]:
    """Load only vectors whose stored hash matches current canonical Markdown."""
    if not entries:
        return {}
    entries_by_id = {entry["id"]: entry for entry in entries}
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    placeholders = ",".join("?" for _ in entries_by_id)
    rows = conn.execute(
        f"SELECT entry_id, content_hash, vector_json FROM memory_embeddings "
        f"WHERE status = 'ready' AND provider = ? AND model = ? AND dimensions = ? "
        f"AND entry_id IN ({placeholders})",
        (
            embedding_provider.name,
            embedding_provider.model,
            embedding_provider.dimensions,
            *tuple(entries_by_id),
        ),
    ).fetchall()
    conn.close()

    vectors = {}
    for entry_id, content_hash, raw in rows:
        entry = entries_by_id[entry_id]
        if content_hash != embedding_content_hash(entry):
            continue
        try:
            vector = [float(value) for value in json.loads(raw)]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            len(vector) == embedding_provider.dimensions
            and all(math.isfinite(value) for value in vector)
        ):
            vectors[entry_id] = vector
    return vectors


def embedding_stats() -> dict[str, int]:
    init_ingest_log()
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM memory_embeddings "
        "WHERE provider = ? AND model = ? AND dimensions = ? GROUP BY status",
        (
            embedding_provider.name,
            embedding_provider.model,
            embedding_provider.dimensions,
        ),
    ).fetchall())
    conn.close()
    return {"ready": counts.get("ready", 0), "failed": counts.get("failed", 0)}


def embedding_progress() -> dict:
    """Inspect canonical memories, including rows absent from the derived DB."""
    counts = {"ready": 0, "missing": 0, "stale": 0, "failed": 0}
    for entry in load_vault_entries():
        state = embedding_state(entry)
        counts[state] = counts.get(state, 0) + 1
    return {
        **counts,
        "total": sum(counts.values()),
        "pending": counts["missing"] + counts["stale"] + counts["failed"],
        **embedding_reconciliation,
    }


async def reconcile_embedding_batch(limit: int = 1) -> dict[str, int]:
    """Repair a deliberately tiny resumable batch of derived vectors."""
    outcomes = {"embedded": 0, "unchanged": 0, "failed": 0, "stale": 0}
    if limit <= 0 or not embedding_provider.available:
        return outcomes
    candidates = [
        entry for entry in load_vault_entries() if embedding_state(entry) != "ready"
    ][:limit]
    for entry in candidates:
        outcome = await ensure_entry_embedding(entry)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


async def embedding_reconciliation_worker() -> None:
    """Non-blocking, rate-shaped upgrade repair; never delays service startup."""
    session_limit = max(0, int(os.getenv("FOOTNOTES_BACKGROUND_BACKFILL_LIMIT", "5")))
    interval = max(5.0, float(os.getenv("FOOTNOTES_BACKGROUND_BACKFILL_INTERVAL", "30")))
    if not PRODUCT_MODE or session_limit == 0:
        return
    await asyncio.sleep(min(interval, 10.0))
    embedding_reconciliation["running"] = True
    try:
        while embedding_reconciliation["processed_this_session"] < session_limit:
            if not embedding_provider.available:
                await verify_provider()
                embedding_reconciliation["last_result"] = "provider unavailable"
                await asyncio.sleep(interval)
                continue
            outcomes = await reconcile_embedding_batch(limit=1)
            processed = sum(outcomes.values())
            if not processed:
                embedding_reconciliation["last_result"] = "current"
                return
            embedding_reconciliation["processed_this_session"] += processed
            embedding_reconciliation["last_result"] = next(
                (name for name, count in outcomes.items() if count), "idle"
            )
            await asyncio.sleep(interval)
    finally:
        embedding_reconciliation["running"] = False


# ──────────────────────────────────────────────
# Hybrid Recall
# ──────────────────────────────────────────────

_SEARCH_TOKEN = re.compile(r"[\w][\w'-]*", re.UNICODE)
_SEARCH_STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by", "do",
    "does", "for", "from", "had", "has", "have", "he", "her", "his", "how",
    "i", "in", "is", "it", "its", "me", "my", "not", "of", "on", "or",
    "our", "she", "so", "than", "that", "the", "their", "them", "they",
    "this", "to", "was", "we", "were", "what", "when", "who", "why",
    "will", "with", "would", "you", "your", "something", "thing", "tweet",
    "article",
}


def _search_tokens(text: str) -> list[str]:
    tokens = [token.lower() for token in _SEARCH_TOKEN.findall(text or "")]
    useful = [token for token in tokens if token not in _SEARCH_STOPWORDS]
    return useful or tokens


def _field_token_coverage(query_tokens: list[str], text: str) -> tuple[float, list[str]]:
    haystack = set(_search_tokens(text))
    hits = [token for token in query_tokens if token in haystack]
    return (len(set(hits)) / max(len(set(query_tokens)), 1), sorted(set(hits)))


def lexical_relevance(entry: dict, query: str) -> tuple[float, list[str], list[str]]:
    """Transparent field-weighted lexical score in the 0..1 range."""
    needle = " ".join(query.lower().split())
    tokens = _search_tokens(query)
    fields = {
        "user note": entry.get("user_note", ""),
        "title": entry.get("title", ""),
        "tags": " ".join(entry.get("tags") or []),
        "author": f"{entry.get('author', '')} {entry.get('author_handle', '')}",
        "summary": entry.get("summary") or "",
        "insights": " ".join(entry.get("key_insights") or []),
        "source": f"{entry.get('source_platform', '')} {entry.get('source_url', '')}",
        "content": entry.get("content", ""),
    }
    weights = {
        "user note": 0.78, "title": 0.62, "tags": 0.40, "author": 0.32, "summary": 0.34,
        "insights": 0.28, "source": 0.20, "content": 0.30,
    }
    score = 0.0
    reasons = []
    matched_tokens = set()
    for name, value in fields.items():
        normalized = " ".join(value.lower().split())
        coverage, hits = _field_token_coverage(tokens, value)
        matched_tokens.update(hits)
        contribution = weights[name] * coverage
        if needle and needle in normalized:
            contribution += 0.62 if name == "user note" else (0.55 if name == "title" else 0.24)
            reasons.append(f"exact phrase in {name}")
        elif coverage:
            reasons.append(f"keywords in {name}: {', '.join(hits[:4])}")
        score += contribution
    return min(score, 1.0), reasons, sorted(matched_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def useful_excerpt(entry: dict, query_tokens: list[str], width: int = 280) -> str:
    """Choose a cheap query-aware excerpt, falling back to summary/body start."""
    content = " ".join((entry.get("content") or "").split())
    lowered = content.lower()
    positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
    if positions:
        start = max(0, min(positions) - width // 3)
        if start:
            start = content.find(" ", start) + 1
        excerpt = content[start:start + width]
        if start:
            excerpt = "…" + excerpt
        if start + width < len(content):
            excerpt += "…"
        return excerpt
    fallback = " ".join((entry.get("summary") or content).split())
    return fallback[:width] + ("…" if len(fallback) > width else "")


async def hybrid_recall(entries: list[dict], query: str) -> list[dict]:
    """Rank candidates with lexical evidence plus optional semantic similarity."""
    query_vector = None
    if embedding_provider.available:
        try:
            query_vector = await embedding_provider.embed_query(query)
        except Exception as exc:
            logger.warning(
                f"[Recall] Semantic query unavailable; using lexical search: "
                f"{type(exc).__name__}: {str(exc)[:160]}"
            )

    vectors = load_embedding_vectors({entry["id"] for entry in entries}) if query_vector else {}
    query_tokens = _search_tokens(query)
    ranked = []
    for entry in entries:
        lexical, reasons, matched_tokens = lexical_relevance(entry, query)
        semantic = cosine_similarity(query_vector, vectors.get(entry["id"], [])) if query_vector else 0.0
        semantic = max(0.0, semantic)
        score = min(1.0, 0.72 * lexical + 0.42 * semantic)
        if score <= 0:
            continue
        if semantic >= 0.55 and not reasons:
            reasons.append("meaning is similar to the query")
        elif semantic >= 0.55:
            reasons.append("semantic similarity reinforces the text match")

        result = dict(entry)
        result["excerpt"] = useful_excerpt(entry, query_tokens)
        result["relevance"] = {
            "score": round(score, 4),
            "lexical": round(lexical, 4),
            "semantic": round(semantic, 4) if query_vector else None,
            "matched_terms": matched_tokens,
            "reasons": reasons[:4],
        }
        ranked.append(result)

    ranked.sort(
        key=lambda item: (item["relevance"]["score"], item.get("captured_at", "")),
        reverse=True,
    )
    return ranked


# ──────────────────────────────────────────────
# Auto-Linking
# ──────────────────────────────────────────────
#
# Obsidian's graph view draws an edge for every [[wikilink]] between two
# files. Without links, one-file-per-entry is graph-view input with nothing
# to graph — a wall of disconnected dots. These tag links are an Obsidian
# convenience, not the canonical relationship store. Runtime Related below
# uses rebuildable local embeddings and never materializes semantic edges in
# Markdown. Tag linking still degrades gracefully: an entry with no tags
# simply has no wikilinks yet and can pick them up after enrichment.
#
# Only one direction needs writing. Obsidian's own backlinks panel surfaces
# the reverse automatically for any note that's linked TO, and the graph
# draws the edge regardless of which file's frontmatter caused it.

MAX_RELATED = 5
MIN_SHARED_TAGS = 1
SEMANTIC_RELATED_DEFAULT_LIMIT = 3
SEMANTIC_RELATED_MAX_LIMIT = 5
SEMANTIC_RELATED_MIN_SIMILARITY = 0.72
SEMANTIC_RELATED_TAG_BOOST = 0.03
SEMANTIC_RELATED_MAX_TAG_BOOST = 0.06
RESURFACE_DEFAULT_LIMIT = 3
RESURFACE_MAX_LIMIT = 3
RESURFACE_MIN_SIMILARITY = 0.84
RESURFACE_MAX_LEXICAL_BOOST = 0.04
RESURFACE_CACHE_TTL_SECONDS = 30 * 60
RESURFACE_FAILURE_CACHE_TTL_SECONDS = 5 * 60
RESURFACE_CACHE_MAX_ENTRIES = 128

# format_bookmark_entry() always emits Related, if present, as the last
# section — so rewriting it is just "cut everything from this marker
# onward, re-append." No document-structure parsing required.
_RELATED_MARKER = "\n\n## Related\n\n"


def find_related(tags: list[str], exclude_id: str, entries: list[dict] = None) -> list[tuple]:
    """
    Rank other entries by shared-tag count and return the top matches as
    (wikilink_stem, title) pairs. Pass a pre-loaded `entries` list to avoid
    re-scanning the whole vault when linking many entries in one pass.
    """
    if not tags:
        return []
    wanted = {t.lower() for t in tags}
    if entries is None:
        entries = load_vault_entries()

    scored = []
    for entry in entries:
        if entry["id"] == exclude_id:
            continue
        overlap = len(wanted & {t.lower() for t in entry["tags"]})
        if overlap >= MIN_SHARED_TAGS:
            stem = Path(entry["vault_path"]).stem
            scored.append((overlap, stem, entry["title"] or stem))

    scored.sort(key=lambda t: -t[0])
    return [(stem, title) for _, stem, title in scored[:MAX_RELATED]]


def _shared_tags(left: dict, right: dict) -> list[str]:
    left_tags = {tag.lower(): tag for tag in left.get("tags") or []}
    right_tags = {tag.lower() for tag in right.get("tags") or []}
    return sorted(left_tags[tag] for tag in left_tags.keys() & right_tags)


def _is_obvious_duplicate(source: dict, candidate: dict) -> bool:
    source_url = normalize_url(source.get("source_url", ""))
    candidate_url = normalize_url(candidate.get("source_url", ""))
    if source_url and source_url == candidate_url:
        return True

    source_title = " ".join((source.get("title") or "").lower().split())
    candidate_title = " ".join((candidate.get("title") or "").lower().split())
    source_content = " ".join((source.get("content") or "").lower().split())
    candidate_content = " ".join((candidate.get("content") or "").lower().split())
    return bool(
        source_title
        and source_content
        and source_title == candidate_title
        and source_content == candidate_content
    )


def related_memories(
    entries: list[dict], entry_id: str, limit: int = SEMANTIC_RELATED_DEFAULT_LIMIT
) -> list[dict]:
    """Return a small, high-confidence semantic neighborhood for one memory."""
    source = next((entry for entry in entries if entry["id"] == entry_id), None)
    if source is None:
        return []

    limit = max(1, min(limit, SEMANTIC_RELATED_MAX_LIMIT))
    candidates = [
        entry
        for entry in entries
        if entry["id"] != entry_id and not _is_obvious_duplicate(source, entry)
    ]
    current_vectors = load_current_embedding_vectors([source, *candidates])
    source_vector = current_vectors.get(entry_id)

    ranked = []
    candidates_without_vectors = []
    for candidate in candidates:
        candidate_vector = current_vectors.get(candidate["id"])
        if not source_vector or not candidate_vector:
            candidates_without_vectors.append(candidate)
            continue

        semantic = max(0.0, cosine_similarity(source_vector, candidate_vector))
        if semantic < SEMANTIC_RELATED_MIN_SIMILARITY:
            continue
        shared_tags = _shared_tags(source, candidate)
        tag_boost = min(
            len(shared_tags) * SEMANTIC_RELATED_TAG_BOOST,
            SEMANTIC_RELATED_MAX_TAG_BOOST,
        )
        ranked.append((semantic + tag_boost, semantic, candidate, shared_tags, tag_boost))

    ranked.sort(
        key=lambda item: (item[0], item[1], item[2].get("captured_at", "")),
        reverse=True,
    )
    results = []
    used_ids = set()
    for score, semantic, candidate, shared_tags, tag_boost in ranked[:limit]:
        result = dict(candidate)
        result["excerpt"] = useful_excerpt(candidate, [], width=180)
        result["relationship"] = {
            "method": "semantic",
            "score": round(min(score, 1.0), 4),
            "semantic": round(semantic, 4),
            "tag_boost": round(tag_boost, 4),
            "shared_tags": shared_tags,
            "minimum_semantic": SEMANTIC_RELATED_MIN_SIMILARITY,
        }
        result.pop("content", None)
        results.append(result)
        used_ids.add(candidate["id"])

    # No provider call is needed here. When a current vector is absent, reuse
    # the established tag relationship for only that gap. A candidate with a
    # current but weak vector is deliberately not rescued by tags.
    if len(results) < limit:
        fallback_pool = candidates if not source_vector else candidates_without_vectors
        fallback = []
        for candidate in fallback_pool:
            if candidate["id"] in used_ids:
                continue
            shared_tags = _shared_tags(source, candidate)
            if len(shared_tags) < MIN_SHARED_TAGS:
                continue
            fallback.append((len(shared_tags), candidate, shared_tags))
        fallback.sort(
            key=lambda item: (item[0], item[1].get("captured_at", "")), reverse=True
        )
        for _, candidate, shared_tags in fallback[: limit - len(results)]:
            result = dict(candidate)
            result["excerpt"] = useful_excerpt(candidate, [], width=180)
            result["relationship"] = {
                "method": "tag_fallback",
                "score": None,
                "semantic": None,
                "tag_boost": None,
                "shared_tags": shared_tags,
                "minimum_semantic": SEMANTIC_RELATED_MIN_SIMILARITY,
            }
            result.pop("content", None)
            results.append(result)

    return results


def canonical_page_context(page: PageContext) -> str:
    """The bounded, temporary text sent to the query-embedding provider."""
    try:
        domain = urlsplit(page.url).hostname or ""
    except ValueError:
        domain = ""
    return "\n".join(
        (
            f"Title: {' '.join(page.title.split())}",
            f"Domain: {domain}",
            f"Description: {' '.join(page.description.split())}",
            f"Visible page text: {' '.join(page.text.split())}",
        )
    ).strip()


def _contextual_lexical_evidence(page: PageContext, entry: dict) -> tuple[float, list[str]]:
    page_heading = f"{page.title} {page.description}"
    page_tokens = set(_search_tokens(page_heading))
    memory_fields = {
        "personal thought": entry.get("user_note", ""),
        "title": entry.get("title", ""),
        "tags": " ".join(entry.get("tags") or []),
    }
    matched = set()
    for value in memory_fields.values():
        matched.update(page_tokens & set(_search_tokens(value)))
    if len(matched) < 2:
        return 0.0, []
    return min(len(matched) * 0.01, RESURFACE_MAX_LEXICAL_BOOST), sorted(matched)


def _strict_contextual_lexical_matches(
    page: PageContext, entries: list[dict], limit: int
) -> list[dict]:
    """Provider-free fallback limited to long exact title/note phrases."""
    haystack = " ".join(
        f"{page.title} {page.description} {page.text}".lower().split()
    )
    matches = []
    for entry in entries:
        if normalize_url(page.url) == normalize_url(entry.get("source_url", "")):
            continue
        evidence = []
        title = " ".join((entry.get("title") or "").lower().split())
        note = " ".join((entry.get("user_note") or "").lower().split())
        if len(title) >= 20 and title in haystack:
            evidence.append("exact memory title on page")
        if len(note) >= 20 and note in haystack:
            evidence.append("exact personal thought on page")
        if evidence:
            matches.append((len(evidence), entry, evidence))
    matches.sort(
        key=lambda item: (item[0], item[1].get("captured_at", "")), reverse=True
    )
    return [
        _contextual_result(entry, "exact_lexical", None, 0.0, [], evidence)
        for _, entry, evidence in matches[:limit]
    ]


def _contextual_result(
    entry: dict,
    method: str,
    semantic: Optional[float],
    lexical_boost: float,
    matched_terms: list[str],
    reasons: list[str],
) -> dict:
    result = dict(entry)
    result["excerpt"] = useful_excerpt(entry, [], width=180)
    result["context_match"] = {
        "method": method,
        "semantic": round(semantic, 4) if semantic is not None else None,
        "lexical_boost": round(lexical_boost, 4),
        "score": round(min((semantic or 0.0) + lexical_boost, 1.0), 4),
        "minimum_semantic": RESURFACE_MIN_SIMILARITY,
        "matched_terms": matched_terms,
        "reasons": reasons,
    }
    result.pop("content", None)
    return result


async def contextual_resurface(
    page: PageContext, entries: list[dict], limit: int = RESURFACE_DEFAULT_LIMIT
) -> dict:
    """Match one ephemeral public-page context against durable memories."""
    limit = max(1, min(limit, RESURFACE_MAX_LIMIT))
    context = canonical_page_context(page)
    cache_key = hashlib.sha256(
        (
            f"{embedding_provider.name}\n{embedding_provider.model}\n"
            f"{embedding_provider.dimensions}\n{context}"
        ).encode("utf-8")
    ).hexdigest()
    now = time.monotonic()
    cached = resurface_cache.get(cache_key)
    if cached:
        age = now - cached[0]
        ttl = (
            RESURFACE_CACHE_TTL_SECONDS
            if cached[1].get("embedding_available")
            else RESURFACE_FAILURE_CACHE_TTL_SECONDS
        )
        if age < ttl:
            return dict(cached[1], cached=True)
        resurface_cache.pop(cache_key, None)

    page_url = normalize_url(page.url)
    candidates = [
        entry
        for entry in entries
        if not page_url or normalize_url(entry.get("source_url", "")) != page_url
    ]
    results = []
    embedding_available = embedding_provider.available
    if embedding_available and context:
        try:
            query_vector = await embedding_provider.embed_query(context)
            vectors = load_current_embedding_vectors(candidates)
            ranked = []
            for entry in candidates:
                vector = vectors.get(entry["id"])
                if not vector:
                    continue
                semantic = max(0.0, cosine_similarity(query_vector, vector))
                if semantic < RESURFACE_MIN_SIMILARITY:
                    continue
                lexical_boost, matched_terms = _contextual_lexical_evidence(page, entry)
                ranked.append(
                    (semantic + lexical_boost, semantic, entry, lexical_boost, matched_terms)
                )
            ranked.sort(
                key=lambda item: (item[0], item[1], item[2].get("captured_at", "")),
                reverse=True,
            )
            seen_urls = set()
            for _, semantic, entry, lexical_boost, matched_terms in ranked:
                identity = normalize_url(entry.get("source_url", "")) or entry["id"]
                if identity in seen_urls:
                    continue
                seen_urls.add(identity)
                results.append(
                    _contextual_result(
                        entry,
                        "semantic",
                        semantic,
                        lexical_boost,
                        matched_terms,
                        ["strong semantic match"],
                    )
                )
                if len(results) >= limit:
                    break
        except Exception:
            embedding_available = False

    if not results and not embedding_available:
        results = _strict_contextual_lexical_matches(page, candidates, limit)

    response = {
        "count": len(results),
        "entries": results,
        "embedding_available": embedding_available,
        "cached": False,
        "context_persisted": False,
    }
    resurface_cache[cache_key] = (now, response)
    if len(resurface_cache) > RESURFACE_CACHE_MAX_ENTRIES:
        oldest = min(resurface_cache, key=lambda key: resurface_cache[key][0])
        resurface_cache.pop(oldest, None)
    return response


def render_related_section(related: list[tuple]) -> str:
    """Render the trailing '## Related' block, or '' if there's nothing to link."""
    if not related:
        return ""
    lines = "\n".join(f"- [[{stem}|{title}]]" for stem, title in related)
    return f"{_RELATED_MARKER}{lines}"


def strip_related_section(text: str) -> str:
    """Remove a trailing Related block, if present. Idempotent."""
    idx = text.find(_RELATED_MARKER)
    return text[:idx] if idx != -1 else text


def rebuild_related_links() -> int:
    """
    Recompute every entry's Related section from current tags and rewrite it
    in place. Safe to run any time tags change underneath already-written
    files — a backfill run that enriches previously-empty entries, or the
    one-time migration into this layout. Returns the number of files changed.
    """
    entries = load_vault_entries()
    changed = 0

    for entry in entries:
        related = find_related(entry["tags"], entry["id"], entries=entries)
        path = VAULT_DIR / entry["vault_path"]
        text = path.read_text(encoding="utf-8")
        # Match format_bookmark_entry's invariant exactly: the file ends with
        # one newline whether or not a Related section follows.
        base = strip_related_section(text).rstrip("\n")
        new_text = base + render_related_section(related) + "\n"

        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed += 1

    return changed


# ──────────────────────────────────────────────
# Vault Writing
# ──────────────────────────────────────────────


async def write_entry_file(
    payload: IngestPayload,
    entry_id: str,
    tags: list = None,
    summary: Optional[str] = None,
    key_insights: list = None,
    status: str = "ingested",
    content_header: str = "Content",
) -> Path:
    """
    Render one entry, link it to whatever related entries currently exist,
    and write it to its own new file.

    Older files are never rewritten here — only appended-to-the-vault-count.
    Their links stay whatever they were computed as at their own write time
    until something calls rebuild_related_links() (a backfill run, or the
    one-time migration). That's a deliberate asymmetry, not a bug: it keeps
    every live ingest a single small write instead of a full-vault rescan.
    """
    async with vault_write_lock:
        related = find_related(tags or [], entry_id)
        text = format_bookmark_entry(
            payload=payload, entry_id=entry_id, tags=tags, summary=summary,
            key_insights=key_insights, status=status, content_header=content_header,
            related=related,
        )
        path = entry_path(payload, entry_id)
        async with aiofiles.open(str(path), mode="w", encoding="utf-8") as f:
            await f.write(text)
    return path


async def persist_user_note_to_markdown(
    entry_id: str, user_note: str
) -> Optional[dict]:
    """Update only user_note; preserve every unrelated Markdown byte."""
    async with vault_write_lock:
        target = None
        for path in iter_entry_files():
            parsed = parse_entry_file(path)
            if parsed and parsed["id"] == entry_id:
                target = path
                break
        if target is None:
            return None

        text = target.read_text(encoding="utf-8")
        fence = text.find("\n---\n", 4)
        if fence == -1:
            return None
        try:
            metadata = yaml.safe_load(text[4:fence]) or {}
        except yaml.YAMLError:
            return None
        if not isinstance(metadata, dict):
            return None

        frontmatter = text[4:fence + 1]
        note_line = yaml.safe_dump(
            {"user_note": user_note or None},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=10_000,
        ).rstrip("\n")
        existing_note = re.search(r"(?m)^user_note\s*:", frontmatter)
        if existing_note:
            next_key = re.search(
                r"(?m)^[A-Za-z_][A-Za-z0-9_-]*\s*:",
                frontmatter[existing_note.end():],
            )
            end = (
                existing_note.end() + next_key.start()
                if next_key
                else len(frontmatter)
            )
            updated_frontmatter = (
                frontmatter[:existing_note.start()]
                + note_line
                + "\n"
                + frontmatter[end:]
            )
        else:
            insert_before = re.search(r"(?m)^tags\s*:", frontmatter)
            position = insert_before.start() if insert_before else len(frontmatter)
            updated_frontmatter = (
                frontmatter[:position]
                + note_line
                + "\n"
                + frontmatter[position:]
            )

        updated = f"---\n{updated_frontmatter}---\n{text[fence + 5:]}"
        temporary = target.with_name(f".{target.name}.footnotes-note.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, target)
        return parse_entry_file(target)


# ──────────────────────────────────────────────
# Background Worker
# ──────────────────────────────────────────────


async def process_payload(payload: IngestPayload, entry_id: str) -> str:
    """
    Run one payload all the way through: extract, enrich, write.

    Returns the final status string. Raises on unrecoverable errors so the
    caller can retry or record a failure.
    """
    # A note can arrive while this item is queued. SQLite bridges that brief
    # gap; Markdown remains canonical once the capture is written.
    pending_note = get_ingest_user_note(entry_id)
    if pending_note is not None:
        payload.user_note = pending_note

    # Trust the URL over the caller's declared type — see detect_type().
    payload.type = detect_type(payload.source_url, payload.type)
    payload.source_platform = detect_platform(payload.source_url) or payload.source_platform
    is_youtube = payload.type == "youtube"

    video_path = None

    # ── YouTube: transcript, metadata, optional video download ──
    if is_youtube:
        video_id = extract_video_id(payload.source_url)
        if video_id:
            logger.info(f"[Worker] YouTube video detected: {video_id}")

            metadata = await extract_youtube_metadata(payload.source_url)
            if metadata:
                # Overwrite placeholder titles like "YouTube" that the extension
                # captures before the SPA has set the real document title.
                if metadata.get("title") and is_placeholder_title(payload.title):
                    payload.title = metadata["title"]
                if metadata.get("channel") and not payload.author:
                    payload.author = metadata["channel"]
                    payload.author_handle = (
                        metadata.get("channel_id")
                        or metadata["channel"].lower().replace(" ", "_")
                    )
                if metadata.get("upload_date"):
                    payload.published_at = normalize_upload_date(metadata["upload_date"])
                logger.info(
                    f"[Worker] YouTube metadata: {payload.title} by {payload.author}"
                )

            if not payload.content.strip():
                transcript = await get_youtube_transcript(video_id)
                if transcript:
                    payload.content = transcript
                else:
                    description = (metadata or {}).get("description", "").strip()
                    if description:
                        # Better than nothing: the description usually carries
                        # enough signal for tags even when captions are off.
                        payload.content = f"[No transcript — video description]\n\n{description}"
                        logger.info("[Worker] Using video description as fallback content")
                    else:
                        payload.content = (
                            f"[No transcript available for this video]\n\n"
                            f"URL: {payload.source_url}"
                        )
                        logger.warning(f"[Worker] No transcript or description for {video_id}")

            if DOWNLOAD_VIDEOS:
                # Awaited, not fire-and-forget. The old code spawned this as a
                # task and then immediately globbed for the finished file, so
                # enrichment always lost the race and the multimodal path was
                # effectively dead code.
                logger.info(f"[Worker] Downloading video (FOOTNOTES_DOWNLOAD_VIDEOS=1)")
                video_path = await download_video_locally(payload.source_url, video_id)

    # ── Article: full-text extraction via trafilatura ──
    elif payload.type == "article" and not payload.content.strip():
        logger.info(f"[Worker] Article has no content. Extracting from {payload.source_url}")
        extracted = await extract_article_content(payload.source_url)

        if extracted:
            payload.content = extracted
            logger.info(f"[Worker] Article extraction succeeded: {len(extracted)} chars")
        elif payload.selection:
            payload.content = f"[User selection]\n\n{payload.selection}"
            logger.info(f"[Worker] Falling back to user selection: {len(payload.selection)} chars")
        else:
            payload.content = (
                f"[Failed to extract content from {payload.source_url}]\n\n"
                "The page could not be parsed. The URL has been saved for reference."
            )
            logger.warning(f"[Worker] Article extraction failed: {payload.source_url}")

    # ── Enrichment ──
    if is_placeholder_content(payload.content):
        logger.warning(
            f"[Worker] Nothing extractable, skipping enrichment: {payload.source_url}"
        )
        enrichment = _empty_enrichment(ok=False, reason="no_extractable_content")
    else:
        enrichment = await enrich_with_llm(payload, video_path=video_path)

    # Status now reflects what actually happened. "enriched" is a claim that
    # tags/summary/insights exist; anything else is "ingested" — captured and
    # saved, but not yet understood.
    status = "enriched" if enrichment.get("ok") else "ingested"
    if not enrichment.get("ok"):
        logger.warning(
            f"[Worker] Saving without enrichment "
            f"(reason: {enrichment.get('reason') or 'unknown'}): {payload.source_url}"
        )

    content_header = (
        "Transcript"
        if is_youtube and payload.content and not payload.content.startswith("[No transcript")
        else "Content"
    )

    target = await write_entry_file(
        payload=payload,
        entry_id=entry_id,
        tags=enrichment.get("tags", []),
        summary=enrichment.get("summary"),
        key_insights=enrichment.get("key_insights", []),
        status=status,
        content_header=content_header,
    )
    logger.info(
        f"[Worker] Saved to {target.relative_to(VAULT_DIR)} ({status}): "
        f"{payload.title or payload.source_url}"
    )

    # Close the race where a note arrived after the pre-processing check but
    # before the Markdown write completed.
    latest_note = get_ingest_user_note(entry_id)
    if latest_note is not None and latest_note != (payload.user_note or ""):
        written_with_note = await persist_user_note_to_markdown(entry_id, latest_note)
        if written_with_note:
            payload.user_note = latest_note

    # Markdown is the durable capture. Embedding is deliberately attempted
    # only after that write succeeds, and ensure_entry_embedding contains its
    # own failure handling, so a provider outage cannot reject the memory.
    written_entry = parse_entry_file(target)
    if written_entry:
        await ensure_entry_embedding(written_entry)
    return status


async def background_worker():
    """
    Persistent background coroutine that processes the ingest queue.

    For each payload:
    1. Pop from queue
    2. Skip if this URL was already captured (dedup on normalized URL)
    3. Generate UUID and log as 'processing'
    4. Type-specific extraction, enrichment, vault write
    5. Retry transient failures up to MAX_ATTEMPTS, then save a stub so the
       URL is never lost
    """
    logger.info("[Worker] Background worker started.")

    while True:
        try:
            queued_item = await ingest_queue.get()
        except asyncio.CancelledError:
            raise

        try:
            if isinstance(queued_item, tuple):
                payload, entry_id = queued_item
            else:  # compatibility for maintenance/tests that enqueue a payload directly
                payload, entry_id = queued_item, str(uuid.uuid4())
            existing = find_existing_entry(payload.source_url)
            if existing and existing[0] != entry_id and existing[1] in ("enriched", "ingested"):
                logger.info(
                    f"[Worker] Skipping duplicate ({existing[1]}): {payload.source_url}"
                )
                continue

            # Resolve the real type before the first log write, so the row
            # never records the caller's guess (context menu sends "article"
            # for everything). process_payload re-derives it idempotently.
            payload.type = detect_type(payload.source_url, payload.type)

            logger.info(f"[Worker] Processing: {payload.source_url} (id={entry_id})")
            log_ingest_entry(entry_id, payload.source_url, payload.type, "processing")

            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    status = await process_payload(payload, entry_id)
                    log_ingest_entry(
                        entry_id, payload.source_url, payload.type, status, attempt - 1
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        f"[Worker] Attempt {attempt}/{MAX_ATTEMPTS} failed for "
                        f"{payload.source_url}: {type(e).__name__}: {e}"
                    )
                    if attempt < MAX_ATTEMPTS:
                        log_ingest_entry(
                            entry_id, payload.source_url, payload.type, "retrying", attempt
                        )
                        await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                        continue

                    # Out of attempts — save a stub so the URL isn't lost.
                    try:
                        pending_note = get_ingest_user_note(entry_id)
                        if pending_note is not None:
                            payload.user_note = pending_note
                        await write_entry_file(
                            payload=payload, entry_id=entry_id, status="failed"
                        )
                    except Exception as write_err:
                        logger.error(f"[Worker] Could not even write stub: {write_err}")
                    log_ingest_entry(
                        entry_id, payload.source_url, payload.type, "failed", attempt
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Worker] Unexpected error in worker loop: {e}")
        finally:
            ingest_queue.task_done()


# ──────────────────────────────────────────────
# FastAPI App + Lifespan
# ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initializes SQLite, checks the active provider, and spawns the
    background worker on startup. The worker runs until shutdown, when it's
    cancelled cleanly.
    """
    init_ingest_log()
    provider_task = asyncio.create_task(verify_provider())
    worker_task = asyncio.create_task(background_worker())
    reconciliation_task = asyncio.create_task(embedding_reconciliation_worker())
    logger.info("[Server] FootNotes V2 ingestion server started.")

    yield

    for task in (worker_task, reconciliation_task, provider_task):
        task.cancel()
    for task in (worker_task, reconciliation_task, provider_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("[Server] FootNotes V2 ingestion server stopped.")


app = FastAPI(title="FootNotes Ingestion Server", version="0.1.0", lifespan=lifespan)


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────


def onboarding_assets_dir() -> Path:
    bundled_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return bundled_root / "onboarding"


def require_local_setup_origin(request: Request) -> None:
    origin = request.headers.get("origin", "")
    allowed = {"http://localhost:8000", "http://127.0.0.1:8000"}
    if origin and origin not in allowed:
        raise HTTPException(status_code=403, detail="Local FootNotes setup only")


@app.get("/footnotes", include_in_schema=False)
async def footnotes_home():
    return FileResponse(onboarding_assets_dir() / "index.html")


@app.get("/footnotes/{asset_name}", include_in_schema=False)
async def footnotes_asset(asset_name: str):
    if asset_name not in {"app.js", "style.css"}:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(onboarding_assets_dir() / asset_name)


@app.get("/footnotes-api/setup")
async def setup_status():
    await verify_provider()
    config = load_config()
    progress = embedding_progress()
    return {
        "setup_complete": config.setup_complete,
        "vault_path": str(VAULT_DIR if PRODUCT_MODE else config.resolved_vault_path),
        "provider": intelligence_provider.name,
        "provider_available": bool(provider_health.get("runtime_available")),
        "provider_health": provider_health,
        "service": "running",
        "semantic_memory": progress,
        "resurfacing": "off by default; managed in the Chrome extension",
        "log_path": str(Path.home() / "Library" / "Logs" / "FootNotes" / "footnotes.log"),
    }


@app.post("/footnotes-api/choose-folder")
async def choose_storage_folder(request: Request):
    require_local_setup_origin(request)
    if sys.platform != "darwin":
        raise HTTPException(status_code=501, detail="Folder picker is currently available on macOS")
    script = 'POSIX path of (choose folder with prompt "Choose where your FootNotes memories live")'
    result = await asyncio.to_thread(
        subprocess.run,
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {"cancelled": True}
    return {"cancelled": False, "vault_path": result.stdout.strip().rstrip("/")}


@app.get("/footnotes-api/ollama-status")
async def ollama_setup_status():
    """Check the supported local runtime without changing active settings."""
    candidate = OllamaProvider(
        base_url=os.getenv("FOOTNOTES_OLLAMA_URL", "http://127.0.0.1:11434"),
        embedding_model=os.getenv("FOOTNOTES_OLLAMA_EMBEDDING_MODEL", "embeddinggemma"),
        enrichment_model=os.getenv("FOOTNOTES_OLLAMA_ENRICHMENT_MODEL", "qwen3:1.7b"),
        dimensions=OLLAMA_EMBEDDING_DIMENSIONS,
    )
    try:
        return await candidate.check_health()
    finally:
        await candidate.aclose()


@app.post("/footnotes-api/setup")
async def save_setup(update: SetupUpdate, request: Request):
    require_local_setup_origin(request)
    vault_path = Path(update.vault_path).expanduser()
    if not vault_path.is_absolute():
        raise HTTPException(status_code=400, detail="Choose an absolute storage location")
    try:
        vault_path.mkdir(parents=True, exist_ok=True)
        if not vault_path.is_dir():
            raise OSError("not a folder")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"FootNotes cannot use that folder: {exc}") from exc

    config = FootNotesConfig(
        setup_complete=True,
        vault_path=str(vault_path.resolve()),
        provider=update.provider,
    )
    save_config(config)
    configure_runtime(config.resolved_vault_path, config.provider)
    await verify_provider()
    return {
        "status": "saved",
        "provider_available": bool(provider_health.get("runtime_available")),
        "provider_health": provider_health,
        "vault_path": str(VAULT_DIR),
        "message": (
            "FootNotes is ready. Your intelligence provider is connected."
            if provider_health.get("embedding_ready") and provider_health.get("enrichment_ready")
            else "FootNotes is ready. Capture and exact Recall work without semantic memory."
        ),
    }


@app.post("/footnotes-api/stop")
async def stop_footnotes(request: Request, background_tasks: BackgroundTasks):
    require_local_setup_origin(request)
    background_tasks.add_task(os.kill, os.getpid(), signal.SIGTERM)
    return {"status": "stopping"}


@app.post("/ingest", status_code=202)
async def ingest(payload: IngestPayload):
    """
    Accept a normalized bookmark payload and queue it for async processing.

    Returns 202 Accepted immediately — the user never waits for LLM enrichment
    or video download. Processing happens in the background worker.
    """
    existing = find_existing_entry(payload.source_url)
    if existing and existing[1] in ("queued", "processing", "retrying", "enriched", "ingested"):
        logger.info(f"[API] Duplicate, not queued: {payload.source_url}")
        existing_memory = next(
            (entry for entry in load_vault_entries() if entry["id"] == existing[0]),
            None,
        )
        return {
            "status": "duplicate",
            "existing_id": existing[0],
            "user_note": (
                (existing_memory or {}).get("user_note", "")
                or get_ingest_user_note(existing[0])
                or ""
            ),
            "queue_size": ingest_queue.qsize(),
        }

    entry_id = str(uuid.uuid4())
    payload.type = detect_type(payload.source_url, payload.type)
    log_ingest_entry(entry_id, payload.source_url, payload.type, "queued")
    await ingest_queue.put((payload, entry_id))
    logger.info(f"[API] Queued: {payload.source_url} (queue size: {ingest_queue.qsize()})")
    return {"status": "accepted", "entry_id": entry_id, "queue_size": ingest_queue.qsize()}


@app.get("/health")
async def health():
    """Health check endpoint."""
    entry_counts = {
        folder_name: len(list((VAULT_DIR / folder_name).glob("*.md")))
        for folder_name in sorted(set(TYPE_FOLDERS.values()))
        if (VAULT_DIR / folder_name).exists()
    }
    return {
        "status": "healthy",
        "service": "footnotes",
        "version": app.version,
        "queue_size": ingest_queue.qsize(),
        "provider_configured": intelligence_provider.name != "none",
        "intelligence_provider": intelligence_provider.name,
        "provider_health": provider_health,
        "embedding_provider": embedding_provider.name,
        "embedding_model": embedding_provider.model,
        "embedding_available": embedding_provider.available,
        "embeddings": embedding_progress(),
        "video_download_enabled": DOWNLOAD_VIDEOS,
        "vault_dir": str(VAULT_DIR),
        "entry_counts": entry_counts,
        "total_entries": sum(entry_counts.values()),
    }


@app.get("/status/{entry_id}")
async def get_status(entry_id: str):
    """Look up the processing status of a specific bookmark by UUID."""
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT id, source_url, type, status, retries, created_at, updated_at "
        "FROM ingest_log WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {
        "id": row[0],
        "source_url": row[1],
        "type": row[2],
        "status": row[3],
        "retries": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


@app.get("/stats")
async def stats():
    """Counts by status — the fastest way to see whether enrichment is working."""
    conn = sqlite3.connect(str(INGEST_LOG_DB))
    cursor = conn.cursor()
    by_status = dict(
        cursor.execute("SELECT status, COUNT(*) FROM ingest_log GROUP BY status").fetchall()
    )
    by_type = dict(
        cursor.execute("SELECT type, COUNT(*) FROM ingest_log GROUP BY type").fetchall()
    )
    total = cursor.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
    conn.close()

    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "embeddings": embedding_stats(),
    }


@app.get("/entries")
async def list_entries(
    type: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    List vault entries, newest first, with optional filters.

    Returns an excerpt rather than the full body — some transcripts run tens
    of thousands of characters, and a list view has no use for that. Fetch
    GET /entries/{id} for the complete entry.
    """
    entries = load_vault_entries()

    if type:
        entries = [e for e in entries if e["type"] == type]
    if status:
        entries = [e for e in entries if e["status"] == status]
    if tag:
        wanted = tag.lower()
        entries = [e for e in entries if wanted in (t.lower() for t in e["tags"])]
    if q:
        entries = await hybrid_recall(entries, q)
    else:
        entries.sort(key=lambda e: e["captured_at"], reverse=True)
    total = len(entries)
    page = entries[offset: offset + limit]

    for e in page:
        if "excerpt" not in e:
            e["excerpt"] = useful_excerpt(e, [])
        del e["content"]

    return {"total": total, "count": len(page), "entries": page}


@app.get("/search")
async def search_entries(
    q: str,
    type: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """Explicit Recall endpoint; `/entries?q=...` uses the same ranking."""
    result = await list_entries(
        type=type,
        status=status,
        tag=tag,
        q=q,
        limit=limit,
        offset=offset,
    )
    result["query"] = q
    return result


@app.post("/resurface")
async def resurface_page(page: PageContext, limit: int = RESURFACE_DEFAULT_LIMIT):
    """Match temporary page context without saving it as browsing history."""
    return await contextual_resurface(page, load_vault_entries(), limit=limit)


@app.get("/entries/{entry_id}")
async def get_entry(entry_id: str):
    """Fetch one entry in full, including its complete body."""
    for entry in load_vault_entries():
        if entry["id"] == entry_id:
            return entry
    raise HTTPException(status_code=404, detail="Entry not found")


@app.get("/entries/{entry_id}/related")
async def get_related_entries(
    entry_id: str, limit: int = SEMANTIC_RELATED_DEFAULT_LIMIT
):
    """Return derived semantic neighbors with safe shared-tag fallback."""
    entries = load_vault_entries()
    if not any(entry["id"] == entry_id for entry in entries):
        raise HTTPException(status_code=404, detail="Entry not found")
    related = related_memories(entries, entry_id, limit=limit)
    return {
        "entry_id": entry_id,
        "count": len(related),
        "entries": related,
    }


@app.patch("/entries/{entry_id}/note")
async def update_entry_note(
    entry_id: str, update: UserNoteUpdate, background_tasks: BackgroundTasks
):
    """Durably set or clear the user's own note without changing memory content."""
    note = " ".join(update.user_note.split()).strip()
    if not set_ingest_user_note(entry_id, note):
        existing = next(
            (entry for entry in load_vault_entries() if entry["id"] == entry_id),
            None,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        # A hand-copied/legacy Markdown memory may not yet have an ingest row.
        log_ingest_entry(
            entry_id,
            existing["source_url"],
            existing["type"],
            existing["status"],
        )
        set_ingest_user_note(entry_id, note)

    entry = await persist_user_note_to_markdown(entry_id, note)
    if entry is None:
        # The capture is still queued/processing. The worker reads this value
        # after its durable Markdown write and applies it before embedding.
        return {"status": "pending", "entry_id": entry_id, "user_note": note}

    # The note is already durable and lexically searchable. Refreshing its
    # derived vector happens after the HTTP response so the UI never waits on
    # an embedding provider; a stopped task remains detectable as stale.
    background_tasks.add_task(ensure_entry_embedding, entry)
    return {
        "status": "saved",
        "entry_id": entry_id,
        "user_note": note,
        "embedding": "scheduled",
    }
