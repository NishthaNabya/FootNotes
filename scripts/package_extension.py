#!/usr/bin/env python3
"""Create the clean Chrome Web Store-ready FootNotes extension ZIP."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
DIST = ROOT / "dist"
ALLOWED = {
    "background.js", "content.js", "interceptor.js", "manifest.json",
    "page-context-state.js", "page-context.js", "popup.html", "popup.js",
    "recall-state.js", "recall.css", "recall.html", "recall.js",
    "icons/footnotes-mark.svg", "icons/icon16.png", "icons/icon32.png",
    "icons/icon48.png", "icons/icon128.png",
}


def validate() -> dict:
    actual = {str(path.relative_to(EXTENSION)) for path in EXTENSION.rglob("*") if path.is_file()}
    if actual != ALLOWED:
        raise SystemExit(f"Extension allowlist mismatch: missing={sorted(ALLOWED-actual)} extra={sorted(actual-ALLOWED)}")
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise SystemExit("Chrome release must use Manifest V3")
    return manifest


def package() -> Path:
    manifest = validate()
    DIST.mkdir(exist_ok=True)
    target = DIST / f"footnotes-extension-{manifest['version']}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(ALLOWED):
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (EXTENSION / relative).read_bytes())
    return target


if __name__ == "__main__":
    output = package()
    print(output)
