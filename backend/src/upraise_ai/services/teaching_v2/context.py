"""Bounded clarifying references from already authorized, version-pinned sources."""

import re
from collections import Counter
from math import log, sqrt

from upraise_ai.services.source_references import citation_chunks
from upraise_ai.services.v3.polly_speech import _MATH_PATTERN

MAX_CONTEXT_CHUNKS = 6
MAX_CONTEXT_CHARACTERS = 12000


def factual_chunk(chunk):
    return {key: chunk[key] for key in ("chunk_id", "text", "page_numbers", "heading_path") if key in chunk}


def terms(text):
    # Unicode words and mathematical symbols; frequency weighting suppresses
    # common words without a subject-specific or language-specific stop list.
    return set(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def identifiers(text):
    return {token.replace("_", "") for token in re.findall(r"[^\W_]+(?:_[^\W_]+)?", text, re.UNICODE)}


def notation(text):
    # Case-sensitive short/subscripted identifiers from source math delimiters
    # prioritize definitions over repeated headings. No symbol meaning is assumed.
    return {token.replace("_", "") for expression in _MATH_PATTERN.finditer(text)
            for token in re.findall(r"[^\W_]+(?:_[^\W_]+)?", expression.group(), re.UNICODE)
            if len(token) <= 2 or "_" in token}


class DocumentContext:
    def __init__(self, documents):
        self.documents = {}
        for row, chunks in documents:
            document_id = str(row["document_id"])
            for chunk in citation_chunks(chunks):
                if (str(chunk.get("document_id")) != document_id
                        or (chunk.get("ingestion_token") and str(chunk["ingestion_token"]) != str(row.get("active_attempt")))):
                    continue
                key = (document_id, str(chunk.get("document_version_id")))
                reference = {**factual_chunk(chunk), "quality_status": chunk.get("quality", {}).get("status", "UNVERIFIED")}
                self.documents.setdefault(key, []).append(reference)
        self.tokens = {key: [terms(chunk["text"]) for chunk in pool] for key, pool in self.documents.items()}
        self.frequencies = {key: Counter(token for tokens in values for token in tokens)
                            for key, values in self.tokens.items()}
        self.identifiers = {key: [identifiers(chunk["text"]) for chunk in pool] for key, pool in self.documents.items()}
        self.selected = {}

    def select(self, section):
        key = (str(section.get("document_id")), str(section.get("document_version_id")))
        pool = self.documents.get(key, [])
        if not pool:
            return []
        primary_ids = {chunk.get("chunk_id") for chunk in section["chunks"]}
        positions = [i for i, chunk in enumerate(pool) if chunk.get("chunk_id") in primary_ids]
        if not positions:
            return []
        cache_key = (key, tuple(positions))
        if cache_key in self.selected:
            return self.selected[cache_key]
        token_sets = self.tokens[key]
        query = set().union(*(token_sets[index] for index in positions))
        frequency = self.frequencies[key]
        anchors = set().union(*(notation(pool[index]["text"]) for index in positions))

        def relevance(index):
            overlap = query & token_sets[index]
            return sum(1 + log((len(pool) + 1) / (frequency[token] + 1)) for token in overlap) / sqrt(
                max(1, len(token_sets[index])))

        neighbours = [min(positions) - 1, max(positions) + 1]
        ranked = sorted(range(len(pool)), key=lambda i: (
            -len(anchors & self.identifiers[key][i]), -relevance(i), abs(i - positions[0]), i))
        selected, seen, count = [], set(primary_ids), 0
        for index in [*neighbours, *ranked]:
            if not 0 <= index < len(pool):
                continue
            chunk = pool[index]
            identity = chunk.get("chunk_id")
            size = len(chunk["text"])
            if identity in seen or (not query.intersection(token_sets[index]) and index not in neighbours):
                continue
            # Whole chunks only: never truncate a definition or equation.
            if count + size > MAX_CONTEXT_CHARACTERS:
                continue
            selected.append(chunk)
            seen.add(identity)
            count += size
            if len(selected) == MAX_CONTEXT_CHUNKS:
                break
        self.selected[cache_key] = selected
        return selected
