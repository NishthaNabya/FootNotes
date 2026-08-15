"""Local product configuration for Footnote.

Markdown remains user-owned data. This module stores only launcher settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


CONFIG_VERSION = 3


def _legacy_app_support_dir() -> Path:
    # One release-boundary compatibility path: settings are migrated into the
    # new product directory without touching the user-owned Markdown vault.
    return Path.home() / "Library" / "Application Support" / "Orbit"


def _legacy_default_vault_dir() -> Path:
    return Path.home() / "Documents" / "Orbit"


def app_support_dir() -> Path:
    override = os.getenv("FOOTNOTE_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "Footnote"


def logs_dir() -> Path:
    override = os.getenv("FOOTNOTE_LOG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / "Library" / "Logs" / "Footnote"


def default_vault_dir() -> Path:
    current = Path.home() / "Documents" / "Footnote"
    legacy = _legacy_default_vault_dir()
    return legacy if legacy.exists() and not current.exists() else current


def config_path() -> Path:
    return app_support_dir() / "config.json"


@dataclass
class FootnoteConfig:
    version: int = CONFIG_VERSION
    setup_complete: bool = False
    vault_path: str = ""
    provider: str = "none"
    ollama_enrichment_model: str = "qwen3:1.7b"
    ollama_embedding_model: str = "embeddinggemma"

    @property
    def resolved_vault_path(self) -> Path:
        return Path(self.vault_path).expanduser() if self.vault_path else default_vault_dir()


def load_config() -> FootnoteConfig:
    path = config_path()
    source = path
    legacy = _legacy_app_support_dir() / "config.json"
    if not source.exists() and not os.getenv("FOOTNOTE_CONFIG_DIR", "").strip() and legacy.exists():
        source = legacy
    if not source.exists():
        return FootnoteConfig(vault_path=str(default_vault_dir()))
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        raw_provider = str(raw.get("provider") or "none").lower()
        provider = raw_provider if raw_provider in {"ollama", "none"} else "none"
        config = FootnoteConfig(
            version=int(raw.get("version", CONFIG_VERSION)),
            setup_complete=bool(raw.get("setup_complete", False)),
            vault_path=str(raw.get("vault_path") or default_vault_dir()),
            provider=provider,
            ollama_enrichment_model=str(raw.get("ollama_enrichment_model") or "qwen3:1.7b"),
            ollama_embedding_model=str(raw.get("ollama_embedding_model") or "embeddinggemma"),
        )
        # Persist unsupported-provider migrations so future launches never
        # return to an unknown mode. This changes launcher settings only;
        # Markdown memories and adjacent derived indexes remain untouched.
        if source != path or raw_provider != provider or config.version != CONFIG_VERSION:
            config.version = CONFIG_VERSION
            try:
                save_config(config)
            except OSError:
                # A read-only settings file should not make startup forget the
                # selected vault. The in-memory migration is still safe.
                pass
        return config
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return FootnoteConfig(vault_path=str(default_vault_dir()))


def save_config(config: FootnoteConfig) -> None:
    directory = app_support_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
