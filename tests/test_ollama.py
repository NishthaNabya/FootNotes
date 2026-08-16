import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

import server
from providers import OllamaProvider


class OllamaTransport:
    def __init__(self, *, running=True, models=None, fail_generate=False):
        self.running = running
        self.models = models or []
        self.fail_generate = fail_generate
        self.requests = []

    @staticmethod
    def vector(text):
        text = text.lower()
        if any(word in text for word in ("invisible", "ambient", "background", "quiet")):
            return [1.0, 0.0, 0.0, 0.0]
        if any(word in text for word in ("footnotes", "homepage", "landing", "onboarding")):
            return [0.7, 0.7, 0.0, 0.0]
        if any(word in text for word in ("bread", "recipe", "sourdough")):
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]

    def handler(self, request):
        self.requests.append(request)
        if not self.running:
            raise httpx.ConnectError("Ollama stopped", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in self.models]})
        body = json.loads(request.content)
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [self.vector(body["input"])]})
        if request.url.path == "/api/generate":
            if self.fail_generate:
                return httpx.Response(500, json={"error": "local model failed"})
            return httpx.Response(200, json={"response": json.dumps({
                "tags": ["calm-technology", "product-design"],
                "summary": "Useful products can recede into the background.",
                "key_insights": ["Quiet interfaces reduce cognitive load."],
            })})
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(
            base_url="http://127.0.0.1:11434",
            transport=httpx.MockTransport(self.handler),
        )


class OllamaProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_available_with_both_required_models(self):
        transport = OllamaTransport(models=["embeddinggemma:latest", "qwen3:1.7b"])
        provider = OllamaProvider(dimensions=4, client=transport.client())
        health = await provider.check_health()
        self.assertTrue(provider.available)
        self.assertTrue(provider.enrichment_available)
        self.assertEqual(health["missing_models"], [])

    async def test_unavailable_runtime_is_clear_and_nonfatal(self):
        provider = OllamaProvider(dimensions=4, client=OllamaTransport(running=False).client())
        health = await provider.check_health()
        self.assertFalse(provider.available)
        self.assertIn("not running", health["message"])

    async def test_running_runtime_reports_missing_models(self):
        provider = OllamaProvider(
            dimensions=4,
            client=OllamaTransport(models=["embeddinggemma:latest"]).client(),
        )
        health = await provider.check_health()
        self.assertTrue(health["runtime_available"])
        self.assertTrue(health["embedding_ready"])
        self.assertFalse(health["enrichment_ready"])
        self.assertEqual(health["missing_models"], ["qwen3:1.7b"])

    async def test_local_enrichment_and_embedding(self):
        transport = OllamaTransport(models=["embeddinggemma", "qwen3:1.7b"])
        provider = OllamaProvider(dimensions=4, client=transport.client())
        await provider.check_health()
        result = await provider.enrich(
            "Invisible products stay out of the way.", "Analyze.", server.EnrichmentResult
        )
        vector = await provider.embed_document("ambient background interface")
        self.assertEqual(result.tags[0], "calm-technology")
        self.assertEqual(vector, [1.0, 0.0, 0.0, 0.0])
        generate = next(request for request in transport.requests if request.url.path == "/api/generate")
        self.assertEqual(json.loads(generate.content)["stream"], False)
        self.assertIn("format", json.loads(generate.content))


class OllamaMemoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.originals = {
            name: getattr(server, name) for name in (
                "VAULT_DIR", "INGEST_LOG_DB", "VIDEOS_DIR", "embedding_provider",
                "intelligence_provider", "provider_health",
                "ingest_queue", "resurface_cache",
            )
        }
        server.VAULT_DIR = self.root
        server.INGEST_LOG_DB = self.root / "ingest.log"
        server.VIDEOS_DIR = self.root / "videos"
        server.VIDEOS_DIR.mkdir()
        transport = OllamaTransport(models=["embeddinggemma", "qwen3:1.7b"])
        self.provider = OllamaProvider(dimensions=4, client=transport.client())
        await self.provider.check_health()
        server.intelligence_provider = self.provider
        server.embedding_provider = self.provider
        server.ingest_queue = asyncio.Queue()
        server.resurface_cache = {}
        server.init_ingest_log()

    async def asyncTearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        await self.provider._client.aclose()
        self.tempdir.cleanup()

    def payload(self, entry_id, title, content, url, note=""):
        return server.IngestPayload(
            type="article", source_url=url, source_platform="other",
            title=title, captured_at="2026-08-12T12:00:00+00:00",
            content=content, user_note=note,
        )

    async def save(self, entry_id, title, content, url, note="", tags=None):
        path = await server.write_entry_file(
            self.payload(entry_id, title, content, url, note), entry_id,
            tags=tags or [], status="ingested",
        )
        entry = server.parse_entry_file(path)
        await server.ensure_entry_embedding(entry)
        return entry

    async def test_semantic_recall_note_related_and_resurfacing_use_ollama(self):
        calm = await self.save(
            "calm", "Calm products", "Tools recede quietly into the background.",
            "https://example.com/calm", tags=["interfaces"],
        )
        noted = await self.save(
            "noted", "Design reference", "A visual design case study.",
            "https://example.com/design", note="use this for FootNotes landing page",
            tags=["reference"],
        )
        bread = await self.save(
            "bread", "Bread", "Sourdough bread recipe.", "https://example.com/bread",
            tags=["cooking"],
        )
        ranked = await server.hybrid_recall(server.load_vault_entries(), "invisible technology")
        self.assertEqual(ranked[0]["id"], calm["id"])
        note_ranked = await server.hybrid_recall(server.load_vault_entries(), "FootNotes homepage idea")
        self.assertEqual(note_ranked[0]["id"], noted["id"])
        related = server.related_memories(server.load_vault_entries(), calm["id"])
        self.assertNotIn(bread["id"], [entry["id"] for entry in related])
        page = server.PageContext(
            url="https://current.example.com", title="Invisible interfaces",
            text="Ambient products work quietly in the background.",
        )
        resurfaced = await server.contextual_resurface(page, server.load_vault_entries())
        self.assertEqual(resurfaced["entries"][0]["id"], calm["id"])

    async def test_provider_failure_never_loses_capture(self):
        failed = OllamaProvider(
            dimensions=4,
            client=OllamaTransport(models=["embeddinggemma", "qwen3:1.7b"], fail_generate=True).client(),
        )
        await failed.check_health()
        server.intelligence_provider = failed
        server.embedding_provider = failed
        status = await server.process_payload(
            self.payload("failure", "Survives", "Durable content", "https://example.com/survives"),
            "failure",
        )
        self.assertEqual(status, "ingested")
        self.assertEqual(server.load_vault_entries()[0]["content"], "Durable content")
        await failed._client.aclose()

    async def test_obsolete_provider_rows_do_not_interfere_with_ollama(self):
        entry = await self.save(
            "switch", "Switch safely", "Ambient product design.",
            "https://example.com/switch",
        )
        ollama_row = server._embedding_row(entry["id"])
        self.assertEqual(ollama_row[0], "ollama")

        now = "2026-08-12T12:00:00+00:00"
        connection = sqlite3.connect(str(server.INGEST_LOG_DB))
        connection.execute(
            "INSERT INTO memory_embeddings "
            "(entry_id, provider, model, dimensions, content_hash, vector_json, "
            "status, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["id"], "gemini", "gemini-embedding-001", 4,
                "obsolete", "[0,0,1,0]", "ready", None, now, now,
            ),
        )
        connection.commit()
        connection.close()

        self.assertEqual(server.embedding_state(entry), "ready")
        self.assertEqual(server._embedding_row(entry["id"])[0], "ollama")
        ranked = await server.hybrid_recall(
            server.load_vault_entries(), "ambient product"
        )
        self.assertEqual(ranked[0]["id"], entry["id"])


if __name__ == "__main__":
    unittest.main()
