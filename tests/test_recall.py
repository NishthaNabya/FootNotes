import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

import server
import backfill


class FakeEmbeddingProvider:
    name = "fake"
    model = "semantic-test-v1"
    dimensions = 4

    def __init__(self, fail=False):
        self.fail = fail
        self.document_calls = 0
        self.query_calls = 0

    @property
    def available(self):
        return True

    @staticmethod
    def _vector(text):
        text = text.lower()
        if any(word in text for word in ("footnote", "landing", "homepage", "onboarding")):
            return [0.5, 0.5, 0.0, 0.0]
        if any(word in text for word in ("background", "ambient", "unnoticed", "invisible")):
            return [1.0, 0.0, 0.0, 0.0]
        if any(word in text for word in ("cooking", "sourdough", "recipe", "bread")):
            return [0.0, 1.0, 0.0, 0.0]
        if any(word in text for word in ("network", "tcp", "router")):
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_document(self, text, title=""):
        self.document_calls += 1
        if self.fail:
            raise RuntimeError("simulated embedding outage")
        return self._vector(f"{title}\n{text}")

    async def embed_query(self, text):
        self.query_calls += 1
        if self.fail:
            raise RuntimeError("simulated embedding outage")
        return self._vector(text)


class RecallFoundationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.originals = {
            "VAULT_DIR": server.VAULT_DIR,
            "INGEST_LOG_DB": server.INGEST_LOG_DB,
            "VIDEOS_DIR": server.VIDEOS_DIR,
            "embedding_provider": server.embedding_provider,
            "ingest_queue": server.ingest_queue,
            "resurface_cache": server.resurface_cache,
        }
        server.VAULT_DIR = self.root
        server.INGEST_LOG_DB = self.root / "ingest.log"
        server.VIDEOS_DIR = self.root / "videos"
        server.VIDEOS_DIR.mkdir()
        server.embedding_provider = FakeEmbeddingProvider()
        server.ingest_queue = asyncio.Queue()
        server.resurface_cache = {}
        server.init_ingest_log()

    async def asyncTearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.tempdir.cleanup()

    def payload(self, title, content, url, entry_type="article", selection=None):
        return server.IngestPayload(
            type=entry_type,
            source_url=url,
            source_platform=server.detect_platform(url),
            author="Ada Example",
            author_handle="@ada",
            title=title,
            captured_at="2026-08-12T12:00:00+00:00",
            published_at=None,
            content=content,
            selection=selection,
        )

    async def save(self, entry_id, content, url, *, title, embed=True, tags=None):
        payload = self.payload(title, content, url)
        path = await server.write_entry_file(
            payload,
            entry_id,
            tags=tags or [],
            summary=None,
            status="ingested",
        )
        entry = server.parse_entry_file(path)
        if embed:
            await server.ensure_entry_embedding(entry)
        return entry

    async def update_note(self, entry_id, note):
        background_tasks = BackgroundTasks()
        result = await server.update_entry_note(
            entry_id, server.UserNoteUpdate(user_note=note), background_tasks
        )
        await background_tasks()
        return result

    def set_vector(self, entry, vector):
        server._save_embedding_state(
            entry["id"], server.embedding_content_hash(entry), "ready", vector=vector
        )

    def page(self, title="Invisible interfaces", text="Ambient products stay out of the way."):
        return server.PageContext(
            url="https://current.example.com/article",
            title=title,
            description="A public article about calm product design and useful interfaces.",
            text=text,
        )

    async def test_exact_title_search(self):
        intended = await self.save(
            "title-1", "Short body.", "https://example.com/receipt", embed=False,
            title="Give users a receipt when they subscribe",
        )
        await self.save(
            "title-2", "A sourdough recipe.", "https://example.com/bread",
            title="Weekend baking",
        )
        ranked = await server.hybrid_recall(server.load_vault_entries(), intended["title"])
        self.assertEqual(ranked[0]["id"], intended["id"])
        self.assertGreater(ranked[0]["relevance"]["lexical"], 0)
        self.assertTrue(any("exact phrase in title" in reason for reason in ranked[0]["relevance"]["reasons"]))

    async def test_exact_keyword_search(self):
        intended = await self.save(
            "keyword-1",
            "The deployment uses an idempotency token to keep retries safe.",
            "https://example.com/retries",
            title="Reliable ingestion",
            embed=False,
        )
        ranked = await server.hybrid_recall(server.load_vault_entries(), "idempotency")
        self.assertEqual(ranked[0]["id"], intended["id"])
        self.assertIn("idempotency", ranked[0]["relevance"]["matched_terms"])

    async def test_vague_semantic_wording_retrieves_intended_entry(self):
        intended = await self.save(
            "semantic-1",
            "The best tools recede into the background and work ambiently without being noticed.",
            "https://example.com/calm-tech",
            title="Calm technology",
        )
        await self.save(
            "semantic-2",
            "Feed the sourdough starter before using this bread recipe.",
            "https://example.com/sourdough",
            title="Kitchen notes",
        )
        ranked = await server.hybrid_recall(
            server.load_vault_entries(), "products becoming invisible"
        )
        self.assertEqual(ranked[0]["id"], intended["id"])
        self.assertEqual(ranked[0]["relevance"]["lexical"], 0)
        self.assertGreater(ranked[0]["relevance"]["semantic"], 0)

    async def test_unrelated_memory_ranks_lower(self):
        intended = await self.save(
            "rank-1", "Ambient systems fade into the background.",
            "https://example.com/ambient", title="Quiet products",
        )
        unrelated = await self.save(
            "rank-2", "A sourdough bread cooking recipe.",
            "https://example.com/cooking", title="Bread",
        )
        ranked = await server.hybrid_recall(server.load_vault_entries(), "invisible technology")
        scores = {entry["id"]: entry["relevance"]["score"] for entry in ranked}
        self.assertGreater(scores[intended["id"]], scores.get(unrelated["id"], 0))

    async def test_duplicate_capture_is_not_requeued(self):
        payload = self.payload("Duplicate", "Body", "https://example.com/item?utm_source=x")
        server.log_ingest_entry("existing-id", payload.source_url, payload.type, "ingested")
        duplicate = self.payload("Duplicate", "Body", "https://example.com/item")
        result = await server.ingest(duplicate)
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["existing_id"], "existing-id")
        self.assertEqual(server.ingest_queue.qsize(), 0)

    async def test_duplicate_capture_preserves_a_queued_user_note(self):
        payload = self.payload("Duplicate", "Body", "https://example.com/noted-item")
        accepted = await server.ingest(payload)
        await self.update_note(accepted["entry_id"], "research later")
        duplicate = await server.ingest(payload)
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["existing_id"], accepted["entry_id"])
        self.assertEqual(duplicate["user_note"], "research later")
        self.assertEqual(server.ingest_queue.qsize(), 1)

    async def test_embedding_backfill_is_idempotent(self):
        entry = await self.save(
            "backfill-1", "TCP packets cross a network.",
            "https://example.com/network", title="Networking", embed=False,
        )
        provider = server.embedding_provider
        self.assertEqual(server.embedding_state(entry), "missing")
        self.assertEqual(await server.ensure_entry_embedding(entry), "embedded")
        self.assertEqual(await server.ensure_entry_embedding(entry), "unchanged")
        self.assertEqual(provider.document_calls, 1)
        self.assertEqual(server.embedding_state(entry), "ready")

    async def test_failed_embedding_does_not_reject_capture(self):
        server.embedding_provider = FakeEmbeddingProvider(fail=True)
        payload = self.payload(
            "Captured during outage",
            "This content must survive.",
            "https://x.com/example/status/123456789",
            entry_type="tweet",
        )
        status = await server.process_payload(payload, "failure-1")
        entries = server.load_vault_entries()
        self.assertEqual(status, "ingested")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], "This content must survive.")
        self.assertEqual(server.embedding_state(entries[0]), "failed")

    async def test_restart_preserves_derived_vectors_and_recall(self):
        intended = await self.save(
            "restart-1", "An ambient assistant stays in the background.",
            "https://example.com/restart", title="Calm systems",
        )
        server.embedding_provider = FakeEmbeddingProvider()  # simulate a fresh process/provider object
        self.assertIn(intended["id"], server.load_embedding_vectors({intended["id"]}))
        ranked = await server.hybrid_recall(server.load_vault_entries(), "invisible products")
        self.assertEqual(ranked[0]["id"], intended["id"])

    async def test_existing_markdown_without_embedding_is_readable_and_searchable(self):
        legacy = await self.save(
            "legacy-1", "A rare heliotrope protocol appears here.",
            "https://example.com/legacy", title="Heliotrope protocol", embed=False,
        )
        await self.save(
            "legacy-2", "A sourdough cooking recipe.",
            "https://example.com/other", title="Cooking",
        )
        parsed = server.parse_entry_file(self.root / legacy["vault_path"])
        self.assertEqual(parsed["id"], legacy["id"])
        ranked = await server.hybrid_recall(server.load_vault_entries(), "heliotrope protocol")
        self.assertEqual(ranked[0]["id"], legacy["id"])
        self.assertGreater(ranked[0]["relevance"]["lexical"], 0)

    async def test_existing_capture_type_and_selection_paths_still_work(self):
        self.assertEqual(server.detect_type("https://youtu.be/abcdefghijk", "article"), "youtube")
        self.assertEqual(server.detect_type("https://x.com/i/status/123", "article"), "tweet")
        payload = self.payload(
            "Selection capture", "", "https://example.com/page", selection="The selected passage."
        )
        with patch.object(server, "extract_article_content", new=AsyncMock(return_value=None)):
            await server.process_payload(payload, "selection-1")
        entry = server.load_vault_entries()[0]
        self.assertIn("The selected passage.", entry["content"])

    async def test_hash_tracks_only_canonical_provider_input(self):
        entry = await self.save(
            "hash-1", "Canonical body.", "https://example.com/hash",
            title="Canonical title", embed=False,
        )
        original = server.embedding_content_hash(entry)
        entry["status"] = "enriched"
        entry["captured_at"] = "2099-01-01T00:00:00Z"
        entry["vault_path"] = "renamed/for-display.md"
        self.assertEqual(server.embedding_content_hash(entry), original)
        entry["content"] = "Canonical body changed."
        self.assertNotEqual(server.embedding_content_hash(entry), original)

    async def test_provider_model_versions_can_coexist(self):
        entry = await self.save(
            "models-1", "Ambient tools stay in the background.",
            "https://example.com/models", title="Provider versions",
        )
        replacement = FakeEmbeddingProvider()
        replacement.model = "semantic-test-v2"
        server.embedding_provider = replacement
        self.assertEqual(server.embedding_state(entry), "missing")
        await server.ensure_entry_embedding(entry)

        import sqlite3
        connection = sqlite3.connect(server.INGEST_LOG_DB)
        rows = connection.execute(
            "SELECT model FROM memory_embeddings WHERE entry_id = ? ORDER BY model",
            (entry["id"],),
        ).fetchall()
        connection.close()
        self.assertEqual(rows, [("semantic-test-v1",), ("semantic-test-v2",)])

    async def test_search_api_shape_supports_recall_ui(self):
        intended = await self.save(
            "api-1", "Ambient tools recede into the background.",
            "https://example.com/api", title="Calm interface", tags=["product-design"],
        )
        result = await server.search_entries(q="invisible products", limit=10)
        hit = result["entries"][0]
        self.assertEqual(hit["id"], intended["id"])
        for field in ("title", "source_platform", "source_url", "author", "captured_at", "excerpt", "tags", "relevance"):
            self.assertIn(field, hit)
        self.assertNotIn("content", hit)
        self.assertIn("score", hit["relevance"])
        self.assertIn("reasons", hit["relevance"])

    async def test_capture_succeeds_without_a_user_note(self):
        payload = self.payload(
            "Zero friction", "Capture remains complete without annotation.",
            "https://example.com/zero-friction",
        )
        status = await server.process_payload(payload, "no-note-1")
        entry = server.load_vault_entries()[0]
        self.assertEqual(status, "ingested")
        self.assertEqual(entry["user_note"], "")
        self.assertEqual(entry["source_url"], "https://example.com/zero-friction")
        self.assertEqual(entry["captured_at"], "2026-08-12T12:00:00+00:00")
        expected_hash = server.captured_content_hash(
            "Capture remains complete without annotation."
        )
        self.assertEqual(entry["content_hash"], expected_hash)
        markdown = (self.root / entry["vault_path"]).read_text()
        self.assertIn(f"content_hash: {expected_hash}", markdown)
        self.assertIn("user_note: null", markdown)

    async def test_backfill_does_not_replace_a_short_captured_article(self):
        content = "A short but complete saved page."
        meta = {
            "id": "short-original-1",
            "type": "article",
            "source_url": "https://example.com/short-original",
            "source_platform": "other",
            "author": "Ada Example",
            "author_handle": "@ada",
            "title": "Short original",
            "captured_at": "2026-08-12T12:00:00+00:00",
            "published_at": None,
            "user_note": None,
            "tags": ["example"],
            "summary": "Already enriched.",
            "key_insights": [],
            "status": "enriched",
        }
        with patch.object(
            server,
            "extract_article_content",
            new=AsyncMock(return_value="A changed page fetched later."),
        ) as extract:
            payload, repaired, *_rest = await backfill.repair_entry(
                meta, content, force=False, dry_run=False
            )

        extract.assert_not_awaited()
        self.assertEqual(repaired, content)
        self.assertEqual(payload.content, content)

    async def test_note_can_arrive_while_capture_is_queued(self):
        payload = self.payload(
            "Queued thought", "The original captured content.",
            "https://example.com/queued-thought",
        )
        accepted = await server.ingest(payload)
        self.assertEqual(accepted["status"], "accepted")
        entry_id = accepted["entry_id"]
        pending = await self.update_note(entry_id, "use this for Footnote onboarding")
        self.assertEqual(pending["status"], "pending")

        queued_payload, queued_id = await server.ingest_queue.get()
        try:
            await server.process_payload(queued_payload, queued_id)
        finally:
            server.ingest_queue.task_done()
        entry = server.load_vault_entries()[0]
        self.assertEqual(entry["id"], entry_id)
        self.assertEqual(entry["user_note"], "use this for Footnote onboarding")
        self.assertEqual(entry["content"], "The original captured content.")

    async def test_note_persists_and_participates_in_exact_recall(self):
        entry = await self.save(
            "note-exact-1", "Generic visual design material.",
            "https://example.com/inspiration", title="Design reference",
        )
        path = self.root / entry["vault_path"]
        original_text = path.read_text()
        original_body = original_text[original_text.find("\n---\n", 4) + 5:]
        original_source_line = "source_url: https://example.com/inspiration"
        self.assertIn(original_source_line, original_text)
        result = await self.update_note(entry["id"], "use this for Footnote landing page")
        self.assertEqual(result["status"], "saved")
        updated_text = path.read_text()
        updated_body = updated_text[updated_text.find("\n---\n", 4) + 5:]
        self.assertEqual(updated_body, original_body)
        self.assertIn(original_source_line, updated_text)
        reparsed = server.parse_entry_file(path)
        self.assertEqual(reparsed["user_note"], "use this for Footnote landing page")
        self.assertEqual(reparsed["content"], "Generic visual design material.")

        ranked = await server.hybrid_recall(
            server.load_vault_entries(), "Footnote landing page"
        )
        self.assertEqual(ranked[0]["id"], entry["id"])
        self.assertTrue(any("user note" in reason for reason in ranked[0]["relevance"]["reasons"]))

    async def test_user_note_participates_in_semantic_recall(self):
        intended = await self.save(
            "note-semantic-1", "A generic collection of color examples.",
            "https://example.com/colors", title="Color study",
        )
        unrelated = await self.save(
            "note-semantic-2", "A sourdough bread recipe.",
            "https://example.com/bread-note", title="Bread",
        )
        await self.update_note(intended["id"], "use this for Footnote landing page")
        ranked = await server.hybrid_recall(
            server.load_vault_entries(), "thing I wanted for the Footnote homepage"
        )
        self.assertEqual(ranked[0]["id"], intended["id"])
        scores = {item["id"]: item["relevance"]["score"] for item in ranked}
        self.assertGreater(scores[intended["id"]], scores.get(unrelated["id"], 0))

    async def test_changing_note_rebuilds_only_that_embedding(self):
        entry = await self.save(
            "note-change-1", "Stable original body.",
            "https://example.com/change-note", title="Stable memory",
        )
        provider = server.embedding_provider
        before_hash = server.embedding_content_hash(entry)
        before_calls = provider.document_calls
        await self.update_note(entry["id"], "research later")
        updated = server.parse_entry_file(self.root / entry["vault_path"])
        self.assertNotEqual(server.embedding_content_hash(updated), before_hash)
        self.assertEqual(provider.document_calls, before_calls + 1)
        self.assertEqual(server.embedding_state(updated), "ready")

    async def test_embedding_failure_never_loses_note_or_memory(self):
        entry = await self.save(
            "note-failure-1", "Original content must remain.",
            "https://example.com/note-failure", title="Durable note", embed=False,
        )
        server.embedding_provider = FakeEmbeddingProvider(fail=True)
        result = await self.update_note(entry["id"], "argument for my essay")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["embedding"], "scheduled")
        reparsed = server.parse_entry_file(self.root / entry["vault_path"])
        self.assertEqual(reparsed["user_note"], "argument for my essay")
        self.assertEqual(reparsed["content"], "Original content must remain.")
        self.assertEqual(server.embedding_state(reparsed), "failed")

    async def test_related_finds_semantic_neighbor_with_different_tags(self):
        source = await self.save(
            "related-source", "Calm products fade into daily life.",
            "https://example.com/calm", title="Calm products", embed=False,
            tags=["product-design"],
        )
        neighbor = await self.save(
            "related-neighbor", "Ambient interfaces stay out of the way.",
            "https://example.com/ambient-ui", title="Ambient interface", embed=False,
            tags=["calm-technology"],
        )
        unrelated = await self.save(
            "related-unrelated", "A sourdough bread recipe.",
            "https://example.com/bread-related", title="Bread", embed=False,
            tags=["cooking"],
        )
        self.set_vector(source, [1.0, 0.0, 0.0, 0.0])
        self.set_vector(neighbor, [0.8, 0.6, 0.0, 0.0])
        # Cosine 0.70 is plausibly similar, but below Footnote's quality floor.
        self.set_vector(unrelated, [0.70, 0.71414284, 0.0, 0.0])

        related = server.related_memories(server.load_vault_entries(), source["id"])
        self.assertEqual([entry["id"] for entry in related], [neighbor["id"]])
        self.assertEqual(related[0]["relationship"]["method"], "semantic")
        self.assertEqual(related[0]["relationship"]["shared_tags"], [])
        self.assertNotIn(source["id"], [entry["id"] for entry in related])
        self.assertNotIn(unrelated["id"], [entry["id"] for entry in related])

    async def test_related_excludes_obvious_duplicate(self):
        source = await self.save(
            "duplicate-source", "Same original body.",
            "https://example.com/duplicate?utm_source=footnote",
            title="Original", embed=False,
        )
        duplicate = await self.save(
            "duplicate-copy", "A copied body.",
            "https://example.com/duplicate", title="Copy", embed=False,
        )
        self.set_vector(source, [1.0, 0.0, 0.0, 0.0])
        self.set_vector(duplicate, [1.0, 0.0, 0.0, 0.0])
        related = server.related_memories(server.load_vault_entries(), source["id"])
        self.assertEqual(related, [])

    async def test_user_note_naturally_influences_relatedness(self):
        source = await self.save(
            "note-related-source", "Generic visual reference.",
            "https://example.com/note-related-source", title="Reference A",
        )
        neighbor = await self.save(
            "note-related-neighbor", "Another generic visual reference.",
            "https://example.com/note-related-neighbor", title="Reference B",
        )
        await self.update_note(source["id"], "use this for Footnote onboarding")
        await self.update_note(neighbor["id"], "idea for the Footnote homepage")
        related = server.related_memories(server.load_vault_entries(), source["id"])
        self.assertEqual(related[0]["id"], neighbor["id"])
        self.assertEqual(related[0]["relationship"]["method"], "semantic")

    async def test_related_missing_embeddings_falls_back_to_shared_tags(self):
        source = await self.save(
            "fallback-source", "Legacy memory one.",
            "https://example.com/fallback-source", title="Legacy A", embed=False,
            tags=["local-first"],
        )
        fallback = await self.save(
            "fallback-neighbor", "Legacy memory two.",
            "https://example.com/fallback-neighbor", title="Legacy B", embed=False,
            tags=["local-first", "ownership"],
        )
        related = server.related_memories(server.load_vault_entries(), source["id"])
        self.assertEqual(related[0]["id"], fallback["id"])
        self.assertEqual(related[0]["relationship"]["method"], "tag_fallback")

    async def test_related_uses_local_vectors_during_provider_failure_and_restart(self):
        source = await self.save(
            "offline-source", "Ambient interaction.",
            "https://example.com/offline-source", title="Ambient A", embed=False,
        )
        neighbor = await self.save(
            "offline-neighbor", "Invisible interaction.",
            "https://example.com/offline-neighbor", title="Ambient B", embed=False,
        )
        self.set_vector(source, [1.0, 0.0, 0.0, 0.0])
        self.set_vector(neighbor, [0.9, 0.1, 0.0, 0.0])

        server.embedding_provider = FakeEmbeddingProvider(fail=True)
        first = server.related_memories(server.load_vault_entries(), source["id"])
        self.assertEqual(first[0]["id"], neighbor["id"])
        self.assertEqual(server.embedding_provider.query_calls, 0)
        self.assertEqual(server.embedding_provider.document_calls, 0)

        server.embedding_provider = FakeEmbeddingProvider()
        after_restart = server.related_memories(
            server.load_vault_entries(), source["id"]
        )
        self.assertEqual(after_restart[0]["id"], neighbor["id"])

    async def test_related_endpoint_returns_recognizable_entries(self):
        source = await self.save(
            "api-related-source", "Ambient product behavior.",
            "https://example.com/api-related-source", title="Source", embed=False,
        )
        neighbor = await self.save(
            "api-related-neighbor", "Calm product behavior.",
            "https://example.com/api-related-neighbor", title="Neighbor", embed=False,
        )
        self.set_vector(source, [1.0, 0.0, 0.0, 0.0])
        self.set_vector(neighbor, [0.9, 0.1, 0.0, 0.0])
        response = await server.get_related_entries(source["id"], limit=3)
        self.assertEqual(response["count"], 1)
        hit = response["entries"][0]
        for field in ("id", "title", "source_platform", "source_url", "author", "excerpt"):
            self.assertIn(field, hit)
        self.assertNotIn("content", hit)

    async def test_strong_current_page_context_resurfaces_expected_memory(self):
        intended = await self.save(
            "resurface-strong", "Tools should recede into the background.",
            "https://example.com/resurface-strong", title="Calm technology", embed=False,
        )
        self.set_vector(intended, [1.0, 0.0, 0.0, 0.0])
        response = await server.contextual_resurface(
            self.page(), server.load_vault_entries()
        )
        self.assertEqual([entry["id"] for entry in response["entries"]], [intended["id"]])
        self.assertFalse(response["context_persisted"])

    async def test_weak_current_page_match_shows_nothing(self):
        weak = await self.save(
            "resurface-weak", "Somewhat adjacent material.",
            "https://example.com/resurface-weak", title="Adjacent", embed=False,
        )
        self.set_vector(weak, [0.83, 0.557763, 0.0, 0.0])
        response = await server.contextual_resurface(
            self.page(), server.load_vault_entries()
        )
        self.assertEqual(response["entries"], [])

    async def test_unrelated_current_page_shows_nothing(self):
        unrelated = await self.save(
            "resurface-none", "Sourdough bread cooking recipe.",
            "https://example.com/resurface-none", title="Bread", embed=False,
        )
        self.set_vector(unrelated, [0.0, 1.0, 0.0, 0.0])
        response = await server.contextual_resurface(
            self.page(), server.load_vault_entries()
        )
        self.assertEqual(response["entries"], [])

    async def test_multiple_strong_context_matches_are_ranked(self):
        strongest = await self.save(
            "resurface-first", "Ambient interfaces.",
            "https://example.com/resurface-first", title="First", embed=False,
        )
        second = await self.save(
            "resurface-second", "Quiet products.",
            "https://example.com/resurface-second", title="Second", embed=False,
        )
        third = await self.save(
            "resurface-third", "Background tools.",
            "https://example.com/resurface-third", title="Third", embed=False,
        )
        self.set_vector(strongest, [1.0, 0.0, 0.0, 0.0])
        self.set_vector(second, [0.95, 0.31225, 0.0, 0.0])
        self.set_vector(third, [0.90, 0.43589, 0.0, 0.0])
        response = await server.contextual_resurface(
            self.page(), server.load_vault_entries()
        )
        self.assertEqual(
            [entry["id"] for entry in response["entries"]],
            [strongest["id"], second["id"], third["id"]],
        )

    async def test_user_note_influences_contextual_resurfacing(self):
        memory = await self.save(
            "resurface-note", "Generic visual reference.",
            "https://example.com/resurface-note", title="Reference",
        )
        await self.update_note(memory["id"], "try this invisible interface later")
        response = await server.contextual_resurface(
            self.page(), server.load_vault_entries()
        )
        self.assertEqual(response["entries"][0]["id"], memory["id"])
        self.assertEqual(
            response["entries"][0]["user_note"],
            "try this invisible interface later",
        )

    async def test_page_context_is_ephemeral_and_cached(self):
        memory = await self.save(
            "resurface-cache", "Ambient product behavior.",
            "https://example.com/resurface-cache", title="Ambient", embed=False,
        )
        self.set_vector(memory, [1.0, 0.0, 0.0, 0.0])
        provider = server.embedding_provider
        files_before = list(server.iter_entry_files())
        first = await server.contextual_resurface(self.page(), server.load_vault_entries())
        second = await server.contextual_resurface(self.page(), server.load_vault_entries())
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(provider.query_calls, 1)
        self.assertEqual(list(server.iter_entry_files()), files_before)

    async def test_provider_failure_makes_resurfacing_fail_silently(self):
        memory = await self.save(
            "resurface-failure", "Ambient product behavior.",
            "https://example.com/resurface-failure", title="Ambient", embed=False,
        )
        self.set_vector(memory, [1.0, 0.0, 0.0, 0.0])
        server.embedding_provider = FakeEmbeddingProvider(fail=True)
        response = await server.contextual_resurface(
            self.page(title="A different public page"), server.load_vault_entries()
        )
        self.assertEqual(response["entries"], [])
        self.assertFalse(response["embedding_available"])


if __name__ == "__main__":
    unittest.main()
