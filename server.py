"""
server.py — Orbit V2 Processing Layer
FastAPI server with async queue, file locking, and background worker.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
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
from fastapi import FastAPI, HTTPException
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi

# Load environment variables from .env file
load_dotenv()

# ──────────────────────────────────────────────
# Gemini Client (google-genai SDK)
# ──────────────────────────────────────────────
# Uses GEMINI_API_KEY from env. The SDK is client-based, not module-level —
# one client instance is shared across all calls. Constructing the client does
# NOT validate the key; that only happens on the first request. See
# verify_gemini_credentials() below, which probes at startup so a bad key
# surfaces as a loud log line instead of silently empty enrichment forever.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Model selection by content type:
# - 2.5-flash for text (tweets, articles): fast, cheap, plenty for tag+summary extraction
# - 2.5-pro for video: the reasoning headroom is worth the cost when analyzing 10+ min of footage
TEXT_MODEL = "gemini-2.5-flash"
VIDEO_MODEL = "gemini-2.5-pro"

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

VAULT_DIR = Path(__file__).parent / "vault"
BOOKMARKS_FILE = VAULT_DIR / "bookmarks.md"
TRANSCRIPTS_FILE = VAULT_DIR / "transcripts.md"
INGEST_LOG_DB = VAULT_DIR / "ingest.log"

MAX_WORKER_CONCURRENCY = 3
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

# Downloading full MP4s is off by default: it costs hundreds of MB per video and
# only buys the multimodal path, which the transcript usually covers. Opt in with
# ORBIT_DOWNLOAD_VIDEOS=1. When enabled the download is awaited (see the worker),
# because enrichment cannot analyze a file that hasn't finished writing.
DOWNLOAD_VIDEOS = os.getenv("ORBIT_DOWNLOAD_VIDEOS", "").lower() in ("1", "true", "yes")

# Gemini's inline text limit is generous but not infinite, and a 3-hour podcast
# transcript is mostly redundant for tag extraction. Truncate before sending.
MAX_ENRICHMENT_CHARS = 100_000

VAULT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("orbit")

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


class EnrichmentResult(BaseModel):
    """
    Schema enforced on Gemini's response. Using response_schema with this Pydantic
    model means Gemini returns guaranteed-valid JSON matching this shape — no more
    regex-fishing for {...} in the response text.
    """
    tags: list[str] = Field(default_factory=list, description="3-7 lowercase topical tags")
    summary: str = Field(default="", description="One-sentence summary of the core idea")
    key_insights: list[str] = Field(default_factory=list, description="2-5 standalone insights")


# ──────────────────────────────────────────────
# Async resources
# ──────────────────────────────────────────────

# Async queue for non-blocking ingestion
ingest_queue: asyncio.Queue = asyncio.Queue()

# File locks for safe concurrent appends
file_lock = asyncio.Lock()
transcript_lock = asyncio.Lock()

# LLM concurrency semaphore — caps simultaneous Gemini calls
llm_semaphore = asyncio.Semaphore(MAX_WORKER_CONCURRENCY)

# Set by verify_gemini_credentials() at startup. When False we skip the API call
# entirely rather than burning a round-trip per bookmark to rediscover the same 400.
gemini_available = False


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

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_normalized_url ON ingest_log(normalized_url)"
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
) -> str:
    """
    Render a bookmark entry with YAML frontmatter and Markdown body.

    Frontmatter is serialized by PyYAML rather than f-string interpolation.
    Hand-rolled quoting broke on any title containing a backslash, a leading
    '@', or an embedded quote — and those failures were silent, producing a
    file that only revealed itself as corrupt when something tried to parse it.
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
        f"- **Platform:** {payload.source_platform}\n\n"
        "---\n\n"
    )


def _single_line(value: Optional[str]) -> str:
    """Collapse newlines — a title spanning lines breaks the frontmatter block."""
    if not value:
        return ""
    return " ".join(value.split())


# ──────────────────────────────────────────────
# Content Type Detection
# ──────────────────────────────────────────────


def detect_platform(url: str) -> str:
    """Classify a URL's origin. Mirrors extractPlatform() in background.js."""
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
# LLM Enrichment (google-genai SDK)
# ──────────────────────────────────────────────

ENRICHMENT_PROMPT = """You are a research librarian organizing someone's personal knowledge graph. Analyze this content and extract:

- 3-7 lowercase topical tags (single words or hyphenated phrases, no spaces)
- A one-sentence summary capturing the core idea in the author's voice
- 2-5 standalone insights — each should be readable on its own, without needing the original

Be concrete. Avoid filler like "discusses" or "talks about". If the content is thin, return fewer tags and insights rather than padding."""


async def verify_gemini_credentials() -> bool:
    """
    Probe the API once at startup so a bad key is loud instead of silent.

    Without this the first sign of trouble is a vault full of entries marked
    "enriched" with empty tags — which is exactly how the existing 15 entries
    got that way. Constructing genai.Client() never validates anything.
    """
    global gemini_available

    if not GEMINI_API_KEY:
        logger.error(
            "[Gemini] GEMINI_API_KEY is not set. Enrichment is DISABLED — "
            "bookmarks will still be captured and saved with status 'ingested'. "
            "Copy .env.example to .env and add a key from "
            "https://aistudio.google.com/apikey"
        )
        gemini_available = False
        return False

    try:
        await gemini_client.aio.models.generate_content(
            model=TEXT_MODEL,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=1000),
        )
        logger.info(f"[Gemini] Credentials verified — enrichment active ({TEXT_MODEL}).")
        gemini_available = True
        return True
    except Exception as e:
        logger.error(
            f"[Gemini] Credential check FAILED: {type(e).__name__}: {str(e)[:200]}"
        )
        logger.error(
            "[Gemini] Enrichment is DISABLED for this session. Bookmarks will be "
            "captured and saved with status 'ingested' so nothing is lost; fix the "
            "key and re-run backfill to enrich them."
        )
        gemini_available = False
        return False


async def enrich_with_llm(payload: IngestPayload, video_path: Optional[str] = None) -> dict:
    """
    Enrich content with Gemini, returning structured tags/summary/insights.

    Returns a dict with an "ok" flag so the caller can tell "the model returned
    nothing because the content was thin" from "the call failed". The old
    version collapsed both into an empty dict and then wrote status "enriched"
    either way.

    Uses response_schema to guarantee valid JSON output — no regex parsing,
    no markdown-fenced JSON to strip, no fallback-on-malformed-output paths.
    """
    if not gemini_available:
        return _empty_enrichment(ok=False, reason="gemini_unavailable")

    async with llm_semaphore:
        try:
            generation_config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EnrichmentResult,
                system_instruction=ENRICHMENT_PROMPT,
            )

            # ── Video path: upload the MP4 the worker already finished downloading ──
            if video_path and Path(video_path).exists():
                logger.info(f"[Gemini] Uploading local video: {Path(video_path).name}")
                uploaded = await gemini_client.aio.files.upload(file=video_path)

                # Poll until ACTIVE — Gemini processes video before it can be referenced
                while uploaded.state.name == "PROCESSING":
                    logger.info("[Gemini] Waiting for video processing…")
                    await asyncio.sleep(2)
                    uploaded = await gemini_client.aio.files.get(name=uploaded.name)

                if uploaded.state.name == "ACTIVE":
                    try:
                        response = await gemini_client.aio.models.generate_content(
                            model=VIDEO_MODEL,
                            contents=["Analyze this video for my knowledge graph.", uploaded],
                            config=generation_config,
                        )
                        return _parse_structured_response(response)
                    finally:
                        try:
                            await gemini_client.aio.files.delete(name=uploaded.name)
                        except Exception as cleanup_err:
                            logger.warning(f"[Gemini] File cleanup failed: {cleanup_err}")
                else:
                    logger.error(f"[Gemini] Video processing failed: {uploaded.state.name}")

            # ── Text path: tweet, article, or YouTube transcript ──
            content_for_analysis = payload.content or payload.title or payload.source_url
            if not content_for_analysis.strip():
                logger.warning("[Gemini] No content to analyze, returning empty enrichment")
                return _empty_enrichment(ok=False, reason="no_content")

            if len(content_for_analysis) > MAX_ENRICHMENT_CHARS:
                logger.info(
                    f"[Gemini] Truncating {len(content_for_analysis)} chars "
                    f"to {MAX_ENRICHMENT_CHARS} for enrichment"
                )
                content_for_analysis = content_for_analysis[:MAX_ENRICHMENT_CHARS]

            response = await gemini_client.aio.models.generate_content(
                model=TEXT_MODEL,
                contents=content_for_analysis,
                config=generation_config,
            )
            return _parse_structured_response(response)

        except Exception as e:
            logger.error(f"[Gemini] Enrichment failed: {type(e).__name__}: {str(e)[:200]}")
            return _empty_enrichment(ok=False, reason=type(e).__name__)


def _parse_structured_response(response) -> dict:
    """
    Parse a Gemini response that was constrained by response_schema.

    With response_schema set, response.parsed is the typed Pydantic instance.
    response.text is the raw JSON. We try parsed first, fall back to text.
    """
    try:
        if getattr(response, "parsed", None) is not None:
            result: EnrichmentResult = response.parsed
            logger.info(
                f"[Gemini] Enriched: {len(result.tags)} tags, "
                f"{len(result.key_insights)} insights"
            )
            return {
                "ok": True,
                "tags": result.tags,
                "summary": result.summary or None,
                "key_insights": result.key_insights,
            }

        data = json.loads(response.text)
        return {
            "ok": True,
            "tags": data.get("tags", []),
            "summary": data.get("summary") or None,
            "key_insights": data.get("key_insights", []),
        }
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        logger.error(f"[Gemini] Failed to parse structured response: {e}")
        return _empty_enrichment(ok=False, reason="unparseable_response")


def _empty_enrichment(ok: bool = False, reason: str = "") -> dict:
    return {"ok": ok, "tags": [], "summary": None, "key_insights": [], "reason": reason}


# ──────────────────────────────────────────────
# Vault Writing
# ──────────────────────────────────────────────


async def write_entry(entry: str, is_youtube: bool) -> Path:
    """Append a rendered entry to the correct vault file under its lock."""
    target, lock = (
        (TRANSCRIPTS_FILE, transcript_lock) if is_youtube else (BOOKMARKS_FILE, file_lock)
    )
    async with lock:
        async with aiofiles.open(str(target), mode="a", encoding="utf-8") as f:
            await f.write(entry)
    return target


# ──────────────────────────────────────────────
# Background Worker
# ──────────────────────────────────────────────


async def process_payload(payload: IngestPayload, entry_id: str) -> str:
    """
    Run one payload all the way through: extract, enrich, write.

    Returns the final status string. Raises on unrecoverable errors so the
    caller can retry or record a failure.
    """
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
                if metadata.get("title") and _single_line(payload.title).lower() in PLACEHOLDER_TITLES:
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
                logger.info(f"[Worker] Downloading video (ORBIT_DOWNLOAD_VIDEOS=1)")
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

    entry = format_bookmark_entry(
        payload=payload,
        entry_id=entry_id,
        tags=enrichment.get("tags", []),
        summary=enrichment.get("summary"),
        key_insights=enrichment.get("key_insights", []),
        status=status,
        content_header=content_header,
    )

    target = await write_entry(entry, is_youtube)
    logger.info(f"[Worker] Saved to {target.name} ({status}): {payload.title or payload.source_url}")
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
            payload: IngestPayload = await ingest_queue.get()
        except asyncio.CancelledError:
            raise

        try:
            existing = find_existing_entry(payload.source_url)
            if existing and existing[1] in ("enriched", "ingested"):
                logger.info(
                    f"[Worker] Skipping duplicate ({existing[1]}): {payload.source_url}"
                )
                continue

            # Resolve the real type before the first log write, so the row
            # never records the caller's guess (context menu sends "article"
            # for everything). process_payload re-derives it idempotently.
            payload.type = detect_type(payload.source_url, payload.type)

            entry_id = str(uuid.uuid4())
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
                        entry = format_bookmark_entry(
                            payload=payload, entry_id=entry_id, status="failed"
                        )
                        await write_entry(entry, payload.type == "youtube")
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
    Initializes SQLite log, verifies Gemini credentials, and spawns the
    background worker on startup. The worker runs until shutdown, when it's
    cancelled cleanly.
    """
    init_ingest_log()
    await verify_gemini_credentials()
    worker_task = asyncio.create_task(background_worker())
    logger.info("[Server] Orbit V2 ingestion server started.")

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("[Server] Orbit V2 ingestion server stopped.")


app = FastAPI(title="Orbit Ingestion Server", version="2.1.0", lifespan=lifespan)


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────


@app.post("/ingest", status_code=202)
async def ingest(payload: IngestPayload):
    """
    Accept a normalized bookmark payload and queue it for async processing.

    Returns 202 Accepted immediately — the user never waits for LLM enrichment
    or video download. Processing happens in the background worker.
    """
    existing = find_existing_entry(payload.source_url)
    if existing and existing[1] in ("enriched", "ingested"):
        logger.info(f"[API] Duplicate, not queued: {payload.source_url}")
        return {"status": "duplicate", "existing_id": existing[0], "queue_size": ingest_queue.qsize()}

    await ingest_queue.put(payload)
    logger.info(f"[API] Queued: {payload.source_url} (queue size: {ingest_queue.qsize()})")
    return {"status": "accepted", "queue_size": ingest_queue.qsize()}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "queue_size": ingest_queue.qsize(),
        "gemini_available": gemini_available,
        "video_download_enabled": DOWNLOAD_VIDEOS,
        "bookmarks_file": str(BOOKMARKS_FILE),
        "bookmarks_exists": BOOKMARKS_FILE.exists(),
        "transcripts_file": str(TRANSCRIPTS_FILE),
        "transcripts_exists": TRANSCRIPTS_FILE.exists(),
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

    return {"total": total, "by_status": by_status, "by_type": by_type}
