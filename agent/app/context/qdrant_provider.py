import time
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models


COLLECTION_NAME = "deep_knowledge"


@dataclass
class KnowledgeResult:
    text: str
    knowledge_type: str
    document_id: str | None
    score: float


class QdrantKnowledgeProvider:
    def __init__(self, client: AsyncQdrantClient, embedder: Any):
        self._client = client
        self._embedder = embedder

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(COLLECTION_NAME):
            await self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=self._embedder.dimensions,
                    distance=models.Distance.COSINE,
                ),
            )

    async def upsert_document(
        self,
        *,
        tenant_id: str,
        text: str,
        knowledge_type: str,
        document_id: str | None = None,
    ) -> None:
        vector = await self._embedder.embed(text)

        await self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "tenant_id": tenant_id,
                        "text": text,
                        "knowledge_type": knowledge_type,
                        "document_id": document_id,
                        "ts": time.time(),
                    },
                )
            ],
        )

    async def retrieve(
        self,
        *,
        tenant_id: str,
        query_text: str,
        knowledge_type: str | None = None,
        top_k: int = 5,
    ) -> list[KnowledgeResult]:
        query_vector = await self._embedder.embed(query_text)

        must: list[Any] = [
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=tenant_id),
            )
        ]

        if knowledge_type:
            must.append(
                models.FieldCondition(
                    key="knowledge_type",
                    match=models.MatchValue(value=knowledge_type),
                )
            )

        results = await self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=models.Filter(must=must),
            limit=top_k,
        )

        output: list[KnowledgeResult] = []

        for point in results.points:
            if point.payload is None:
                continue

            output.append(
                KnowledgeResult(
                    text=str(point.payload["text"]),
                    knowledge_type=str(point.payload["knowledge_type"]),
                    document_id=(
                        str(point.payload["document_id"])
                        if point.payload.get("document_id") is not None
                        else None
                    ),
                    score=float(point.score),
                )
            )

        return output
