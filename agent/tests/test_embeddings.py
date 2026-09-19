"""
Unit tests for app.context.embeddings.SentenceTransformerEmbedder.

The real sentence-transformers model is never loaded here -- these tests
stub out `sentence_transformers.SentenceTransformer` entirely, so they
exercise only this module's own logic (lazy import, the "query: " prefix
convention, and the executor-based async wrapping), not model behavior.
"""
import sys
import types

import pytest

from app.context.embeddings import SentenceTransformerEmbedder, _DIMENSIONS, _MODEL_NAME


@pytest.fixture
def stub_sentence_transformers(monkeypatch):
    """Installs a fake `sentence_transformers` module in sys.modules so the
    embedder's lazy `from sentence_transformers import SentenceTransformer`
    succeeds without the real (heavy) package or a model download."""
    fake_module = types.ModuleType("sentence_transformers")
    constructed_with: list[str] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str):
            constructed_with.append(model_name)

        def encode(self, text: str):
            recorded_encode_calls.append(text)
            return FakeVector([0.1, 0.2, 0.3])

    class FakeVector(list):
        def tolist(self):
            return list(self)

    recorded_encode_calls: list[str] = []

    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    return types.SimpleNamespace(
        constructed_with=constructed_with,
        encode_calls=recorded_encode_calls,
    )


def test_module_import_does_not_require_sentence_transformers():
    """Importing the module (already done at the top of this file) must not
    have required sentence-transformers to be installed -- the import is
    lazy, inside __init__, per the module's own docstring."""
    assert SentenceTransformerEmbedder.dimensions == _DIMENSIONS == 384
    assert SentenceTransformerEmbedder.name == "multilingual-e5-small"


def test_init_default_model_name(stub_sentence_transformers):
    SentenceTransformerEmbedder()
    assert stub_sentence_transformers.constructed_with == [_MODEL_NAME]


def test_init_custom_model_name(stub_sentence_transformers):
    SentenceTransformerEmbedder(model_name="some/other-model")
    assert stub_sentence_transformers.constructed_with == ["some/other-model"]


@pytest.mark.asyncio
async def test_embed_applies_query_prefix_and_returns_list(stub_sentence_transformers):
    embedder = SentenceTransformerEmbedder()

    vector = await embedder.embed("where is the nearest technician")

    assert stub_sentence_transformers.encode_calls == ["query: where is the nearest technician"]
    assert vector == [0.1, 0.2, 0.3]
    assert isinstance(vector, list)


@pytest.mark.asyncio
async def test_embed_runs_encode_via_executor_not_inline(stub_sentence_transformers, monkeypatch):
    """embed() must go through loop.run_in_executor rather than calling
    .encode() inline, so a slow model load never blocks the event loop."""
    import asyncio

    embedder = SentenceTransformerEmbedder()
    real_loop = asyncio.get_event_loop()
    calls = {"count": 0}
    original_run_in_executor = real_loop.run_in_executor

    def spying_run_in_executor(executor, func, *args):
        calls["count"] += 1
        return original_run_in_executor(executor, func, *args)

    monkeypatch.setattr(real_loop, "run_in_executor", spying_run_in_executor)

    await embedder.embed("hello")

    assert calls["count"] == 1
