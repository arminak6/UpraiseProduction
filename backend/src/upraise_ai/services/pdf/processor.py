import json
import os
from pathlib import Path

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid, sha256_file
from upraise_ai.schemas.documents import ProcessedDocument
from upraise_ai.services.aws import aws_session
from upraise_ai.services.chunking import StructureAwareChunker
from upraise_ai.services.embeddings import BedrockTitanEmbeddingProvider

from .enrichment import PdfEnricher
from .input import validate_pdf_input
from .parser import DoclingPdfParser
from .vision import BedrockImageDescriber


class PdfProcessor:
    def __init__(self, settings, *, parser=None, embedder=None, enricher=None):
        self.settings = settings
        self.parser = parser
        self.embedder = embedder
        self.enricher = enricher

    def prepare(self, source: Path, metadata, document_id, job_dir, token, progress):
        settings = self.settings
        validate_pdf_input(source, settings.max_pdf_size_mb * 1024**2, settings.max_pdf_pages)
        source_hash = sha256_file(source)
        checkpoint = job_dir / "prepared.json"
        if checkpoint.is_file():
            try:
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                processed = ProcessedDocument.model_validate(payload["document"])
                if (
                    processed.document_id == document_id
                    and processed.metadata == metadata
                    and processed.pipeline_version == settings.pipeline_signature
                ):
                    progress("store", 0.93, "Resuming prepared document.")
                    return processed, payload["vectors"]
            except (OSError, ValueError, KeyError):
                pass
        artifact_dir = job_dir / "attempts" / str(token)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        parser = self.parser or DoclingPdfParser(settings, metadata.language)
        progress("parse", 0.1, "Extracting PDF.")
        parsed = parser.parse(
            source, source_hash=source_hash, artifact_dir=artifact_dir, progress=progress
        )
        session = None
        if self.enricher is None or self.embedder is None:
            session = aws_session(settings)
        enricher = self.enricher or PdfEnricher(
            settings,
            BedrockImageDescriber(
                session=session, model_id=settings.vision_model_id, cache_dir=artifact_dir / "cache"
            ),
            BedrockImageDescriber(
                session=session, model_id=settings.math_vision_model_id, cache_dir=artifact_dir / "cache"
            ),
        )
        warnings = enricher.enrich(parsed, progress)
        progress("chunk", 0.64, "Creating structure-aware chunks.")
        version_id = deterministic_uuid(
            f"{document_id}:version:{source_hash}:{settings.pipeline_signature}"
        )
        chunks = StructureAwareChunker(
            max_tokens=settings.chunk_max_tokens, overlap_tokens=settings.chunk_overlap_tokens
        ).chunk(
            parsed.elements,
            metadata=metadata,
            document_id=document_id,
            document_version_id=version_id,
            source_hash=source_hash,
        )
        if not chunks:
            raise IngestionError("NO_RETRIEVABLE_CONTENT")
        embedder = self.embedder or BedrockTitanEmbeddingProvider(
            session=session, model_id=settings.embedding_model_id, dimension=settings.embedding_dimension
        )
        vectors = []
        for start in range(0, len(chunks), 8):
            progress("embed", 0.7 + 0.2 * start / len(chunks), "Embedding chunks.")
            vectors.extend(embedder.embed_texts([c.embedding_text for c in chunks[start : start + 8]]))
            progress("embed", 0.7 + 0.2 * min(start + 8, len(chunks)) / len(chunks), "Embedding chunks.")
        processed = ProcessedDocument(
            document_id=document_id,
            document_version_id=version_id,
            source_hash=source_hash,
            metadata=metadata,
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            pipeline_version=settings.pipeline_signature,
            embedding_model_id=settings.embedding_model_id,
            embedding_dimension=settings.embedding_dimension,
            page_count=parsed.page_count,
            elements=parsed.elements,
            chunks=chunks,
            warnings=warnings,
        )
        temporary = checkpoint.with_name(f"prepared.{token}.part")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {"document": processed.model_dump(mode="json"), "vectors": vectors},
                stream,
                ensure_ascii=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(checkpoint)
        return processed, vectors
