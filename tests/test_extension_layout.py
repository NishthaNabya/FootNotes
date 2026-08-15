import json
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = REPOSITORY_ROOT / "extension"


class ExtensionLayoutTests(unittest.TestCase):
    def test_extension_contains_only_browser_runtime_files(self):
        expected = {
            "background.js",
            "content.js",
            "icons/footnote-mark.svg",
            "icons/icon16.png",
            "icons/icon32.png",
            "icons/icon48.png",
            "icons/icon128.png",
            "interceptor.js",
            "manifest.json",
            "page-context-state.js",
            "page-context.js",
            "popup.html",
            "popup.js",
            "recall-state.js",
            "recall.css",
            "recall.html",
            "recall.js",
        }
        actual = {
            str(path.relative_to(EXTENSION_ROOT))
            for path in EXTENSION_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)
        directories = {
            str(path.relative_to(EXTENSION_ROOT))
            for path in EXTENSION_ROOT.rglob("*")
            if path.is_dir()
        }
        self.assertEqual(directories, {"icons"})

    def test_every_manifest_resource_exists(self):
        manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text())
        resources = {
            manifest["background"]["service_worker"],
            manifest["action"]["default_popup"],
            *manifest["icons"].values(),
            *manifest["action"]["default_icon"].values(),
        }
        for content_script in manifest.get("content_scripts", []):
            resources.update(content_script.get("js", []))
            resources.update(content_script.get("css", []))
        for group in manifest.get("web_accessible_resources", []):
            resources.update(group.get("resources", []))

        for resource in resources:
            with self.subTest(resource=resource):
                self.assertNotIn("..", Path(resource).parts)
                self.assertFalse(Path(resource).is_absolute())
                self.assertTrue((EXTENSION_ROOT / resource).is_file())

    def test_extension_html_dependencies_exist(self):
        dependencies = {
            "popup.html": {"popup.js", "icons/icon48.png"},
            "recall.html": {
                "recall.css",
                "recall-state.js",
                "recall.js",
                "icons/icon48.png",
            },
        }
        for document, resources in dependencies.items():
            source = (EXTENSION_ROOT / document).read_text()
            for resource in resources:
                with self.subTest(document=document, resource=resource):
                    self.assertIn(resource, source)
                    self.assertTrue((EXTENSION_ROOT / resource).is_file())

    def test_resurfacing_uses_optional_all_site_access(self):
        manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text())
        self.assertIn("scripting", manifest["permissions"])
        self.assertEqual(
            set(manifest["optional_host_permissions"]),
            {"http://*/*", "https://*/*"},
        )
        static_scripts = {
            script
            for group in manifest.get("content_scripts", [])
            for script in group.get("js", [])
        }
        self.assertNotIn("page-context.js", static_scripts)
        background = (EXTENSION_ROOT / "background.js").read_text()
        self.assertIn('js: ["page-context-state.js", "page-context.js"]', background)

    def test_page_context_does_not_read_form_or_typed_values(self):
        source = (EXTENSION_ROOT / "page-context.js").read_text()
        self.assertIn("form, input, textarea, select", source)
        self.assertIn("[contenteditable='true']", source)
        self.assertNotIn(".value", source)

    def test_optional_personal_thought_uses_intent_prompt(self):
        popup = (EXTENSION_ROOT / "popup.html").read_text()
        self.assertIn("Why are you saving this?", popup)
        self.assertIn("Optional — something future-you should remember", popup)
        self.assertIn("Saved ✓", popup)
        self.assertNotIn("Add a thought…", popup)


if __name__ == "__main__":
    unittest.main()
