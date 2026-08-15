import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import footnote_app
import footnote_config
import server
from scripts import package_extension


class FakeRequest:
    headers = {"origin": "http://localhost:8000"}


class ReleaseConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = patch.dict(os.environ, {
            "FOOTNOTE_CONFIG_DIR": str(self.root / "config"),
            "FOOTNOTE_LOG_DIR": str(self.root / "logs"),
        })
        self.env.start()
        self.originals = {
            name: getattr(server, name) for name in (
                "VAULT_DIR", "INGEST_LOG_DB", "VIDEOS_DIR", "LEGACY_BOOKMARKS_FILE",
                "LEGACY_TRANSCRIPTS_FILE", "embedding_provider", "intelligence_provider",
                "provider_health", "ACTIVE_PROVIDER_NAME",
            )
        }

    async def asyncTearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.env.stop()
        self.tempdir.cleanup()

    async def test_fresh_no_ai_setup_and_custom_storage(self):
        vault = self.root / "My Memories"
        result = await server.save_setup(
            server.SetupUpdate(vault_path=str(vault), provider="none"), FakeRequest()
        )
        config = footnote_config.load_config()
        self.assertTrue(config.setup_complete)
        self.assertEqual(config.resolved_vault_path, vault.resolve())
        self.assertEqual(config.provider, "none")
        self.assertTrue((vault / "ingest.log").exists())
        self.assertIn("exact Recall", result["message"])

    async def test_existing_vault_is_selected_in_place_without_rewriting(self):
        vault = self.root / "existing"
        vault.mkdir()
        memory = vault / "articles" / "keep-me.md"
        memory.parent.mkdir()
        original = "---\nid: durable\ntype: article\n---\n\nOriginal memory\n"
        memory.write_text(original, encoding="utf-8")
        await server.save_setup(
            server.SetupUpdate(vault_path=str(vault), provider="none"), FakeRequest()
        )
        self.assertEqual(memory.read_text(encoding="utf-8"), original)

    def test_removed_provider_config_migrates_to_no_ai(self):
        path = footnote_config.config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 2,
            "setup_complete": True,
            "vault_path": str(self.root / "vault"),
            "provider": "gemini",
        }), encoding="utf-8")
        loaded = footnote_config.load_config()
        self.assertEqual(loaded.provider, "none")
        self.assertEqual(loaded.resolved_vault_path, self.root / "vault")
        self.assertEqual(json.loads(path.read_text())["provider"], "none")
        with patch.dict(os.environ, {"FOOTNOTE_INTELLIGENCE_PROVIDER": ""}):
            self.assertEqual(server.selected_provider_name(loaded), "none")

    def test_previous_product_config_is_migrated_without_touching_its_vault(self):
        legacy_settings = self.root / "previous-settings"
        current_settings = self.root / "current-settings"
        vault = self.root / "existing-vault"
        memory = vault / "articles" / "keep.md"
        memory.parent.mkdir(parents=True)
        original = "---\nid: keep\ntype: article\n---\n\nHistorical memory\n"
        memory.write_text(original, encoding="utf-8")
        legacy_settings.mkdir()
        (legacy_settings / "config.json").write_text(json.dumps({
            "version": 3,
            "setup_complete": True,
            "vault_path": str(vault),
            "provider": "none",
        }), encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True), \
             patch.object(footnote_config, "app_support_dir", return_value=current_settings), \
             patch.object(footnote_config, "_legacy_app_support_dir", return_value=legacy_settings):
            loaded = footnote_config.load_config()

        self.assertEqual(loaded.resolved_vault_path, vault)
        self.assertTrue((current_settings / "config.json").exists())
        self.assertEqual(memory.read_text(encoding="utf-8"), original)

    def test_existing_enriched_markdown_remains_readable(self):
        vault = self.root / "historic-vault"
        memory = vault / "articles" / "historic.md"
        memory.parent.mkdir(parents=True)
        memory.write_text(
            "---\nid: historic\ntype: article\n"
            "source_url: https://example.com/historic\nsource_platform: other\n"
            "author: ''\nauthor_handle: ''\ntitle: Historic memory\n"
            "captured_at: '2025-01-01T00:00:00+00:00'\npublished_at: null\n"
            "user_note: null\ntags: [local-first]\nsummary: Existing summary\n"
            "key_insights: [Existing insight]\nstatus: enriched\n---\n\n"
            "## Content\n\nOriginal captured body.\n\n## Context\n\n"
            "- **Original URL:** https://example.com/historic\n"
            "- **Captured:** 2025-01-01T00:00:00+00:00\n"
            "- **Platform:** other\n",
            encoding="utf-8",
        )
        server.configure_runtime(vault, "none")
        entry = server.load_vault_entries()[0]
        self.assertEqual(entry["content"], "Original captured body.")
        self.assertEqual(entry["summary"], "Existing summary")

    async def test_provider_outage_keeps_capture_and_lexical_recall_available(self):
        server.configure_runtime(self.root / "outage-vault", "none")
        payload = server.IngestPayload(
            type="article", source_url="https://example.com/outage",
            source_platform="other", title="Offline launch receipt",
            captured_at="2026-08-12T12:00:00+00:00", content="durable local content",
        )
        await server.write_entry_file(payload, "offline-entry", status="ingested")
        ranked = await server.hybrid_recall(server.load_vault_entries(), "launch receipt")
        self.assertEqual(ranked[0]["id"], "offline-entry")

    async def test_bounded_reconciliation_processes_only_requested_count(self):
        server.configure_runtime(self.root / "backfill-vault", "none")
        entries = []
        for index in range(3):
            payload = server.IngestPayload(
                type="article", source_url=f"https://example.com/{index}",
                source_platform="other", title=f"Memory {index}",
                captured_at="2026-08-12T12:00:00+00:00", content="body",
            )
            path = await server.write_entry_file(payload, f"entry-{index}", status="ingested")
            entries.append(server.parse_entry_file(path))
        class Provider:
            name, model, dimensions, available = "fake", "release", 2, True
            async def embed_document(self, text, title=""): return [1.0, 0.0]
            async def embed_query(self, text): return [1.0, 0.0]
        server.embedding_provider = Provider()
        outcomes = await server.reconcile_embedding_batch(limit=1)
        self.assertEqual(sum(outcomes.values()), 1)
        self.assertEqual(sum(server.embedding_state(entry) == "ready" for entry in entries), 1)


class LauncherAndPackagingTests(unittest.TestCase):
    def test_public_onboarding_is_ollama_or_no_ai_only_and_shows_sizes_before_commands(self):
        root = Path(__file__).resolve().parents[1] / "onboarding"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        self.assertEqual(
            set(re.findall(r'name="provider" value="([^"]+)"', html)),
            {"ollama", "none"},
        )
        self.assertIn("about <b>2 GB</b> total", script)
        self.assertIn('embeddinggemma:"about 622 MB"', script)
        self.assertIn('"qwen3:1.7b":"about 1.4 GB"', script)

    def test_duplicate_launch_reuses_existing_service(self):
        with patch.object(footnote_app, "health", return_value={"service": "footnote"}), \
             patch.object(footnote_app, "open_status") as opened, \
             patch("footnote_app.subprocess.Popen") as spawned:
            self.assertEqual(footnote_app.start(), 0)
        opened.assert_called_once()
        spawned.assert_not_called()

    def test_port_conflict_has_clear_error_and_does_not_spawn(self):
        with patch.object(footnote_app, "health", return_value=None), \
             patch.object(footnote_app, "port_is_in_use", return_value=True), \
             patch.object(footnote_app, "wait_for_footnote", return_value=None), \
             patch.object(footnote_app, "show_error") as error, \
             patch("footnote_app.subprocess.Popen") as spawned:
            self.assertEqual(footnote_app.start(open_browser=False), 2)
        self.assertIn("Port 8000", error.call_args.args[0])
        spawned.assert_not_called()

    def test_extension_release_zip_is_allowlisted_and_has_no_dev_files(self):
        target = package_extension.package()
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, package_extension.ALLOWED)
            self.assertNotIn(".env", names)
            self.assertFalse(any("__pycache__" in name or name.startswith("tests/") for name in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], "0.1.0")

    def test_runtime_imports_without_google_sdk(self):
        root = Path(__file__).resolve().parents[1]
        script = """
import builtins
original_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'google' or name.startswith('google.'):
        raise AssertionError('cloud SDK import attempted')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked
import providers
import server
assert server.intelligence_provider.name in {'ollama', 'none'}
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=root,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_requirements_and_packager_exclude_google_ai(self):
        root = Path(__file__).resolve().parents[1]
        dependency = "-".join(("google", "genai"))
        self.assertNotIn(dependency, (root / "requirements.txt").read_text().lower())
        packager = (root / "scripts" / "build_macos_release.py").read_text()
        self.assertIn("forbidden_module_paths", packager)

    def test_uninstall_documentation_explicitly_preserves_memory_folder(self):
        release = (Path(__file__).resolve().parents[1] / "RELEASE.md").read_text(encoding="utf-8")
        self.assertIn("leaves the memory folder", release)
        self.assertIn("Never remove the chosen memory folder", release)


if __name__ == "__main__":
    unittest.main()
