"""Local product configuration and macOS secret storage for Orbit.

Markdown remains user-owned data.  This module stores only launcher settings;
Gemini credentials live in macOS Keychain and never in the vault/config JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


CONFIG_VERSION = 2
KEYCHAIN_SERVICE = "com.orbit.memory"
KEYCHAIN_ACCOUNT = "gemini-api-key"


def app_support_dir() -> Path:
    override = os.getenv("ORBIT_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "Orbit"


def logs_dir() -> Path:
    override = os.getenv("ORBIT_LOG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / "Library" / "Logs" / "Orbit"


def default_vault_dir() -> Path:
    return Path.home() / "Documents" / "Orbit"


def config_path() -> Path:
    return app_support_dir() / "config.json"


@dataclass
class OrbitConfig:
    version: int = CONFIG_VERSION
    setup_complete: bool = False
    vault_path: str = ""
    provider: str = "none"
    ollama_enrichment_model: str = "qwen3:1.7b"
    ollama_embedding_model: str = "embeddinggemma"

    @property
    def resolved_vault_path(self) -> Path:
        return Path(self.vault_path).expanduser() if self.vault_path else default_vault_dir()


def load_config() -> OrbitConfig:
    path = config_path()
    if not path.exists():
        return OrbitConfig(vault_path=str(default_vault_dir()))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return OrbitConfig(
            version=int(raw.get("version", CONFIG_VERSION)),
            setup_complete=bool(raw.get("setup_complete", False)),
            vault_path=str(raw.get("vault_path") or default_vault_dir()),
            provider=raw.get("provider") if raw.get("provider") in {"ollama", "gemini", "none"} else "none",
            ollama_enrichment_model=str(raw.get("ollama_enrichment_model") or "qwen3:1.7b"),
            ollama_embedding_model=str(raw.get("ollama_embedding_model") or "embeddinggemma"),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return OrbitConfig(vault_path=str(default_vault_dir()))


def save_config(config: OrbitConfig) -> None:
    directory = app_support_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _test_secret_file() -> Optional[Path]:
    """A deliberately opt-in test backend; production always uses Keychain."""
    if os.getenv("ORBIT_SECRET_BACKEND") != "test-file":
        return None
    return app_support_dir() / "test-secret"


def store_gemini_key(api_key: str) -> None:
    key = api_key.strip()
    if not key:
        delete_gemini_key()
        return
    test_path = _test_secret_file()
    if test_path:
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(key, encoding="utf-8")
        os.chmod(test_path, 0o600)
        return
    if os.uname().sysname != "Darwin":
        raise RuntimeError("Orbit v0.1 secret storage currently requires macOS Keychain")
    result = subprocess.run(
        [
            "/usr/bin/security", "add-generic-password", "-U",
            "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w",
        ],
        input=key + "\n",
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not save the Gemini key in macOS Keychain")


def load_gemini_key() -> str:
    test_path = _test_secret_file()
    if test_path:
        return test_path.read_text(encoding="utf-8").strip() if test_path.exists() else ""
    if os.uname().sysname != "Darwin":
        return ""
    result = subprocess.run(
        [
            "/usr/bin/security", "find-generic-password",
            "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def delete_gemini_key() -> None:
    test_path = _test_secret_file()
    if test_path:
        test_path.unlink(missing_ok=True)
        return
    if os.uname().sysname == "Darwin":
        subprocess.run(
            [
                "/usr/bin/security", "delete-generic-password",
                "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )


def configured_api_key(config: Optional[OrbitConfig] = None) -> str:
    """Developer environment wins; packaged installs then consult Keychain."""
    current = config or load_config()
    if current.provider != "gemini":
        return ""
    environment_key = os.getenv("GEMINI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    return load_gemini_key()
