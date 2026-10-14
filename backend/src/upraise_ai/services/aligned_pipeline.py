"""A3 v2: freeze common teaching units before generating any level's speech."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonResult, ChunkPlan, ContentUnit
from upraise_ai.schemas.lessons import (
    DraftParagraph,
    ReviewReport,
    SectionDraft,
    StoredLessonInput,
)
from upraise_ai.services.aligned_models import (
    ALIGNMENT_PROMPT_VERSION,
    IDENTITY_RECOVERY_VERSION,
    MATH_VALIDATION_VERSION,
    REVIEW_STRATEGY_VERSION,
    UnitSizeError,
    validate_ids,
    validate_material_format,
)
from upraise_ai.services.aligned_responses import RESPONSE_FORMAT_VERSION
from upraise_ai.services.lesson_models import merge_reviews
from upraise_ai.services.lesson_pipeline import (
    LessonPipeline,
    plan_sections,
    segment,
    source_issues,
    validate_draft,
    write_json,
)
from upraise_ai.services.lesson_speech import split_words
from upraise_ai.services.lesson_translation import needs_translation
from upraise_ai.services.source_references import display_citations, display_review, lesson_source
from upraise_ai.services.versions import VersionCatalog


class AlignedLessonPipeline(LessonPipeline):
    def prepare(self, job, documents, progress, *, save_text, save_speech, save_plan):
        data = StoredLessonInput.model_validate(job['metadata'])
        profiles = data.level_profiles
        if (not profiles or data.plan_level not in profiles or not data.levels
                or len(set(data.levels)) != len(data.levels) or set(data.levels) != set(profiles)):
            raise IngestionError('LESSON_LEVEL_CONFIGURATION_INVALID')
        profiles = {level: profiles[level] for level in data.levels}
        data = data.model_copy(update={'level_profiles': profiles})
        folder = self.staging.job_dir(str(job['job_id']))
        self._variant_folder = folder
        sections = plan_sections(documents, data.options.source_window_pages)
        if not sections:
            raise IngestionError('LESSON_SOURCE_EMPTY')
        self.speech.validate_voice(data.voice, data.language)
        refs = VersionCatalog(self.settings).references()
        signature = hashlib.sha256(json.dumps({'sections': sections, 'request': job['metadata'],
            'pipeline': job['pipeline_version'], 'versions': refs}, sort_keys=True).encode()).hexdigest()
        expected = {str(row['document_id']): str(row['active_attempt']) for row, _ in documents}
        ready_path = folder / 'aligned-text.json'
        cached = json.loads(ready_path.read_text()) if ready_path.is_file() else None
        progress.set_workload('lesson', len(sections) * (1 + 4 * len(profiles)))
        if cached and cached.get('signature') == signature:
            plan = ChunkPlan.model_validate(cached['plan'])
            prepared = cached['prepared']
        else:
            plan = self.make_plan(job, data, sections, expected, signature, progress)
            # A size failure changes the common plan, never only one variant.
            for refinement in range(3):
                try:
                    prepared = self.generate_variants(data, plan, sections, signature, progress)
                    break
                except UnitSizeError as error:
                    if refinement == 2:
                        raise IngestionError('LESSON_UNIT_TOO_LARGE') from None
                    plan = self.refine_plan(job, plan, error.unit_id)
            write_json(ready_path, {'signature': signature, 'plan': plan.model_dump(mode='json'),
                                    'prepared': prepared})
        if plan.source_attempts != expected or set(prepared) != set(profiles):
            raise IngestionError('LESSON_ALIGNMENT_CHECKPOINT_INVALID')
        versions = {**refs, 'pipeline': job['pipeline_version'], 'chunk_plan': plan.version,
                    'alignment_prompt': ALIGNMENT_PROMPT_VERSION,
                    'identity_recovery': IDENTITY_RECOVERY_VERSION,
                    'response_format': RESPONSE_FORMAT_VERSION,
                    'math_validation': MATH_VALIDATION_VERSION,
                    'review_strategy': REVIEW_STRATEGY_VERSION,
                    'post_edit_material_review': 'deferred',
                    'generator_model': self.settings.lesson_generation_model_id,
                    'review_model': self.settings.lesson_review_model_id,
                    'max_chunk_words': str(data.options.max_chunk_words),
                    'configured_levels': ','.join(profiles), 'aws_region': self.settings.aws_region,
                    'source_languages': ','.join(dict.fromkeys(section['source_language'] for section in sections)),
                    'translation_applied': str(any(needs_translation(section['source_language'], data.language)
                                                  for section in sections)).lower()}
        tuples, by_level = [], {}
        for level in profiles:
            values = prepared[level]
            if [item['content_unit_id'] for item in values] != [str(unit.content_unit_id) for unit in plan.units]:
                raise IngestionError('LESSON_ALIGNMENT_CHECKPOINT_INVALID')
            by_level[level] = []
            for unit, item in zip(plan.units, values, strict=True):
                if len(item['text'].split()) > data.options.max_chunk_words:
                    raise IngestionError('LESSON_UNIT_TOO_LARGE')
                chunk_id = str(uuid5(NAMESPACE_URL, f"upraise:aligned:{job['job_id']}:{level}:{unit.content_unit_id}"))
                report = ReviewReport.model_validate(item['report'])
                value = (chunk_id, display_citations(item['text'], unit.source), unit.source,
                         display_review(report, unit.source), unit.section_index, 1)
                by_level[level].append(value)
                tuples.append((*value, level, unit.content_unit_id))
        # Both durable plan and all finalized text commit before the first TTS request.
        save_plan(plan)
        save_text(tuples, versions)
        progress.set_workload('speech', len(tuples))
        variants = {}
        for level_index, level in enumerate(profiles):
            selected = data.model_copy(update={'options': data.options.model_copy(update={'learner_level': level})})

            def variant_progress(step, value):
                progress(step, 0.60 + 0.35 * (level_index + max(0, min(1, (value - 0.60) / 0.35))) / len(profiles))

            variant_progress.set_workload = lambda *args: None
            result = self.publish(job, selected, by_level[level], versions, signature, variant_progress, save_speech)
            result.generation_version = 'a3-v2'
            result.chunk_plan_id = plan.chunk_plan_id
            result.source_attempts = expected
            for chunk, unit in zip(result.chunks, plan.units, strict=True):
                chunk.content_unit_id = unit.content_unit_id
            variants[level] = result
        bundle = AlignedLessonResult(job_id=str(job['job_id']), lesson_id=data.lesson_id,
            course_id=data.course_id, activity_id=data.activity_id, language=data.language, voice=data.voice,
            chunk_plan_id=plan.chunk_plan_id, content_unit_ids=[unit.content_unit_id for unit in plan.units],
            chunk_count=len(plan.units), variants=variants, source_attempts=expected,
            reviewer_report=merge_reviews([variant.reviewer_report for variant in variants.values()]), versions=versions)
        bundle.reviewer_report.issues = list({json.dumps(issue.model_dump(), sort_keys=True): issue
                                             for issue in bundle.reviewer_report.issues}.values())
        write_json(folder / 'result.json', bundle.model_dump(mode='json'))
        return bundle

    def make_plan(self, job, data, sections, expected, signature, progress):
        units = []
        for index, section in enumerate(sections):
            if len(json.dumps(section, ensure_ascii=False)) > self.settings.lesson_max_source_characters:
                raise IngestionError('LESSON_SOURCE_WINDOW_TOO_LARGE')
            path = self.staging.job_dir(str(job['job_id'])) / f'canonical-section-{index}.json'
            cached = json.loads(path.read_text()) if path.is_file() else None
            if cached and cached.get('signature') == signature:
                draft = SectionDraft.model_validate(cached['draft'])
                report = ReviewReport.model_validate(cached['review'])
            else:
                draft, report, _ = self.models.generate_section(section, section['source_language'],
                    self.staging.source_path(section['document_job_id']), learner_level=data.plan_level,
                    level_profiles=data.level_profiles,
                    notify=lambda step: progress(step, 0.05 + 0.15 * index / len(sections)))
                validate_draft(draft, section['pages'])
                write_json(path, {'signature': signature, 'draft': draft.model_dump(), 'review': report.model_dump()})
            validate_draft(draft, section['pages'])
            for piece, pages in segment(draft, data.options.target_chunk_words):
                source = lesson_source(section, pages)
                units.append(ContentUnit(content_unit_id=uuid5(NAMESPACE_URL,
                    f"upraise:unit:{job['job_id']}:{index}:{len(units)}:{piece}"), index=len(units),
                    section_index=index, source_language=section['source_language'], material_text=piece,
                    source=source, canonical_review=report))
        return self.plan(job, units, expected)

    @staticmethod
    def plan(job, units, expected):
        units = [unit.model_copy(update={'index': index}) for index, unit in enumerate(units)]
        identity = ':'.join(str(unit.content_unit_id) for unit in units)
        return ChunkPlan(chunk_plan_id=uuid5(NAMESPACE_URL, f"upraise:plan:{job['job_id']}:{identity}"),
                         source_attempts=expected, units=units)

    def refine_plan(self, job, plan, unit_id):
        failed = next(unit for unit in plan.units if str(unit.content_unit_id) == unit_id)
        threshold = len(failed.material_text.split())
        units = []
        for unit in plan.units:
            if len(unit.material_text.split()) < threshold:
                units.append(unit)
                continue
            pieces = split_words(unit.material_text, max(15, len(unit.material_text.split()) // 2))
            if len(pieces) < 2:
                raise IngestionError('LESSON_UNIT_TOO_LARGE')
            for index, piece in enumerate(pieces):
                units.append(unit.model_copy(update={'content_unit_id': uuid5(unit.content_unit_id, str(index)),
                                                     'material_text': piece}))
        return self.plan(job, units, plan.source_attempts)

    def generate_variants(self, data, plan, sections, signature, progress):
        prepared = {level: [] for level in data.level_profiles}
        completed, total = 0, len(plan.units) * len(data.level_profiles)
        for level in data.level_profiles:
            for section_index, section in enumerate(sections):
                selected = [unit for unit in plan.units if unit.section_index == section_index]
                # Bound the output budget even when callers request long units.
                size = min(self.settings.lesson_alignment_batch_units, max(1, 2500 // data.options.max_chunk_words))
                for start in range(0, len(selected), size):
                    units = selected[start:start + size]
                    key = hashlib.sha256(':'.join(str(unit.content_unit_id) for unit in units).encode()).hexdigest()[:20]
                    path = self._variant_folder / f'aligned-{level}-{key}.json'
                    cached = json.loads(path.read_text()) if path.is_file() else None
                    if cached and cached.get('signature') == signature:
                        values = cached['values']
                    else:
                        position = 0.20 + 0.40 * completed / total
                        texts, reports = self.models.adapt_units(section, units, section['source_language'],
                            learner_level=level, profiles=data.level_profiles,
                            target_words=data.options.target_chunk_words, max_words=data.options.max_chunk_words,
                            notify=lambda step: progress(step, position))
                        validate_ids(texts.units, units)
                        if needs_translation(section['source_language'], data.language):
                            progress('TRANSLATE', position)
                            draft = SectionDraft(paragraphs=[DraftParagraph(text=item.text, pages=unit.source.citation_numbers)
                                for item, unit in zip(texts.units, units, strict=True)])
                            translated = self.models.translate_section(draft, section['source_language'],
                                                                       data.language, learner_level=level)
                            if len(translated.paragraphs) != len(units):
                                raise IngestionError('LESSON_ALIGNMENT_TRANSLATION_INVALID', retryable=True)
                            for item, paragraph in zip(texts.units, translated.paragraphs, strict=True):
                                item.text = paragraph.text
                        if any(len(item.text.split()) > data.options.max_chunk_words for item in texts.units):
                            texts, reports = self.models.adapt_units(section, units, data.language,
                                learner_level=level, profiles=data.level_profiles,
                                target_words=data.options.target_chunk_words, max_words=data.options.max_chunk_words,
                                current=texts, notify=lambda step: progress(step, position))
                            validate_ids(texts.units, units)
                        values = []
                        for item, unit in zip(texts.units, units, strict=True):
                            item.text = validate_material_format(unit, item.text)
                            if len(item.text.split()) > data.options.max_chunk_words:
                                raise UnitSizeError(unit.content_unit_id)
                            report = reports[str(unit.content_unit_id)]
                            flags = [*source_issues(section), *(unit.canonical_review.issues if unit.canonical_review else [])]
                            report = ReviewReport(approved=report.approved and not flags, summary=report.summary,
                                issues=[*report.issues, *flags], revision_instructions=report.revision_instructions)
                            values.append({'content_unit_id': str(unit.content_unit_id), 'text': item.text,
                                           'report': report.model_dump()})
                        write_json(path, {'signature': signature, 'values': values})
                    prepared[level].extend(values)
                    completed += len(units)
        return prepared
