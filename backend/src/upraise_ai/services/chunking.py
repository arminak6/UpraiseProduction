from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from upraise_ai.core.utils import deterministic_uuid, sha256_text
from upraise_ai.schemas.documents import Chunk, DocumentElement, ElementType, IngestionMetadata


class TokenCodec:
    """Local token estimate with optional tiktoken support."""

    def __init__(self, encoding_name: str | None = None):
        self.encoding_name = encoding_name or "bounded-whitespace-estimate-v2"
        self._encoding = None
        if encoding_name is None:
            return
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding(encoding_name)
        except Exception:
            # Tiktoken may need a one-time remote vocabulary download. Chunking
            # must still work in offline and air-gapped environments.
            self._encoding = None

    @property
    def active_encoding_name(self) -> str:
        return self.encoding_name

    def encode(self, text: str) -> list[int] | list[str]:
        if self._encoding is not None:
            return self._encoding.encode(text)
        return re.findall(r"\S{1,8}\s*|\s+", text)

    def decode(self, tokens: Sequence[int] | Sequence[str]) -> str:
        if self._encoding is not None:
            return self._encoding.decode(list(tokens))  # type: ignore[arg-type]
        return "".join(str(token) for token in tokens)

    def count(self, text: str) -> int:
        return len(self.encode(text))

    def split(
        self,
        text: str,
        *,
        max_tokens: int,
        overlap_tokens: int,
    ) -> list[str]:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive.")
        tokens = self.encode(text)
        if len(tokens) <= max_tokens:
            return [text.strip()] if text.strip() else []

        overlap = max(0, min(overlap_tokens, max_tokens - 1))
        step = max_tokens - overlap
        pieces: list[str] = []
        start = 0
        while start < len(tokens):
            piece = self.decode(tokens[start : start + max_tokens]).strip()
            if piece:
                pieces.append(piece)
            if start + max_tokens >= len(tokens):
                break
            start += step
        return pieces


@dataclass
class _Unit:
    text: str
    elements: list[DocumentElement]
    heading_path: list[str]
    atomic: bool


class StructureAwareChunker:
    name = "structure-aware-v1"

    def __init__(
        self,
        *,
        max_tokens: int = 600,
        overlap_tokens: int = 60,
        token_codec: TokenCodec | None = None,
    ):
        if max_tokens < 100:
            raise ValueError("max_tokens must be at least 100.")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("overlap_tokens must be between 0 and max_tokens.")
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.codec = token_codec or TokenCodec()

    @property
    def config(self) -> dict[str, int | str]:
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "token_encoding": self.codec.active_encoding_name,
        }

    def chunk(
        self,
        elements: Iterable[DocumentElement],
        *,
        metadata: IngestionMetadata,
        document_id: str,
        document_version_id: str,
        source_hash: str,
    ) -> list[Chunk]:
        units = [unit for element in elements if (unit := self._unit(element))]
        chunks: list[Chunk] = []
        buffered_text: list[str] = []
        buffered_elements: list[DocumentElement] = []
        buffered_heading: list[str] = []

        def flush_buffer() -> None:
            nonlocal buffered_text, buffered_elements, buffered_heading
            if not buffered_text:
                return
            content = "\n\n".join(buffered_text).strip()
            self._emit(
                chunks,
                content=content,
                elements=buffered_elements,
                heading_path=buffered_heading,
                metadata=metadata,
                document_id=document_id,
                document_version_id=document_version_id,
                source_hash=source_hash,
            )
            buffered_text = []
            buffered_elements = []
            buffered_heading = []

        for unit in units:
            if unit.atomic:
                flush_buffer()
                self._emit(
                    chunks,
                    content=unit.text,
                    elements=unit.elements,
                    heading_path=unit.heading_path,
                    metadata=metadata,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    source_hash=source_hash,
                )
                continue

            heading_changed = bool(buffered_text) and unit.heading_path != buffered_heading
            if heading_changed:
                flush_buffer()

            candidate_texts = [*buffered_text, unit.text]
            candidate_content = "\n\n".join(candidate_texts)
            candidate_heading = unit.heading_path
            prefix = self._context_prefix(metadata, candidate_heading)
            if buffered_text and self.codec.count(f"{prefix}\n\n{candidate_content}") > self.max_tokens:
                flush_buffer()

            buffered_text.append(unit.text)
            buffered_elements.extend(unit.elements)
            buffered_heading = candidate_heading

        flush_buffer()
        return chunks

    def _emit(
        self,
        chunks: list[Chunk],
        *,
        content: str,
        elements: list[DocumentElement],
        heading_path: list[str],
        metadata: IngestionMetadata,
        document_id: str,
        document_version_id: str,
        source_hash: str,
    ) -> None:
        prefix = self._context_prefix(metadata, heading_path)
        available = self.max_tokens - self.codec.count(prefix) - 2
        if available < 40:
            raise ValueError("Chunk context exceeds the token budget")
        pieces = self.codec.split(
            content,
            max_tokens=available,
            overlap_tokens=min(self.overlap_tokens, max(0, available - 1)),
        )
        for piece in pieces:
            embedding_text = f"{prefix}\n\n{piece}".strip()
            chunk_index = len(chunks)
            content_hash = sha256_text(embedding_text)
            chunk_id = deterministic_uuid(f"{document_version_id}:chunk:{chunk_index}:{content_hash}")
            page_numbers = sorted(
                {
                    page
                    for element in elements
                    for page in (
                        element.page_numbers
                        or ([element.page_number] if element.page_number is not None else [])
                    )
                }
            )
            element_types = list(dict.fromkeys(element.element_type for element in elements))
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    source_hash=source_hash,
                    course_id=metadata.course_id,
                    activity_id=metadata.activity_id,
                    language=metadata.language,
                    source_ref=metadata.source_ref,
                    version=metadata.version,
                    document_title=metadata.document_title,
                    text=piece,
                    embedding_text=embedding_text,
                    token_count=self.codec.count(embedding_text),
                    page_numbers=page_numbers,
                    heading_path=heading_path,
                    element_ids=[element.element_id for element in elements],
                    element_types=element_types,
                )
            )

    @staticmethod
    def _context_prefix(
        metadata: IngestionMetadata,
        heading_path: list[str],
    ) -> str:
        lines = [
            f"Course: {metadata.course_id}",
            f"Activity: {metadata.activity_id}",
            f"Document: {metadata.document_title}",
        ]
        if heading_path:
            lines.append(f"Section: {' > '.join(heading_path[-4:])}")
        return "\n".join(lines)

    @staticmethod
    def _unit(element: DocumentElement) -> _Unit | None:
        if element.element_type in {
            ElementType.HEADER,
            ElementType.FOOTER,
        }:
            return None

        if element.element_type in {
            ElementType.TITLE,
            ElementType.HEADING,
        }:
            return None

        if element.element_type is ElementType.TABLE:
            parts = ["[Table]"]
            if element.caption:
                parts.append(f"Caption: {element.caption}")
            table_text = element.original_text.strip()
            if table_text:
                parts.append(table_text)
            elif element.structured_content:
                parts.append(element.structured_content)
            return _Unit(
                text="\n".join(parts),
                elements=[element],
                heading_path=element.heading_path,
                atomic=True,
            )

        if element.element_type is ElementType.FIGURE:
            parts = ["[Figure]"]
            if element.caption:
                parts.append(f"Caption: {element.caption}")
            if element.derived_description:
                parts.append(f"Description: {element.derived_description}")
            elif element.original_text:
                parts.append(element.original_text)
            if len(parts) == 1:
                return None
            return _Unit(
                text="\n".join(parts),
                elements=[element],
                heading_path=element.heading_path,
                atomic=True,
            )

        if element.element_type is ElementType.FORMULA:
            if not element.original_text.strip():
                return None
            return _Unit(
                text=f"[Formula]\n{element.original_text.strip()}",
                elements=[element],
                heading_path=element.heading_path,
                atomic=True,
            )

        if element.element_type is ElementType.CODE:
            if not element.original_text.strip():
                return None
            return _Unit(
                text=f"[Code]\n{element.original_text.strip()}",
                elements=[element],
                heading_path=element.heading_path,
                atomic=True,
            )

        text = element.original_text.strip()
        if not text:
            return None
        return _Unit(
            text=text,
            elements=[element],
            heading_path=element.heading_path,
            atomic=False,
        )
