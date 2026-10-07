"""Ordered source windows, source-bound checkpoints and temporary lesson artifacts."""

import hashlib
import json
import os
from uuid import NAMESPACE_URL, uuid5

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import (
    LessonChunk,
    LessonInput,
    LessonResult,
    LessonSource,
    ReviewIssue,
    ReviewReport,
    SectionDraft,
)
from upraise_ai.services.lesson_models import (
    DRAFT_PROMPT,
    FINALIZER_PROMPT,
    MATH_PROMPT,
    REVIEW_PROMPT,
    VISUAL_PROMPT,
    merge_reviews,
)
from upraise_ai.services.lesson_prompts import LEARNER_LEVEL_PROMPT_VERSION
from upraise_ai.services.lesson_speech import split_words
from upraise_ai.services.lesson_translation import TRANSLATION_PROMPT_VERSION, needs_translation


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name != "nt":
        for directory in (path.parent, path.parent.parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False).encode())


def plan_sections(documents, window_pages):
    sections = []
    for row, chunks in documents:
        seen = set()
        source_language = row["metadata"].get("language")
        if not isinstance(source_language, str) or not source_language.strip():
            raise IngestionError("LESSON_SOURCE_LANGUAGE_MISSING")
        page_count = int(row["result"]["page_count"])
        for start in range(1, page_count+1, window_pages):
            end = min(page_count, start+window_pages-1)
            selected = [chunk for chunk in chunks if chunk["chunk_id"] not in seen
                        and any(start <= page <= end for page in chunk["page_numbers"])]
            if not selected:
                continue
            seen.update(chunk["chunk_id"] for chunk in selected)
            pages = sorted({page for chunk in selected for page in chunk["page_numbers"]})
            if any(page < 1 or page > page_count for page in pages):
                raise IngestionError("LESSON_SOURCE_PAGES_INVALID")
            sections.append({"document_id": str(row["document_id"]),
                             "document_job_id": str(row["job_id"]),
                             "page_count": page_count,
                             "source_language": source_language.lower(),
                             "document_version_id": selected[0]["document_version_id"],
                             "active_attempt": str(row["active_attempt"]),
                             "window": [start, end], "pages": pages, "chunks": selected})
        if len(seen) != len(chunks):
            raise IngestionError("LESSON_SOURCE_COVERAGE_INVALID")
    return sections


def validate_draft(draft, pages):
    allowed = set(pages)
    for paragraph in draft.paragraphs:
        if not paragraph.text.strip() or not set(paragraph.pages).issubset(allowed) or any(
            isinstance(page, bool) or page < 1 for page in paragraph.pages
        ):
            raise IngestionError("LESSON_CITATIONS_INVALID", retryable=True)


def source_issues(section):
    issues = []
    for chunk in section["chunks"]:
        quality = chunk.get("quality", {})
        if quality.get("status", "UNVERIFIED") != "AUTO_CHECKED":
            issues.append(ReviewIssue(
                category="source", pages=chunk["page_numbers"],
                message=f"Source chunk {chunk['chunk_id']} has extraction quality "
                        f"{quality.get('status', 'UNVERIFIED')}; automated review is not human verification.",
            ))
    return issues


def segment(draft, target):
    chunks, texts, pages, count = [], [], set(), 0
    for paragraph in draft.paragraphs:
        for part in split_words(paragraph.text, target):
            size = len(part.split())
            if texts and count+size > target*1.25:
                chunks.append(("\n\n".join(texts), sorted(pages)))
                texts, pages, count = [], set(), 0
            texts.append(part)
            pages.update(paragraph.pages)
            count += size
    if texts:
        chunks.append(("\n\n".join(texts), sorted(pages)))
    return chunks


class LessonPipeline:
    def __init__(self, settings, documents, vectors, staging, models, speech):
        self.settings, self.documents, self.vectors, self.staging = settings, documents, vectors, staging
        self.models, self.speech = models, speech

    def load_documents(self, job):
        data = LessonInput.model_validate(job["metadata"])
        rows, waiting = [], False
        for document_id in data.document_ids:
            with self.documents.document_transaction(str(document_id)) as (_, row):
                if (not row or row["deletion_requested_at"] or row["client_id"] != job["client_id"]
                        or row["metadata"]["course_id"] != data.course_id):
                    raise IngestionError("LESSON_DOCUMENT_UNAVAILABLE")
                if row["status"] in {"FAILED", "CANCELLED"}:
                    raise IngestionError("LESSON_DOCUMENT_INGESTION_FAILED")
                if row["status"] != "DONE" or not row["active_attempt"]:
                    waiting = True
                rows.append(row)
        if waiting:
            return None
        result = []
        for row in rows:
            chunks = self.vectors.document_sections(str(row["document_id"]), data.course_id, row["active_attempt"])
            if not chunks or len(chunks) != row["result"]["chunk_count"]:
                raise IngestionError("LESSON_SOURCE_INDEX_INCOMPLETE", retryable=True)
            path = self.staging.source_path(str(row["job_id"]))
            if path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if any(chunk["source_hash"] != digest for chunk in chunks):
                    raise IngestionError("LESSON_SOURCE_HASH_MISMATCH")
            result.append((row, chunks))
        return result

    def check_sources(self, job, expected=None, conn=None):
        """Recheck authoritative activation before publication or delivery."""
        for document_id in job["metadata"]["document_ids"]:
            if conn is not None:
                row = conn.execute("SELECT * FROM ingestion_jobs WHERE document_id=%s FOR SHARE", (document_id,)).fetchone()
            else:
                with self.documents.document_transaction(document_id) as (_, source_row):
                    row = source_row
            if (not row or row["client_id"] != job["client_id"] or row["deletion_requested_at"]
                    or row["status"] != "DONE" or not row["active_attempt"]
                    or row["metadata"]["course_id"] != job["metadata"]["course_id"]
                    or (expected and str(row["active_attempt"]) != expected[document_id])):
                raise IngestionError("LESSON_DOCUMENT_UNAVAILABLE", status=409)

    def prepare(self, job, documents, progress, *, save_text, save_speech):
        data = LessonInput.model_validate(job["metadata"])
        job_id = str(job["job_id"])
        folder = self.staging.job_dir(job_id)
        sections = plan_sections(documents, data.options.source_window_pages)
        if not sections:
            raise IngestionError("LESSON_SOURCE_EMPTY")
        progress.set_workload("lesson", len(sections))
        code = self.speech.validate_voice(data.voice, data.language)
        signature = hashlib.sha256(json.dumps({"sections": sections, "request": job["metadata"],
                                               "pipeline": job["pipeline_version"]}, sort_keys=True).encode()).hexdigest()
        all_chunks, reports = [], []
        for index, section in enumerate(sections):
            progress("GENERATE", 0.05+0.55*index/len(sections))
            # Do not truncate long source windows and pretend all pages were covered.
            if len(json.dumps(section, ensure_ascii=False)) > self.settings.lesson_max_source_characters:
                raise IngestionError("LESSON_SOURCE_WINDOW_TOO_LARGE")
            checkpoint = folder / f"section-{index}.json"
            cached = json.loads(checkpoint.read_text()) if checkpoint.is_file() else None
            if cached and cached.get("signature") == signature:
                draft = SectionDraft.model_validate(cached["draft"])
                report = ReviewReport.model_validate(cached["review"])
                revisions = cached["revisions"]
            else:
                # Retain the finalized source-language draft separately so a
                # translation/provider retry does not repeat the three agents.
                source_checkpoint = folder / f"source-section-{index}.json"
                source_cached = json.loads(source_checkpoint.read_text()) if source_checkpoint.is_file() else None
                if source_cached and source_cached.get("signature") == signature:
                    draft = SectionDraft.model_validate(source_cached["draft"])
                    report = ReviewReport.model_validate(source_cached["review"])
                    revisions = source_cached["revisions"]
                else:
                    source_path = self.staging.source_path(section["document_job_id"])
                    draft, report, revisions = self.models.generate_section(section, section["source_language"], source_path,
                        learner_level=data.options.learner_level,
                        notify=lambda step: progress(step, 0.05+0.55*index/len(sections)))
                    validate_draft(draft, section["pages"])
                    flags = source_issues(section)
                    report = ReviewReport(approved=report.approved and not flags, summary=report.summary,
                                          issues=[*report.issues, *flags], revision_instructions=report.revision_instructions)
                    write_json(source_checkpoint, {"signature": signature, "draft": draft.model_dump(),
                                                   "review": report.model_dump(), "revisions": revisions})
                if needs_translation(section["source_language"], data.language):
                    progress("TRANSLATE", 0.05+0.55*index/len(sections))
                    draft = self.models.translate_section(draft, section["source_language"], data.language,
                                                          learner_level=data.options.learner_level)
                validate_draft(draft, section["pages"])
                write_json(checkpoint, {"signature": signature, "draft": draft.model_dump(),
                                        "review": report.model_dump(), "revisions": revisions})
            validate_draft(draft, section["pages"])
            reports.append(report)
            for text, pages in segment(draft, data.options.target_chunk_words):
                chunk_index = len(all_chunks)
                chunk_id = str(uuid5(NAMESPACE_URL, f"upraise:lesson:{job_id}:{chunk_index}"))
                source_chunks = [chunk for chunk in section["chunks"] if set(chunk["page_numbers"]) & set(pages)]
                source = LessonSource(document_id=section["document_id"],
                                      document_version_id=section["document_version_id"], pages=pages,
                                      source_chunk_ids=[chunk["chunk_id"] for chunk in source_chunks],
                                      quality_status="NEEDS_REVIEW" if any(source_issues(section)) else "AUTO_CHECKED")
                all_chunks.append((chunk_id, text, source, report, index, revisions))
        versions = {"pipeline": job["pipeline_version"],
                    "generator_model": self.settings.lesson_generation_model_id,
                    "review_model": self.settings.lesson_review_model_id,
                    "draft_prompt": DRAFT_PROMPT, "review_prompt": REVIEW_PROMPT,
                    "finalizer_prompt": FINALIZER_PROMPT, "learner_level_prompt": LEARNER_LEVEL_PROMPT_VERSION,
                    "translation_prompt": TRANSLATION_PROMPT_VERSION,
                    "translation_model": self.settings.lesson_generation_model_id,
                    "translation_review_model": self.settings.lesson_review_model_id,
                    "translation_applied": str(any(needs_translation(s["source_language"], data.language)
                                                   for s in sections)).lower(),
                    "source_languages": ",".join(dict.fromkeys(s["source_language"] for s in sections))}
        save_text(all_chunks, versions)
        progress.set_workload("speech", len(all_chunks))
        result_chunks, engines = [], set()
        for index, (chunk_id, text, source, report, section_index, revisions) in enumerate(all_chunks):
            progress("SPEECH", 0.60+0.35*index/len(all_chunks))
            speech_checkpoint = folder / f"speech-{chunk_id}.json"
            audio_path = folder / f"{chunk_id}.wav"
            cached = json.loads(speech_checkpoint.read_text()) if speech_checkpoint.is_file() else None
            if cached and cached.get("signature") == signature and audio_path.is_file():
                with audio_path.open("rb") as stream:
                    valid = hashlib.file_digest(stream, "sha256").hexdigest() == cached["audio_sha256"]
            else:
                valid = False
            if valid:
                full, parts, marks, duration, digest = (cached[key] for key in
                                                       ("ssml", "ssml_parts", "visemes", "duration", "audio_sha256"))
                speech_issues = [ReviewIssue.model_validate(issue) for issue in cached["issues"]]
                engine = cached["engine"]
                save_speech(chunk_id, full, parts)
            else:
                full, parts, speech_issues = self.speech.prepare(text, data.language)
                save_speech(chunk_id, full, parts)
                audio, marks, duration, digest = self.speech.synthesize(parts, data.voice, code)
                speech_issues.extend(self.speech.last_timing_issues)
                engine = self.speech.last_engine
                atomic_write(audio_path, audio)
                write_json(speech_checkpoint, {"signature": signature, "ssml": full, "ssml_parts": parts,
                                               "visemes": [mark.model_dump() for mark in marks], "duration": duration,
                                               "audio_sha256": digest, "engine": engine,
                                               "issues": [issue.model_dump() for issue in speech_issues]})
            engines.add(engine)
            chunk_report = ReviewReport(approved=report.approved and not speech_issues, summary=report.summary,
                                        issues=[*report.issues, *speech_issues], revision_instructions=report.revision_instructions)
            result_chunks.append(LessonChunk(
                chunk_id=chunk_id, index=index, text=text, ssml=full, ssml_parts=parts,
                audio_url=f"/v1/jobs/{job_id}/audio/{chunk_id}", audio_sha256=digest,
                visemes=marks, duration_seconds=duration, sources=[source], reviewer_report=chunk_report,
                section_index=section_index, revisions=revisions,
            ))
        review = merge_reviews([chunk.reviewer_report for chunk in result_chunks])
        # Deduplicate section issues inherited by multiple chunks in the lesson report.
        unique = {json.dumps(issue.model_dump(), sort_keys=True): issue for issue in review.issues}
        review.issues = list(unique.values())
        result = LessonResult(job_id=job_id, lesson_id=data.lesson_id, course_id=data.course_id,
                              activity_id=data.activity_id, language=data.language, voice=data.voice,
                              learner_level=data.options.learner_level, learner_level_applied=True,
                              chunks=result_chunks, reviewer_report=review,
                              versions={**versions,
                                        "visual_review_model": "not-used-v3",
                                        "visual_review_prompt": VISUAL_PROMPT,
                                        "math_speech_prompt": MATH_PROMPT, "math_runtime": "MathJax-4.1.3-v3",
                                        "polly_engine": ",".join(sorted(engines)), "aws_region": self.settings.aws_region})
        write_json(folder / "result.json", result.model_dump(mode="json"))
        return result
