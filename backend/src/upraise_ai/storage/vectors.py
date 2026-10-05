import math

from qdrant_client import QdrantClient, models

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid


class VectorStore:
    def __init__(self, url, collection, dimension):
        self.client = QdrantClient(url=url, timeout=30)
        self.collection = collection
        self.dimension = dimension

    def ensure_collection(self):
        if not self.client.collection_exists(self.collection):
            try:
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(size=self.dimension, distance=models.Distance.COSINE),
                )
            except Exception:
                if not self.client.collection_exists(self.collection):
                    raise
        config = self.client.get_collection(self.collection).config.params.vectors
        if (
            not isinstance(config, models.VectorParams)
            or config.size != self.dimension
            or config.distance != models.Distance.COSINE
        ):
            raise IngestionError("VECTOR_COLLECTION_CONFIGURATION_MISMATCH")
        for key in (
            "document_id", "course_id", "activity_id", "ingestion_token", "language", "source_ref", "quality_status"
        ):
            self.client.create_payload_index(
                self.collection, key, models.PayloadSchemaType.KEYWORD, wait=True
            )

    @staticmethod
    def attempt_filter(document_id, token):
        return models.Filter(
            must=[
                models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))
                for key, value in (("document_id", document_id), ("ingestion_token", token))
            ]
        )

    def write(self, processed, vectors, token):
        chunks = processed.chunks
        if len(chunks) != len(vectors) or not chunks:
            raise IngestionError("EMBEDDING_COUNT_MISMATCH")
        if any(len(v) != self.dimension or any(not math.isfinite(x) for x in v) for v in vectors):
            raise IngestionError("INVALID_EMBEDDINGS")
        self.ensure_collection()
        self.remove_attempt(processed.document_id, token)
        for start in range(0, len(chunks), 64):
            points = []
            for chunk, vector in zip(chunks[start : start + 64], vectors[start : start + 64], strict=True):
                payload = chunk.model_dump(mode="json")
                payload.update(
                    page=chunk.page_numbers[0] if chunk.page_numbers else None,
                    section=" > ".join(chunk.heading_path),
                    content_hash=processed.metadata.content_hash,
                    ingestion_token=str(token),
                    pipeline_version=processed.pipeline_version,
                    embedding_model_id=processed.embedding_model_id,
                    ingestion_status="INDEXED",
                    quality_status=chunk.quality.status.value,
                    requires_review=chunk.quality.status.value == "NEEDS_REVIEW",
                    document_quality_status=processed.quality.status.value,
                )
                # Physical IDs isolate attempts; logical chunk IDs preserve old deterministic behavior.
                points.append(
                    models.PointStruct(
                        id=deterministic_uuid(f"{token}:{chunk.chunk_id}"), vector=vector, payload=payload
                    )
                )
            self.client.upsert(self.collection, points, wait=True)
        count = self.client.count(
            self.collection, count_filter=self.attempt_filter(processed.document_id, token), exact=True
        ).count
        if count != len(chunks):
            raise IngestionError("VECTOR_COUNT_MISMATCH", retryable=True)
        return count

    def remove_attempt(self, document_id, token):
        if self.client.collection_exists(self.collection):
            self.client.delete(
                self.collection,
                models.FilterSelector(filter=self.attempt_filter(document_id, token)),
                wait=True,
            )

    @staticmethod
    def document_filter(document_id, course_id, token=None):
        fields = [("document_id", document_id), ("course_id", course_id)]
        if token is not None:
            fields.append(("ingestion_token", token))
        return models.Filter(must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))
            for key, value in fields
        ])

    def document_count(self, document_id, course_id, token=None):
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(
            self.collection, count_filter=self.document_filter(document_id, course_id, token), exact=True
        ).count

    def document_sections(self, document_id, course_id, token):
        if not self.client.collection_exists(self.collection):
            return []
        result, offset = [], None
        while True:
            points, offset = self.client.scroll(
                self.collection, scroll_filter=self.document_filter(document_id, course_id, token),
                limit=128, offset=offset, with_payload=True, with_vectors=False,
            )
            result.extend(point.payload for point in points)
            if offset is None:
                break
        return sorted(result, key=lambda chunk: (chunk["chunk_index"], chunk["chunk_id"]))

    def remove_document(self, document_id, course_id):
        self.purge_inactive_attempts(document_id, course_id)

    def purge_inactive_attempts(self, document_id, course_id, active_token=None):
        if not self.client.collection_exists(self.collection):
            return
        selector = self.document_filter(document_id, course_id)
        if active_token is not None:
            selector.must_not = [models.FieldCondition(
                key="ingestion_token", match=models.MatchValue(value=str(active_token))
            )]
        self.client.delete(self.collection, models.FilterSelector(filter=selector), wait=True)
        if self.client.count(self.collection, count_filter=selector, exact=True).count:
            raise IngestionError("DOCUMENT_DELETE_INCOMPLETE", retryable=True, status=503)

    def cleanup_old_attempts(self, document_id, token):
        self.client.delete(
            self.collection,
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=str(document_id))
                        )
                    ],
                    must_not=[
                        models.FieldCondition(
                            key="ingestion_token", match=models.MatchValue(value=str(token))
                        )
                    ],
                )
            ),
            wait=True,
        )

    def close(self):
        self.client.close()
