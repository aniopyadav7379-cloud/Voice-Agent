"""
Unit tests for app.context.qdrant_provider.QdrantKnowledgeProvider.

No real Qdrant server is used -- the client is a mock, so these tests
exercise this module's own request-shaping and response-mapping logic,
not the Qdrant SDK/server themselves.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import models

from app.context.qdrant_provider import COLLECTION_NAME, KnowledgeResult, QdrantKnowledgeProvider


def make_embedder(vector=None, dimensions=384):
    embedder = MagicMock()
    embedder.dimensions = dimensions
    embedder.embed = AsyncMock(return_value=vector or [0.1] * dimensions)
    return embedder


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing():
    client = AsyncMock()
    client.collection_exists.return_value = False
    embedder = make_embedder(dimensions=384)
    provider = QdrantKnowledgeProvider(client, embedder)

    await provider.ensure_collection()

    client.collection_exists.assert_awaited_once_with(COLLECTION_NAME)
    client.create_collection.assert_awaited_once()
    _, kwargs = client.create_collection.await_args
    assert kwargs["collection_name"] == COLLECTION_NAME
    assert kwargs["vectors_config"].size == 384
    assert kwargs["vectors_config"].distance == models.Distance.COSINE


@pytest.mark.asyncio
async def test_ensure_collection_skips_create_when_present():
    client = AsyncMock()
    client.collection_exists.return_value = True
    provider = QdrantKnowledgeProvider(client, make_embedder())

    await provider.ensure_collection()

    client.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_document_embeds_and_stores_full_payload():
    client = AsyncMock()
    embedder = make_embedder(vector=[0.5, 0.25])
    provider = QdrantKnowledgeProvider(client, embedder)

    await provider.upsert_document(
        tenant_id="tenant-a", text="the pump is offline", knowledge_type="fault_report", document_id="doc-1"
    )

    embedder.embed.assert_awaited_once_with("the pump is offline")
    client.upsert.assert_awaited_once()
    _, kwargs = client.upsert.await_args
    assert kwargs["collection_name"] == COLLECTION_NAME
    point = kwargs["points"][0]
    assert point.vector == [0.5, 0.25]
    assert point.payload["tenant_id"] == "tenant-a"
    assert point.payload["text"] == "the pump is offline"
    assert point.payload["knowledge_type"] == "fault_report"
    assert point.payload["document_id"] == "doc-1"
    assert "ts" in point.payload


@pytest.mark.asyncio
async def test_upsert_document_defaults_document_id_to_none():
    client = AsyncMock()
    provider = QdrantKnowledgeProvider(client, make_embedder())

    await provider.upsert_document(tenant_id="tenant-a", text="hello", knowledge_type="note")

    point = client.upsert.await_args.kwargs["points"][0]
    assert point.payload["document_id"] is None


@pytest.mark.asyncio
async def test_retrieve_filters_by_tenant_only_when_no_knowledge_type():
    client = AsyncMock()
    client.query_points.return_value = MagicMock(points=[])
    provider = QdrantKnowledgeProvider(client, make_embedder())

    await provider.retrieve(tenant_id="tenant-a", query_text="status?")

    _, kwargs = client.query_points.await_args
    must_conditions = kwargs["query_filter"].must
    assert len(must_conditions) == 1
    assert must_conditions[0].key == "tenant_id"
    assert must_conditions[0].match.value == "tenant-a"
    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_retrieve_adds_knowledge_type_filter_when_given():
    client = AsyncMock()
    client.query_points.return_value = MagicMock(points=[])
    provider = QdrantKnowledgeProvider(client, make_embedder())

    await provider.retrieve(tenant_id="tenant-a", query_text="status?", knowledge_type="fault_report", top_k=2)

    _, kwargs = client.query_points.await_args
    must_conditions = kwargs["query_filter"].must
    assert len(must_conditions) == 2
    assert must_conditions[1].key == "knowledge_type"
    assert must_conditions[1].match.value == "fault_report"
    assert kwargs["limit"] == 2


@pytest.mark.asyncio
async def test_retrieve_maps_points_into_knowledge_results():
    client = AsyncMock()
    point = MagicMock(
        payload={"text": "pump 12 is down", "knowledge_type": "fault_report", "document_id": "doc-9"},
        score=0.87,
    )
    client.query_points.return_value = MagicMock(points=[point])
    provider = QdrantKnowledgeProvider(client, make_embedder())

    results = await provider.retrieve(tenant_id="tenant-a", query_text="pump status")

    assert results == [KnowledgeResult(text="pump 12 is down", knowledge_type="fault_report",
                                        document_id="doc-9", score=0.87)]


@pytest.mark.asyncio
async def test_retrieve_skips_points_with_no_payload():
    client = AsyncMock()
    good = MagicMock(payload={"text": "a", "knowledge_type": "note", "document_id": None}, score=0.5)
    bad = MagicMock(payload=None, score=0.9)
    client.query_points.return_value = MagicMock(points=[bad, good])
    provider = QdrantKnowledgeProvider(client, make_embedder())

    results = await provider.retrieve(tenant_id="tenant-a", query_text="q")

    assert len(results) == 1
    assert results[0].text == "a"
    assert results[0].document_id is None
