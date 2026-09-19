"""
Unit tests for app.context.moss_provider.MossContextProvider.

The real Moss SDK client is patched out entirely -- these tests exercise
this module's own tenant-scoping, index-caching, and error-wrapping logic,
never a live Moss project.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.context.moss_provider import MossContextProvider, MossUnavailableError


@pytest.fixture
def mock_client():
    with patch("app.context.moss_provider.MossClient") as MockClient:
        instance = MockClient.return_value
        instance.load_index = AsyncMock()
        instance.query = AsyncMock()
        instance.create_index = AsyncMock()
        instance.add_docs = AsyncMock()
        instance.delete_docs = AsyncMock()
        yield instance


def make_provider(mock_client) -> MossContextProvider:
    return MossContextProvider(project_id="proj-1", project_key="key-1")


def test_index_name_is_tenant_scoped(mock_client):
    assert MossContextProvider._index_name("tenant-a", "session_context") == "tenant-a__session_context"


@pytest.mark.asyncio
async def test_retrieve_context_loads_index_then_queries(mock_client):
    doc = MagicMock(text="the pump is offline", score=0.9, metadata={"source": "ticket-1"})
    mock_client.query.return_value = MagicMock(docs=[doc])
    provider = make_provider(mock_client)

    results = await provider.retrieve_context(
        tenant_id="tenant-a", logical_name="session_context", query_text="pump status", top_k=3
    )

    mock_client.load_index.assert_awaited_once_with("tenant-a__session_context")
    mock_client.query.assert_awaited_once()
    args, _ = mock_client.query.await_args
    assert args[0] == "tenant-a__session_context"
    assert args[1] == "pump status"
    assert args[2].top_k == 3
    assert results == [{"text": "the pump is offline", "score": 0.9, "metadata": {"source": "ticket-1"}}]


@pytest.mark.asyncio
async def test_retrieve_context_handles_missing_score_and_metadata(mock_client):
    doc = MagicMock(text="hello", spec=["text"])  # no .score / .metadata attributes
    mock_client.query.return_value = MagicMock(docs=[doc])
    provider = make_provider(mock_client)

    results = await provider.retrieve_context(tenant_id="t", logical_name="l", query_text="q")

    assert results == [{"text": "hello", "score": None, "metadata": None}]


@pytest.mark.asyncio
async def test_retrieve_context_handles_no_docs(mock_client):
    mock_client.query.return_value = MagicMock(docs=None)
    provider = make_provider(mock_client)

    results = await provider.retrieve_context(tenant_id="t", logical_name="l", query_text="q")

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_context_caches_loaded_index_across_calls(mock_client):
    mock_client.query.return_value = MagicMock(docs=[])
    provider = make_provider(mock_client)

    await provider.retrieve_context(tenant_id="tenant-a", logical_name="session_context", query_text="q1")
    await provider.retrieve_context(tenant_id="tenant-a", logical_name="session_context", query_text="q2")

    mock_client.load_index.assert_awaited_once()  # not reloaded the second time
    assert mock_client.query.await_count == 2


@pytest.mark.asyncio
async def test_retrieve_context_different_tenants_load_separate_indexes(mock_client):
    mock_client.query.return_value = MagicMock(docs=[])
    provider = make_provider(mock_client)

    await provider.retrieve_context(tenant_id="tenant-a", logical_name="session_context", query_text="q")
    await provider.retrieve_context(tenant_id="tenant-b", logical_name="session_context", query_text="q")

    assert mock_client.load_index.await_count == 2
    loaded = {call.args[0] for call in mock_client.load_index.await_args_list}
    assert loaded == {"tenant-a__session_context", "tenant-b__session_context"}


@pytest.mark.asyncio
async def test_retrieve_context_wraps_load_failure(mock_client):
    mock_client.load_index.side_effect = RuntimeError("index not found")
    provider = make_provider(mock_client)

    with pytest.raises(MossUnavailableError):
        await provider.retrieve_context(tenant_id="t", logical_name="l", query_text="q")

    mock_client.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieve_context_wraps_query_failure(mock_client):
    mock_client.query.side_effect = RuntimeError("network error")
    provider = make_provider(mock_client)

    with pytest.raises(MossUnavailableError, match="tenant-a"):
        await provider.retrieve_context(tenant_id="tenant-a", logical_name="l", query_text="q")


@pytest.mark.asyncio
async def test_upsert_context_creates_index_with_document_info(mock_client):
    provider = make_provider(mock_client)
    docs = [{"id": "1", "text": "doc one", "metadata": {"a": "1"}, "embedding": [0.1], "payload": '{"p": 1}'}]

    await provider.upsert_context(tenant_id="tenant-a", logical_name="kb", docs=docs)

    mock_client.create_index.assert_awaited_once()
    args, _ = mock_client.create_index.await_args
    assert args[0] == "tenant-a__kb"
    moss_docs = args[1]
    assert moss_docs[0].id == "1"
    assert moss_docs[0].text == "doc one"
    mock_client.add_docs.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_context_falls_back_to_add_docs_when_create_fails(mock_client):
    mock_client.create_index.side_effect = RuntimeError("index already exists")
    provider = make_provider(mock_client)
    docs = [{"id": "1", "text": "doc one"}]

    await provider.upsert_context(tenant_id="tenant-a", logical_name="kb", docs=docs)

    mock_client.add_docs.assert_awaited_once()
    args, _ = mock_client.add_docs.await_args
    assert args[0] == "tenant-a__kb"


@pytest.mark.asyncio
async def test_delete_context_returns_doc_count(mock_client):
    mock_client.delete_docs.return_value = MagicMock(doc_count=3)
    provider = make_provider(mock_client)

    deleted = await provider.delete_context(tenant_id="tenant-a", logical_name="kb", doc_ids=["1", "2", "3"])

    mock_client.delete_docs.assert_awaited_once_with("tenant-a__kb", ["1", "2", "3"])
    assert deleted == 3
