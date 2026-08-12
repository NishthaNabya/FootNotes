"""Provider-neutral intelligence boundary for Orbit.

Providers own health, enrichment, and embeddings. The memory pipeline owns
durable Markdown, canonical embedding input, and derived vector persistence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional, Protocol, Type

import httpx
from google.genai import types
from pydantic import BaseModel


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_ENRICHMENT_MODEL = "qwen3:1.7b"
OLLAMA_EMBEDDING_MODEL = "embeddinggemma"
OLLAMA_EMBEDDING_DIMENSIONS = 768


class IntelligenceProvider(Protocol):
    """Everything the capture/memory layer may ask an AI provider to do."""

    name: str
    model: str
    dimensions: int
    enrichment_model: str

    @property
    def available(self) -> bool: ...

    @property
    def enrichment_available(self) -> bool: ...

    async def check_health(self) -> dict: ...

    async def enrich(
        self,
        content: str,
        system_prompt: str,
        schema: Type[BaseModel],
        video_path: Optional[str] = None,
    ) -> BaseModel: ...

    async def embed_document(self, text: str, title: str = "") -> list[float]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        client,
        is_available: Callable[[], bool] = lambda: False,
        model: str = "gemini-embedding-001",
        dimensions: int = 768,
        enrichment_model: str = "gemini-2.5-flash",
        video_model: str = "gemini-2.5-pro",
    ):
        self._client = client
        self._legacy_available = is_available
        self._checked = False
        self._healthy = False
        self.model = model
        self.dimensions = dimensions
        self.enrichment_model = enrichment_model
        self.video_model = video_model
        self._last_health = {
            "provider": self.name,
            "runtime_available": bool(client),
            "embedding_ready": False,
            "enrichment_ready": False,
            "missing_models": [],
            "message": "Gemini API key is not configured." if not client else "Not checked yet.",
        }

    @property
    def available(self) -> bool:
        healthy = self._healthy if self._checked else bool(self._legacy_available())
        return self._client is not None and healthy

    @property
    def enrichment_available(self) -> bool:
        return self.available

    async def check_health(self) -> dict:
        self._checked = True
        if self._client is None:
            self._healthy = False
            self._last_health.update(
                runtime_available=False,
                embedding_ready=False,
                enrichment_ready=False,
                message="Gemini API key is not configured.",
            )
            return dict(self._last_health)
        try:
            await self._client.aio.models.generate_content(
                model=self.enrichment_model,
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=8),
            )
            self._healthy = True
            message = "Connected."
        except Exception as exc:
            # A rate-limited authenticated provider is still configured and can
            # recover per request. Other failures leave local Orbit operational.
            if "429" in str(exc) or "rate" in str(exc).lower():
                self._healthy = True
                message = "Connected, but currently rate limited."
            else:
                self._healthy = False
                message = "Gemini is unavailable. Local capture and keyword Recall still work."
        self._last_health.update(
            runtime_available=True,
            embedding_ready=self._healthy,
            enrichment_ready=self._healthy,
            message=message,
        )
        return dict(self._last_health)

    async def _generate(self, **kwargs):
        last_error = None
        for attempt in range(3):
            try:
                return await self._client.aio.models.generate_content(**kwargs)
            except Exception as exc:
                last_error = exc
                if not ("429" in str(exc) or "rate" in str(exc).lower()) or attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise last_error

    async def enrich(self, content, system_prompt, schema, video_path=None):
        if not self.enrichment_available:
            raise RuntimeError("Gemini is unavailable")
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            system_instruction=system_prompt,
        )
        if video_path and Path(video_path).exists():
            uploaded = await self._client.aio.files.upload(file=video_path)
            try:
                while uploaded.state.name == "PROCESSING":
                    await asyncio.sleep(2)
                    uploaded = await self._client.aio.files.get(name=uploaded.name)
                if uploaded.state.name != "ACTIVE":
                    raise RuntimeError(f"Gemini video processing ended as {uploaded.state.name}")
                response = await self._generate(
                    model=self.video_model,
                    contents=[uploaded, "Analyze this saved video."],
                    config=config,
                )
            finally:
                try:
                    await self._client.aio.files.delete(name=uploaded.name)
                except Exception:
                    pass
        else:
            response = await self._generate(
                model=self.enrichment_model,
                contents=content,
                config=config,
            )
        if getattr(response, "parsed", None) is not None:
            parsed = response.parsed
            return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        return schema.model_validate_json(response.text or "{}")

    async def _embed(self, text: str, task_type: str, title: str = "") -> list[float]:
        if not self.available:
            raise RuntimeError("embedding provider unavailable")
        response = await self._client.aio.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions,
                title=title or None,
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings or not embeddings[0].values:
            raise RuntimeError("embedding provider returned no vector")
        return [float(value) for value in embeddings[0].values]

    async def embed_document(self, text: str, title: str = "") -> list[float]:
        return await self._embed(text, "RETRIEVAL_DOCUMENT", title=title)

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(text, "RETRIEVAL_QUERY")


class OllamaProvider:
    """Local Ollama adapter using its stable HTTP API; no Python SDK needed."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        embedding_model: str = OLLAMA_EMBEDDING_MODEL,
        enrichment_model: str = OLLAMA_ENRICHMENT_MODEL,
        dimensions: int = OLLAMA_EMBEDDING_DIMENSIONS,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = embedding_model
        self.enrichment_model = enrichment_model
        self.dimensions = dimensions
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=3.0),
        )
        self._runtime_available = False
        self._embedding_ready = False
        self._enrichment_ready = False
        self._last_health = self._health("Ollama has not been checked yet.")

    def _health(self, message: str, missing_models: Optional[list[str]] = None) -> dict:
        return {
            "provider": self.name,
            "runtime_available": self._runtime_available,
            "embedding_ready": self._embedding_ready,
            "enrichment_ready": self._enrichment_ready,
            "missing_models": missing_models or [],
            "models": {
                "enrichment": self.enrichment_model,
                "embedding": self.model,
            },
            "message": message,
        }

    @staticmethod
    def _model_present(installed: set[str], wanted: str) -> bool:
        if wanted in installed:
            return True
        wanted_base = wanted.removesuffix(":latest")
        return any(name.removesuffix(":latest") == wanted_base for name in installed)

    @property
    def available(self) -> bool:
        return self._runtime_available and self._embedding_ready

    @property
    def enrichment_available(self) -> bool:
        return self._runtime_available and self._enrichment_ready

    async def check_health(self) -> dict:
        try:
            response = await self._client.get("/api/tags", timeout=3.0)
            response.raise_for_status()
            installed = {
                str(model.get("name") or model.get("model") or "")
                for model in response.json().get("models", [])
            }
            self._runtime_available = True
            self._embedding_ready = self._model_present(installed, self.model)
            self._enrichment_ready = self._model_present(installed, self.enrichment_model)
            missing = [
                model for model, ready in (
                    (self.model, self._embedding_ready),
                    (self.enrichment_model, self._enrichment_ready),
                ) if not ready
            ]
            message = "Local AI is ready." if not missing else "Ollama is running, but Orbit's models need to be downloaded."
            self._last_health = self._health(message, missing)
        except (httpx.HTTPError, ValueError, TypeError):
            self._runtime_available = False
            self._embedding_ready = False
            self._enrichment_ready = False
            self._last_health = self._health(
                "Ollama is not running. Start Ollama, then reopen Orbit."
            )
        return dict(self._last_health)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def enrich(self, content, system_prompt, schema, video_path=None):
        if not self.enrichment_available:
            raise RuntimeError("Ollama enrichment model unavailable")
        response = await self._client.post(
            "/api/generate",
            json={
                "model": self.enrichment_model,
                "prompt": content,
                "system": system_prompt + "\nReturn only JSON matching the supplied schema.",
                "format": schema.model_json_schema(),
                "stream": False,
                "think": False,
                "options": {"temperature": 0},
            },
        )
        response.raise_for_status()
        raw = response.json().get("response", "{}")
        return schema.model_validate_json(raw)

    async def _embed(self, text: str) -> list[float]:
        if not self.available:
            raise RuntimeError("Ollama embedding model unavailable")
        response = await self._client.post(
            "/api/embed",
            json={"model": self.model, "input": text, "dimensions": self.dimensions},
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings") or []
        if not embeddings or not embeddings[0]:
            raise RuntimeError("Ollama returned no embedding")
        vector = [float(value) for value in embeddings[0]]
        if len(vector) != self.dimensions:
            raise RuntimeError(f"Ollama returned {len(vector)} dimensions; expected {self.dimensions}")
        return vector

    @staticmethod
    def prepare_document(text: str, title: str = "") -> str:
        if text.startswith("title: ") and " | text: " in text[:600]:
            return text
        safe_title = " ".join((title or "none").split()) or "none"
        return f"title: {safe_title} | text: {text}"

    @staticmethod
    def prepare_query(text: str) -> str:
        if text.startswith("task: search result | query: "):
            return text
        return f"task: search result | query: {text}"

    async def embed_document(self, text: str, title: str = "") -> list[float]:
        return await self._embed(self.prepare_document(text, title))

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(self.prepare_query(text))


class NoAIProvider:
    name = "none"
    model = "none"
    enrichment_model = "none"
    dimensions = 0
    available = False
    enrichment_available = False

    async def check_health(self) -> dict:
        return {
            "provider": "none", "runtime_available": True,
            "embedding_ready": False, "enrichment_ready": False,
            "missing_models": [], "message": "AI is disabled. Capture and keyword Recall are ready.",
        }

    async def enrich(self, *args, **kwargs):
        raise RuntimeError("AI is disabled")

    async def embed_document(self, *args, **kwargs):
        raise RuntimeError("AI is disabled")

    async def embed_query(self, *args, **kwargs):
        raise RuntimeError("AI is disabled")


# Backward-compatible import used by existing tests and maintenance code.
GeminiEmbeddingProvider = GeminiProvider
