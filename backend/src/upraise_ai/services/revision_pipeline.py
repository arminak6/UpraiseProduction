"""Stage selected teaching-unit edits for one atomic lesson replacement."""

import hashlib
import json

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonResult
from upraise_ai.schemas.lessons import (
    LessonResult,
    ReviewIssue,
    ReviewReport,
    SectionDraft,
    StoredLessonInput,
    Viseme,
)
from upraise_ai.schemas.revisions import LessonRevision
from upraise_ai.services.aligned_models import reviewed
from upraise_ai.services.lesson_models import merge_reviews
from upraise_ai.services.lesson_pipeline import LessonPipeline, atomic_write, source_issues, write_json
from upraise_ai.services.lesson_translation import needs_translation
from upraise_ai.services.revision_models import (
    REVISION_PROMPT_VERSION,
    RevisionModelInvalidOutput,
    validate_revision_text,
)
from upraise_ai.services.revision_validation import MAX_ALIGNMENT_CORRECTIONS, validate_aligned_revision
from upraise_ai.services.source_references import (
    citation_chunks,
    display_citations,
    display_review,
    model_citations,
)
from upraise_ai.services.versions import VersionCatalog


def file_hash(path):
    if not path.is_file():
        raise IngestionError("REVISION_SNAPSHOT_UNAVAILABLE")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class RevisionPipeline(LessonPipeline):
    def load_documents(self, job):
        documents = super().load_documents(job)
        data = StoredLessonInput.model_validate(job["metadata"])
        if documents is None:
            raise IngestionError("REVISION_SOURCE_UNAVAILABLE")
        if {str(row["document_id"]): str(row["active_attempt"]) for row, _ in documents} != data.revision_request.source_attempts:
            raise IngestionError("REVISION_SOURCE_VERSION_CHANGED")
        return documents

    def prepare(self, job, documents, progress, *, save_text, save_speech):
        revision = StoredLessonInput.model_validate(job["metadata"]).revision_request
        path = self.staging.job_dir(str(job["job_id"])) / "revision-base.json"
        if file_hash(path) != revision.snapshot_sha256:
            raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
        if not revision.apply_to_all_levels:
            return self.prepare_level(job, documents, progress, save_text=save_text, save_speech=save_speech,
                                      result=LessonResult.model_validate_json(path.read_bytes()))
        result = AlignedLessonResult.model_validate_json(path.read_bytes())
        if (not revision.aligned_unit or set(result.variants) != set(revision.target_chunk_sha256_by_level)
                or result.job_id != str(revision.parent_job_id)):
            raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
        unit_id = str(revision.aligned_unit["content_unit_id"])
        count = len(result.variants)
        for index, (level, variant) in enumerate(result.variants.items()):
            selected = next((chunk for chunk in variant.chunks if str(chunk.content_unit_id) == unit_id), None)
            if selected is None:
                raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
            metadata = {**job["metadata"], "options": {**job["metadata"]["options"], "learner_level": level},
                        "revision_request": {**job["metadata"]["revision_request"], "chunk_id": selected.chunk_id}}
            level_job = {**job, "metadata": metadata}

            def level_progress(step, fraction, offset=index):
                progress(step, 0.05 + 0.90 * (offset + fraction) / count)

            level_progress.set_workload = lambda phase, units: progress.set_workload(phase, count * units)
            result.variants[level] = self.prepare_level(level_job, documents, level_progress,
                save_text=lambda chunks, versions: save_text(chunks, versions, partial=True),
                save_speech=save_speech, result=variant, checkpoint_key="-" + selected.chunk_id)
        result.job_id, result.expires_at = str(job["job_id"]), None
        result.reviewer_report = merge_reviews([variant.reviewer_report for variant in result.variants.values()])
        result.versions.update(VersionCatalog(self.settings).references("revision_"))
        write_json(path.parent / "result.json", result.model_dump(mode="json"))
        return result

    def prepare_level(self, job, documents, progress, *, save_text, save_speech, result, checkpoint_key=""):
        data = StoredLessonInput.model_validate(job["metadata"])
        revision = data.revision_request
        job_id = str(job["job_id"])
        folder = self.staging.job_dir(job_id)
        selected = next((chunk for chunk in result.chunks if chunk.chunk_id == str(revision.chunk_id)), None)
        if (selected is None or result.job_id != str(revision.parent_job_id)
                or result.course_id != data.course_id or result.lesson_id != data.lesson_id
                or result.language != data.language or result.voice != data.voice
                or result.learner_level != data.options.learner_level
                or [chunk.index for chunk in result.chunks] != list(range(len(result.chunks)))):
            raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
        source = selected.sources[0]
        document, available = next(((row, chunks) for row, chunks in documents
                                    if str(row["document_id"]) == source.document_id), (None, []))
        chunks = [chunk for chunk in available if chunk["chunk_id"] in source.source_chunk_ids]
        if (document is None or not chunks or {chunk["chunk_id"] for chunk in chunks} != set(source.source_chunk_ids)
                or any(chunk["document_version_id"] != source.document_version_id for chunk in chunks)):
            raise IngestionError("REVISION_SOURCE_UNAVAILABLE")
        if sum(len(chunk["text"]) for chunk in chunks) > self.settings.lesson_max_source_characters:
            raise IngestionError("REVISION_SOURCE_CONTEXT_TOO_LARGE")
        blocks = source.source_format in {'csv', 'doc', 'docx'}
        chunks = citation_chunks(chunks)
        pages = sorted({page for chunk in chunks for page in chunk["page_numbers"]})
        section = {"document_id": source.document_id, "document_version_id": source.document_version_id,
                   "source_language": document["metadata"]["language"], "pages": pages,
                   "window": [min(pages), max(pages)], "page_count": document["result"]["page_count"],
                   "course_id": data.course_id, "chunks": chunks}
        if blocks:
            section.update(source_format=source.source_format, reference_kind=source.source_format + '_source_block',
                           page_count=document["result"]["source_block_count"])
        if revision.aligned_unit:
            section.update(alignment_unit=revision.aligned_unit, level_profiles=data.level_profiles)
        version_refs = VersionCatalog(self.settings).references("revision_")
        version_refs.update({"revision_" + key: value
                             for key, value in getattr(self.models, "teaching_versions", {}).items()})
        signature = hashlib.sha256(json.dumps({"request": job["metadata"], "pipeline": job["pipeline_version"], "versions": version_refs,
            "prompt": REVISION_PROMPT_VERSION}, sort_keys=True).encode()).hexdigest()
        progress.set_workload("lesson", 1 + 2 * MAX_ALIGNMENT_CORRECTIONS if revision.aligned_unit else 1)
        progress("GENERATE", 0.05)
        neighbours = [{"index": chunk.index, "text": chunk.text[:6000]} for chunk in result.chunks
                      if abs(chunk.index - selected.index) == 1]
        checkpoint_path = folder / f"revision-final{checkpoint_key}.json"
        if checkpoint_path.is_file():
            saved = json.loads(checkpoint_path.read_bytes())
            if saved["signature"] != signature:
                raise IngestionError("REVISION_CHECKPOINT_CONFLICT")
            text = validate_revision_text(saved["text"])
            report = ReviewReport.model_validate(saved["review"])
            edits = saved["edits"]
        else:
            try:
                text, report, edits = self.models.revise_chunk(
                    section, model_citations(selected.text, source), revision.teacher_review, neighbours,
                    learner_level=data.options.learner_level,
                    notify=lambda step: progress(step, 0.25 if step == "REVIEW" else 0.40))
            except RevisionModelInvalidOutput as error:
                error.details.update(chunk_id=selected.chunk_id, chunk_index=selected.index)
                raise
            text = validate_revision_text(text)
            if needs_translation(section["source_language"], data.language):
                progress("TRANSLATE", 0.48)
                draft = self.models.translate_section(SectionDraft(paragraphs=[{"text": text, "pages": pages}]),
                    section["source_language"], data.language, learner_level=data.options.learner_level)
                text = validate_revision_text("\n\n".join(paragraph.text for paragraph in draft.paragraphs))
            flags = source_issues(section)
            report = ReviewReport(approved=report.approved and not flags, summary=report.summary,
                                  issues=[*report.issues, *flags], revision_instructions=report.revision_instructions)
            saved = {"signature": signature, "text": text, "review": report.model_dump(), "edits": edits,
                     "correction_rounds": 0}
            write_json(checkpoint_path, saved)
        correction_rounds = 0
        if revision.aligned_unit:
            text, correction_rounds = validate_aligned_revision(self.models, section, data, selected, neighbours,
                saved, checkpoint_path, progress, reviewed)
            edits = saved["edits"]
        final_check = getattr(self.models, "check_written", None)
        if final_check:
            warnings = final_check(section, [text], data.language, data.options.learner_level,
                        [revision.aligned_unit["material_text"] if revision.aligned_unit else selected.text])
            if warnings:
                report = self.models.retain_warnings(report, warnings)
        selected.text, selected.reviewer_report, selected.revisions = display_citations(text, source), display_review(report, source), edits
        if not revision.aligned_unit:
            if blocks:
                selected.sources[0].source_blocks = pages
            else:
                selected.sources[0].pages = pages
        result.job_id, result.expires_at = job_id, None
        result.revision = LessonRevision(parent_job_id=revision.parent_job_id, chunk_id=revision.chunk_id, number=revision.number)
        result.versions.update(**version_refs,
                               revision_prompt=REVISION_PROMPT_VERSION,
                               revision_pipeline=job["pipeline_version"],
                               revision_source_language=section["source_language"],
                               revision_translation_applied=str(needs_translation(section["source_language"], data.language)).lower(),
                               revision_generator_model=self.settings.lesson_generation_model_id,
                               revision_review_model=self.settings.lesson_review_model_id)
        result.versions.update({"revision_" + key: value
                                for key, value in getattr(self.models, "teaching_versions", {}).items()})
        if revision.aligned_unit:
            result.versions["revision_correction_rounds"] = str(correction_rounds)
        all_chunks = [(chunk.chunk_id, chunk.text, chunk.sources[0], chunk.reviewer_report,
                       chunk.section_index, chunk.revisions) for chunk in result.chunks]
        if revision.aligned_unit:
            all_chunks = [(*value, result.learner_level, chunk.content_unit_id)
                          for value, chunk in zip(all_chunks, result.chunks, strict=True)]
        save_text(all_chunks, result.versions)
        for chunk in result.chunks:
            if chunk.chunk_id != selected.chunk_id:
                if file_hash(folder / f"{chunk.chunk_id}.wav") != chunk.audio_sha256:
                    raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE")
                save_speech(chunk.chunk_id, chunk.ssml, chunk.ssml_parts)
            chunk.audio_url = f"/v1/jobs/{job_id}/audio/{chunk.chunk_id}"
        progress.set_workload("speech", 1)
        progress("SPEECH", 0.60)
        audio_path = folder / f"{selected.chunk_id}.wav"
        speech_path = folder / f"revision-speech{checkpoint_key}.json"
        if speech_path.is_file():
            saved = json.loads(speech_path.read_bytes())
            if saved["signature"] != signature or file_hash(audio_path) != saved["audio_sha256"]:
                raise IngestionError("REVISION_CHECKPOINT_CONFLICT")
            full, parts = saved["ssml"], saved["ssml_parts"]
            marks, duration, digest = saved["visemes"], saved["duration"], saved["audio_sha256"]
            speech_issues = [ReviewIssue.model_validate(issue) for issue in saved["issues"]]
            engine = saved["engine"]
            save_speech(selected.chunk_id, full, parts)
        else:
            full, parts, speech_issues = self.speech.prepare(text, data.language)
            save_speech(selected.chunk_id, full, parts)
            code = self.speech.validate_voice(data.voice, data.language)
            audio, marks, duration, digest = self.speech.synthesize(parts, data.voice, code)
            speech_issues.extend(self.speech.last_timing_issues)
            engine = self.speech.last_engine
            atomic_write(audio_path, audio)
            write_json(speech_path, {"signature": signature, "ssml": full, "ssml_parts": parts,
                "visemes": [mark.model_dump() for mark in marks], "duration": duration, "audio_sha256": digest,
                "issues": [issue.model_dump() for issue in speech_issues], "engine": engine})
        selected.ssml, selected.ssml_parts = full, parts
        selected.audio_sha256, selected.duration_seconds = digest, duration
        # Validate cached marks with the same public schema as fresh marks.
        selected.visemes = [Viseme.model_validate(mark) if isinstance(mark, dict) else mark for mark in marks]
        selected.reviewer_report = ReviewReport(approved=report.approved and not speech_issues, summary=report.summary,
            issues=[*report.issues, *speech_issues], revision_instructions=report.revision_instructions)
        result.versions["polly_engine"] = ",".join(sorted({engine, *result.versions.get("polly_engine", "").split(",")} - {""}))
        result.reviewer_report = merge_reviews([chunk.reviewer_report for chunk in result.chunks])
        result.reviewer_report.issues = list({json.dumps(issue.model_dump(), sort_keys=True): issue
                                             for issue in result.reviewer_report.issues}.values())
        write_json(folder / "result.json", result.model_dump(mode="json"))
        return result
