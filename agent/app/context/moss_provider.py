"""
Thin abstraction over the real `moss` SDK.

Per-tenant isolation: Moss indexes are namespaced by tenant at the
index-name level (`{tenant_id}__{logical_index_name}`).

The provider never accepts a raw index name from callers. Every public
method takes a tenant_id and derives the Moss index name internally.

The Moss SDK calls are based on the verified installed SDK surface.
Live Moss connectivity is environment-dependent and must be verified
separately against a configured Moss project.
"""

import logging

from moss import DocumentInfo, MossClient, QueryOptions

logger = logging.getLogger("agent.moss")


class MossUnavailableError(Exception):
    """Raised when Moss cannot service a context operation.

    Context callers should treat this as a degraded/unavailable fast
    context path rather than failing the complete conversation turn.
    """


class MossContextProvider:
    def __init__(self, project_id: str, project_key: str):
        self._client = MossClient(project_id, project_key)
        self._loaded_indexes: set[str] = set()
        self._failed_indexes: set[str] = set()

    @staticmethod
    def _index_name(tenant_id: str, logical_name: str) -> str:
        """Derive the tenant-scoped Moss index name."""
        return f"{tenant_id}__{logical_name}"

    async def _ensure_loaded(
        self,
        tenant_id: str,
        logical_name: str,
    ) -> str:
        """Load a tenant-scoped Moss index once per provider instance."""
        index_name = self._index_name(tenant_id, logical_name)

        if index_name in self._failed_indexes:
            raise MossUnavailableError(
                f"Moss index {index_name} previously failed to load"
            )

        if index_name not in self._loaded_indexes:
            try:
                await self._client.load_index(index_name)
                self._loaded_indexes.add(index_name)
            except Exception as e:
                self._failed_indexes.add(index_name)
                raise MossUnavailableError(
                    f"failed to load Moss index {index_name}"
                ) from e

        return index_name

    async def retrieve_context(
        self,
        *,
        tenant_id: str,
        logical_name: str,
        query_text: str,
        top_k: int = 3,
    ) -> list[dict]:
        """Retrieve fast in-session context from Moss.

        If Moss is unavailable, raise MossUnavailableError so the
        orchestrator can degrade gracefully.
        """
        try:
            index_name = await self._ensure_loaded(
                tenant_id,
                logical_name,
            )

            results = await self._client.query(
                index_name,
                query_text,
                QueryOptions(top_k=top_k),
            )

            return [
                {
                    "text": doc.text,
                    "score": getattr(doc, "score", None),
                    "metadata": getattr(doc, "metadata", None),
                }
                for doc in (results.docs or [])
            ]

        except MossUnavailableError:
            raise
        except Exception as e:
            raise MossUnavailableError(
                f"Moss query failed for tenant {tenant_id}"
            ) from e

    async def upsert_context(
        self,
        *,
        tenant_id: str,
        logical_name: str,
        docs: list[dict],
    ) -> None:
        """Create or update a tenant-scoped Moss index."""
        index_name = self._index_name(
            tenant_id,
            logical_name,
        )

        moss_docs = [
            DocumentInfo(
                id=str(doc["id"]),
                text=str(doc["text"]),
                metadata=doc.get("metadata"),
                embedding=doc.get("embedding"),
                payload=doc.get("payload"),
            )
            for doc in docs
        ]

        try:
            await self._client.create_index(
                index_name,
                moss_docs,
            )
        except Exception:
            # Index may already exist; fall back to adding documents.
            await self._client.add_docs(
                index_name,
                moss_docs,
            )

    async def delete_context(
        self,
        *,
        tenant_id: str,
        logical_name: str,
        doc_ids: list[str],
    ) -> int:
        """Delete documents from a tenant-scoped Moss index."""
        index_name = self._index_name(
            tenant_id,
            logical_name,
        )

        result = await self._client.delete_docs(
            index_name,
            doc_ids,
        )

        return int(result.doc_count)
