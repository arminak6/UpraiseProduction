from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import boto3
from botocore.config import Config


class BedrockTitanEmbeddingProvider:
    def __init__(
        self,
        *,
        session: boto3.Session,
        model_id: str = "amazon.titan-embed-text-v2:0",
        dimension: int = 1024,
        client: Any | None = None,
    ):
        if dimension not in {256, 512, 1024}:
            raise ValueError("Titan Text Embeddings V2 supports 256, 512, or 1024.")
        self.session = session
        self.model_id = model_id
        self.dimension = dimension
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self.session.client(
                "bedrock-runtime",
                config=Config(
                    retries={"max_attempts": 6, "mode": "adaptive"},
                    connect_timeout=10,
                    read_timeout=60,
                ),
            )
        return self._client

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._embed_one(query)

    def _embed_one(self, text: str) -> list[float]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot embed blank text.")
        if len(cleaned) > 50_000:
            raise ValueError("Embedding input exceeds Titan character limit")

        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(
                {
                    "inputText": cleaned,
                    "dimensions": self.dimension,
                    "normalize": True,
                    "embeddingTypes": ["float"],
                }
            ),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding")
        if vector is None:
            vector = payload.get("embeddingsByType", {}).get("float")
        if not isinstance(vector, list) or len(vector) != self.dimension:
            raise RuntimeError("Titan returned an embedding with an unexpected dimension.")
        return [float(value) for value in vector]
