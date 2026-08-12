#!/usr/bin/env python3
"""Build the self-contained macOS Orbit.app and release ZIP."""

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
    for path in app.rglob("*"):
        relative_parts = set(path.relative_to(app).parts)
        if forbidden & relative_parts or path.suffix == ".pyc":
            raise SystemExit(f"Forbidden development/user file in app: {path}")


def build() -> Path:
    if sys.platform != "darwin":
        raise SystemExit("Orbit v0.1 app packaging currently supports macOS only")
    build_environment = os.environ.copy()
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "cache")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "orbit-runtime", "--distpath", str(BUILD / "dist"),
            "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
            "--add-data", f"{ROOT / 'onboarding'}:onboarding",
            "--collect-all", "trafilatura",
            "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan.on", str(ROOT / "orbit_app.py"),
        ],
        cwd=ROOT,
        env=build_environment,
        check=True,
    )
    app = DIST / "Orbit.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copytree(BUILD / "dist" / "orbit-runtime", resources / "orbit-runtime")
    launcher = macos / "Orbit"
    launcher.write_text('#!/bin/sh\nHERE="$(cd "$(dirname "$0")/../Resources/orbit-runtime" && pwd)"\nexec "$HERE/orbit-runtime" start\n', encoding="utf-8")
    launcher.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as plist:
        plistlib.dump({
            "CFBundleDisplayName": "Orbit", "CFBundleExecutable": "Orbit",
            "CFBundleIdentifier": "com.orbit.memory", "CFBundleName": "Orbit",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION, "LSMinimumSystemVersion": "12.0",
        }, plist)
    verify_release_tree(app)
    archive_base = DIST / f"Orbit-macOS-arm64-{VERSION}"
    archive = Path(shutil.make_archive(str(archive_base), "zip", root_dir=DIST, base_dir="Orbit.app"))
    return archive


if __name__ == "__main__":
    print(build())
