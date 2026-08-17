#!/usr/bin/env python3
"""Build the self-contained macOS FootNotes.app and release ZIP."""

from __future__ import annotations

import plistlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "pyinstaller"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def verify_release_tree(app: Path) -> None:
    forbidden = {".env", ".venv", "vault", "tests", "__pycache__"}
    forbidden_module_paths = {("google", "genai")}
    for path in app.rglob("*"):
        ordered_parts = path.relative_to(app).parts
        relative_parts = set(ordered_parts)
        if forbidden & relative_parts or path.suffix == ".pyc":
            raise SystemExit(f"Forbidden development/user file in app: {path}")
        lowered = tuple(part.lower() for part in ordered_parts)
        if any(
            lowered[index:index + len(module_path)] == module_path
            for module_path in forbidden_module_paths
            for index in range(len(lowered) - len(module_path) + 1)
        ):
            raise SystemExit(f"Forbidden cloud AI SDK in app: {path}")


def build() -> Path:
    if sys.platform != "darwin":
        raise SystemExit("FootNotes v0.1 app packaging currently supports macOS only")
    build_environment = os.environ.copy()
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "cache")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "footnotes-runtime", "--distpath", str(BUILD / "dist"),
            "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
            "--add-data", f"{ROOT / 'onboarding'}:onboarding",
            "--add-data", f"{ROOT / 'extension' / 'fonts'}:fonts",
            "--collect-all", "trafilatura",
            "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan.on", str(ROOT / "footnotes_app.py"),
        ],
        cwd=ROOT,
        env=build_environment,
        check=True,
    )
    app = DIST / "FootNotes.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copytree(BUILD / "dist" / "footnotes-runtime", resources / "footnotes-runtime")
    shutil.copy2(ROOT / "assets" / "FootNotes.icns", resources / "FootNotes.icns")
    launcher = macos / "FootNotes"
    launcher.write_text('#!/bin/sh\nHERE="$(cd "$(dirname "$0")/../Resources/footnotes-runtime" && pwd)"\nexec "$HERE/footnotes-runtime" start\n', encoding="utf-8")
    launcher.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as plist:
        plistlib.dump({
            "CFBundleDisplayName": "FootNotes", "CFBundleExecutable": "FootNotes",
            "CFBundleIdentifier": "com.footnotes.memory", "CFBundleName": "FootNotes",
            "CFBundleIconFile": "FootNotes.icns",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION, "LSMinimumSystemVersion": "12.0",
        }, plist)
    verify_release_tree(app)
    archive_base = DIST / f"FootNotes-macOS-arm64-{VERSION}"
    archive = Path(shutil.make_archive(str(archive_base), "zip", root_dir=DIST, base_dir="FootNotes.app"))
    return archive


if __name__ == "__main__":
    print(build())
