import unittest
from types import SimpleNamespace

from providers import GeminiEmbeddingProvider


class FakeModels:
    def __init__(self):
        self.calls = []

    async def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.25, 0.5, 0.75])]
        )


class GeminiProviderAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_and_query_use_retrieval_tasks(self):
        models = FakeModels()
        client = SimpleNamespace(aio=SimpleNamespace(models=models))
        provider = GeminiEmbeddingProvider(
            client,
            lambda: True,
            model="gemini-embedding-001",
            dimensions=3,
        )

        document = await provider.embed_document("memory text", title="Memory")
        query = await provider.embed_query("vague recollection")

        self.assertEqual(document, [0.25, 0.5, 0.75])
        self.assertEqual(query, [0.25, 0.5, 0.75])
        self.assertEqual(models.calls[0]["config"].task_type, "RETRIEVAL_DOCUMENT")
        self.assertEqual(models.calls[1]["config"].task_type, "RETRIEVAL_QUERY")
        self.assertEqual(models.calls[0]["config"].output_dimensionality, 3)
        self.assertTrue(all(call["model"] == "gemini-embedding-001" for call in models.calls))


if __name__ == "__main__":
    unittest.main()

