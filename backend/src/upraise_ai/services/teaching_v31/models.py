"""Evidence-preserving V3.1 authoring, indexed repairs and explicit level review."""

from difflib import SequenceMatcher

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedText, AlignedTexts
from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport, SectionDraft
from upraise_ai.services.aligned_models import validate_ids
from upraise_ai.services.lesson_pipeline import validate_draft, write_json
from upraise_ai.services.lesson_translation import needs_translation
from upraise_ai.services.teaching_v2.contracts import (
    MaterialDecision,
    TeachingMaterialRejected,
    validate_written,
)
from upraise_ai.services.teaching_v2.entailment import ENTAILMENT_POLICY
from upraise_ai.services.teaching_v2.evidence import ParagraphSupport
from upraise_ai.services.teaching_v2.excerpts import EXCERPT_WARNING, REVIEWED_TEXT_WARNING
from upraise_ai.services.teaching_v2.material_review import question_number_issues
from upraise_ai.services.teaching_v3 import TeachingModelsV3
from upraise_ai.services.teaching_v3.contracts import CoverageCheck
from upraise_ai.services.teaching_v3.entailment import MATHEMATICAL_ENTAILMENT
from upraise_ai.services.teaching_v3.prompts import COURSE_POLICY, COVERAGE_POLICY

from .contracts import EvidenceDraft, EvidenceText, EvidenceUnits, LevelCheck
from .evidence import (
    MaterialFindings,
    bind,
    cited_records,
    presentation_text,
    records,
    reference_label,
    restore_selection,
    substantive,
    without_citations,
)
from .prompts import EVIDENCE_POLICY, LEVEL_POLICY, LEVEL_TASKS, VERSION
from .review import verdict


class TeachingModelsV31(TeachingModelsV3):
    reviews_final_candidate = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teaching_versions.update(teaching_version="v3.1", teaching_policy=VERSION,
            material_evidence="original-chunk-headings-host-citations-v1",
            material_review="cited-record-entailment-indexed-repair-v1",
            paragraph_repair="host-owned-single-target-v2",
            material_recovery="bounded-source-records-v2",
            coverage_scope="substantive-body-with-label-context-v2",
            post_edit_material_review="final-candidate-only-v31",
            level_review="learning-task-and-cross-level-similarity-v1",
            verdict_cache="full-evidence-policy-models-v1", narration_task="questions-and-identities-v31")
        self._bindings, self._finished_levels = {}, {}

    def set_document_context(self, documents):
        super().set_document_context(documents)
        self._bindings, self._finished_levels = {}, {}

    def select_source(self, section):
        if "v3_index" in section:
            return super().select_source(section)
        selected, warnings = super().select_source(section)
        return restore_selection(section, selected), warnings

    def material_context(self, section):
        result = super().material_context(section)
        # The writer and every reviewer see the same original text/subject links.
        return {**{k: v for k, v in result.items() if k != "source_context"}, "evidence": records(section)}

    def _bind(self, section, item, *, allowed=None):
        text, pages, chosen = bind(section, item, allowed=allowed)
        self._bindings[self._binding_key(section, text)] = chosen
        return text, pages

    @staticmethod
    def _binding_key(section, text):
        return str(section["document_id"]), str(section["document_version_id"]), text

    def _payload(self, section, language, level, maximum):
        return {**self.material_context(section), "language": language, "learner_level": level,
                "level_profile": self.settings.lesson_level_profiles.get(level),
                "learning_task": LEVEL_TASKS.get(level, "Follow the supplied level profile."),
                "max_words_per_unit": maximum}

    def generate_section(self, section, language, *args, learner_level="intermediate", **kwargs):
        section, source_warnings = self.select_source(section)
        payload = self._payload(section, language, learner_level, self.max_words)
        draft = self._author(section, payload, EvidenceDraft, "teaching_v31_section")
        if notify := kwargs.get("notify"):
            notify("REVIEW")
        draft, rounds, warnings = self.checked_material(section, draft, language, learner_level,
                                                       allow_source_excerpt=True)
        report = self.retain_warnings(ReviewReport(approved=True,
            summary="V3.1 evidence and teaching checks completed; human review is required."),
            [*source_warnings, *warnings])
        if notify:
            notify("FINALIZE")
        return draft, report, int(rounds > 0)

    def _author(self, section, payload, schema, stage, units=None):
        for attempt in range(3):
            # Checkpoint the parsed author output before downstream checks. A
            # retry of one unavailable verdict must not create a different draft
            # and invalidate every already-passing paragraph verdict.
            output = verdict(self, EVIDENCE_POLICY + COURSE_POLICY, payload, schema, stage, max_tokens=8000)
            try:
                if units is None:
                    paragraphs = [dict(zip(("text", "pages"), self._bind(section, item), strict=True))
                                  for item in output.paragraphs]
                    return SectionDraft(paragraphs=paragraphs)
                if [str(u.content_unit_id) for u in units] != [u.content_unit_id for u in output.units]:
                    raise IngestionError("TEACHING_V31_EVIDENCE_INVALID")
                return AlignedTexts(units=[AlignedText(content_unit_id=unit.content_unit_id,
                    text=self._bind(section, item, allowed=set(unit.source.citation_numbers))[0])
                    for unit, item in zip(units, output.units, strict=True)])
            except IngestionError as error:
                if attempt == 2:
                    raise
                payload = {**payload, "corrections": f"{error.code}: use actual authorized evidence_ids, "
                           "no citation tags, real newlines, and the exact fixed unit IDs in order."}
        raise AssertionError("unreachable")

    def _review(self, section, texts, language, level, required=None, maximum=None):
        if required is not None and len(required) != len(texts):
            raise IngestionError("TEACHING_V31_REQUIRED_SCOPE_INVALID")
        failures, warnings = MaterialFindings(), []

        def fail(index, message, evidence=()):
            failures.add(index, message, evidence)

        for index, text in enumerate(texts):
            try:
                validate_written(text)
                bound = self._bindings.get(self._binding_key(section, text)) or cited_records(section, text)
                # Even remembered bindings must retain resolvable numeric tags.
                cited_records(section, text)
                if len(text) > 16000 or len(text.split()) > (maximum or self.max_words):
                    fail(index, "Split or shorten this complete thought within the word/character limit.", bound)
            except IngestionError as error:
                fail(index, error.code)
                continue
            payload = {"candidate": without_citations(text), "reference": bound,
                       "language": language}
            policy = ENTAILMENT_POLICY + MATHEMATICAL_ENTAILMENT + EVIDENCE_POLICY + (
                "\nAudit only factual support of this candidate in its CHOSEN evidence records, including "
                "structurally linked subjects. Do not accept evidence from other course topics. "
                "Return supported and a reason. For supported=false identify ONLY the actual unsupported "
                "claim and why, in at most two concise sentences. Do not list the claims that pass. "
                "Use the requested language. Check question premises, not an invented answer.")
            check = verdict(self, policy, payload, ParagraphSupport, "teaching_v31_support")
            if not check.supported:
                check = verdict(self, policy + "\nIndependently verify the allegation against the actual evidence.",
                    {**payload, "allegation": check.reason}, ParagraphSupport,
                    "teaching_v31_support_verification", reviewer=True)
            if not check.supported:
                fail(index, check.reason, bound)
        primary = set(section.get("v3_primary_chunk_ids", [c["chunk_id"] for c in section["chunks"]]))
        refs = required if required is not None else [r for r in records(section)
                                                      if r["chunk_id"] in primary and substantive(r)]
        next_append = len(texts)
        for index, reference in enumerate(refs):
            candidates = [texts[index]] if required is not None else texts
            payload = {"required_excerpt": reference, "candidate_teaching": candidates, "language": language}
            policy = COVERAGE_POLICY + EVIDENCE_POLICY + (
                "\nCheck intact substantive facts, subject identities, question tasks and actual prose language. "
                "Do not demand decorative headings or verbatim text. Source damage is a warning. "
                "Use omitted_available, language_errors, warnings.")
            check = verdict(self, policy, payload, CoverageCheck, "teaching_v31_coverage", reviewer=True)
            if check.omitted_available or check.language_errors:
                check = verdict(self, policy + "\nIndependently retain only confirmed omissions or wrong-language prose.",
                    {**payload, "allegation": check.model_dump()}, CoverageCheck,
                    "teaching_v31_coverage_verification")
            if check.omitted_available or check.language_errors:
                if required is not None:
                    target = index
                else:
                    # Add missing evidence as a new paragraph if no current paragraph cites it.
                    matching = []
                    for i, text in enumerate(texts):
                        try:
                            if any(r["chunk_id"] == reference["chunk_id"] for r in cited_records(section, text)):
                                matching.append(i)
                        except IngestionError:
                            continue
                    if matching:
                        target = matching[0]
                    else:
                        # Separate missing source records have separate host
                        # targets; never squeeze a whole source window into one.
                        target, next_append = next_append, next_append + 1
                label = "Required teaching material" if required is not None else reference_label(reference)
                evidence = () if required is not None else (reference,)
                fail(target, label + ": " + "; ".join([*check.omitted_available, *check.language_errors]), evidence)
            warnings.extend(ReviewIssue(category="source", message=message[:2000], pages=section["pages"])
                            for message in check.warnings if message.strip())
        # Keep the existing deterministic question/number invariants too.
        if required is not None:
            for i, (text, reference) in enumerate(zip(texts, required, strict=True)):
                for issue in question_number_issues({"candidates": [text], "required_material": [reference],
                                                    "required_per_candidate": True, "clarifying_context": []}):
                    fail(i, issue)
        return failures, warnings

    def check_written(self, section, texts, language, learner_level, required_material=None, *, correction_rounds=0):
        failures, warnings = self._review(section, texts, language, learner_level, required_material)
        if failures:
            self._reject(section, learner_level, failures, correction_rounds)
        return warnings

    @staticmethod
    def _reject(section, level, failures, rounds):
        raise TeachingMaterialRejected(MaterialDecision(correct=False, complete=False, in_language=True,
            natural=True, issues=[f"Paragraph {i + 1}: {message}" for i, messages in failures.items()
                                  for message in messages][:30]), section=section,
            learner_level=level, correction_rounds=rounds)

    def checked_material(self, section, candidate, language, learner_level, *, units=None, max_words=None,
                         allow_source_excerpt=False):
        required = [u.material_text for u in units] if units is not None else None
        for rounds in range(3):
            if units is None:
                validate_draft(candidate, section["pages"])
                texts = [p.text for p in candidate.paragraphs]
            else:
                validate_ids(units, candidate.units)
                texts = [p.text for p in candidate.units]
            failures, warnings = self._review(section, texts, language, learner_level, required, max_words)
            if not failures:
                return candidate, rounds, warnings
            if rounds == 2:
                if not allow_source_excerpt or not self.draft_review_recovery:
                    self._reject(section, learner_level, failures, rounds)
                return self._recover(section, candidate, failures, language, learner_level, units, max_words, rounds)
            replacements, next_append = {}, len(texts)
            for index, findings in sorted(failures.items()):
                if index < 0 or index > next_append or (units is not None and index >= len(units)):
                    raise IngestionError("TEACHING_V31_REQUIRED_SCOPE_INVALID")
                if index == next_append:
                    next_append += 1
                allowed = set(units[index].source.citation_numbers) if units is not None else None
                payload = {**self._payload(section, language, learner_level, max_words or self.max_words),
                    "target": {"candidate_index": index, "text": texts[index] if index < len(texts) else ""},
                    "findings": findings, "required_material": required[index] if required is not None else None,
                    "target_required_evidence": list(getattr(failures, "evidence", {}).get(index, {}).values()),
                    "correction_round": rounds + 1,
                    "instruction": "Return ONE replacement for the supplied target, or one new complete thought "
                        "when its text is empty. The host owns its identity. Return only text and evidence_ids, "
                        "never paragraph indexes, lists of patches or other paragraphs. Preserve supported facts "
                        "and the requested learning task. No numeric citation tags."}
                if allowed is not None:
                    payload["evidence"] = [r for r in payload["evidence"]
                                           if set(r["source_numbers"]).issubset(allowed)]
                try:
                    patch = verdict(self, EVIDENCE_POLICY + COURSE_POLICY, payload, EvidenceText,
                                    "teaching_v31_target_repair", reviewer=rounds == 1, max_tokens=8000)
                    replacements[index] = self._bind(section, patch, allowed=allowed)
                except IngestionError as error:
                    if error.code not in {"TEACHING_V2_MODEL_OUTPUT_INVALID", "TEACHING_V3_MODEL_RESPONSE_INVALID",
                                          "TEACHING_V31_EVIDENCE_INVALID", "TEACHING_V2_TEXT_INVALID",
                                          "LESSON_CITATIONS_INVALID"}:
                        raise
                    # A malformed model edit never chooses a slot or overwrites
                    # a passed paragraph. Retry this target in the next bounded
                    # round; exhaustion uses flagged recovery, or strict rejection.
                    if self.diagnostic_dir:
                        write_json(self.diagnostic_dir / f"target-repair-{rounds + 1}-{index}.json",
                            {"stage": "teaching_v31_target_repair", "target_index": index,
                             "correction_round": rounds + 1, "problem": error.code,
                             "replacement_applied": False})
            candidate = self._replace(candidate, replacements)
        raise AssertionError("unreachable")

    @staticmethod
    def _replace(candidate, replacements):
        if isinstance(candidate, SectionDraft):
            values = [p.model_dump() for p in candidate.paragraphs]
            original_count = len(values)
            for index, (text, pages) in sorted(replacements.items()):
                value = {"text": text, "pages": pages}
                if index >= original_count:
                    values.append(value)
                else:
                    values[index] = value
            return SectionDraft(paragraphs=values)
        values = [p.model_copy(deep=True) for p in candidate.units]
        for index, (text, _) in replacements.items():
            values[index].text = text
        return AlignedTexts(units=values)

    def _recover(self, section, candidate, failures, language, level, units, maximum, rounds):
        if not self.draft_review_recovery:
            self._reject(section, level, failures, rounds)
        texts = [p.text for p in (candidate.paragraphs if units is None else candidate.units)]
        limit = maximum or self.max_words
        if units is not None:
            replacements = {}
            for index in failures:
                unit = units[index]
                if unit.canonical_review is None or needs_translation(unit.source_language, language):
                    self._reject(section, level, failures, rounds)
                text, pages = unit.material_text, unit.source.citation_numbers
                validate_written(text)
                if len(text) > 16000 or len(text.split()) > limit:
                    self._reject(section, level, {index: ["The reviewed unit exceeds the configured size limit."]}, rounds)
                replacements[index] = text, pages
            recovered = self._replace(candidate, replacements)
        else:
            if not section.get("source_language") or needs_translation(section["source_language"], language):
                self._reject(section, level, {i: ["A source excerpt is unavailable in the requested language."]
                                              for i in failures}, rounds)
            paragraphs, seen = [], set()
            for index in range(max(len(texts), max(failures) + 1)):
                if index not in failures:
                    if index < len(candidate.paragraphs):
                        paragraphs.append(candidate.paragraphs[index].model_dump())
                    continue
                chosen = list(getattr(failures, "evidence", {}).get(index, {}).values())
                if not chosen:
                    if index < len(texts):
                        chosen = cited_records(section, texts[index])
                    else:
                        primary = section.get("v3_primary_chunk_ids", [c["chunk_id"] for c in section["chunks"]])
                        chosen = [r for r in records(section) if r["chunk_id"] in primary]
                # Recovery happens before the canonical plan receives unit IDs.
                # Expand one failed thought into bounded original source records
                # instead of concatenating a whole window into one oversized unit.
                for record in chosen:
                    if record["chunk_id"] in seen or not substantive(record):
                        continue
                    text, pages = self._bind(section, EvidenceText(
                        text=self._excerpt(record), evidence_ids=[record["chunk_id"]]))
                    if len(text) > 16000 or len(text.split()) > limit:
                        self._reject(section, level, {index: [reference_label(record) +
                            f": the complete source excerpt exceeds the {limit}-word limit; increase the limit "
                            "or review this source passage."]}, rounds)
                    paragraphs.append({"text": text, "pages": pages})
                    seen.add(record["chunk_id"])
            if not paragraphs:
                self._reject(section, level, {0: ["No substantive source excerpt is available for recovery."]}, rounds)
            recovered = SectionDraft(paragraphs=paragraphs)
        return recovered, rounds, [ReviewIssue(category="source",
            message=REVIEWED_TEXT_WARNING if units is not None else EXCERPT_WARNING, pages=section["pages"])]

    @staticmethod
    def _excerpt(record):
        headings = record.get("heading_path", [])
        if isinstance(headings, str):
            headings = [headings]
        labels = [heading for heading in headings if heading.strip() and heading not in record["text"]]
        return "\n\n".join([*labels, presentation_text(record["text"])])

    def adapt_units(self, section, units, language, *, learner_level, **kwargs):
        section, source_warnings = self.select_source(section)
        maximum = kwargs.get("max_words") or self.max_words
        payload = {**self._payload(section, language, learner_level, maximum),
            "fixed_units": [u.model_dump(mode="json") for u in units],
            "instruction": "Keep the exact fixed unit IDs and order; preserve each unit's required facts and "
                "citation scope while changing the learning task for this level. Do not copy canonical prose "
                "and call it adapted. If evidence is limited, explain its stated limits without inventing facts."}
        candidate = self._author(section, payload, EvidenceUnits, "teaching_v31_adaptation", units)
        candidate, _, warnings = self.checked_material(section, candidate, language, learner_level,
            units=units, max_words=maximum, allow_source_excerpt=True)
        reports = {}
        for unit, value in zip(units, candidate.units, strict=True):
            level_warnings = self._level_warnings(section, value.text, language, learner_level, unit.material_text)
            reports[str(unit.content_unit_id)] = self.retain_warnings(ReviewReport(approved=True,
                summary="V3.1 final material and level review; human review is required."),
                [*source_warnings, *warnings, *level_warnings])
        return candidate, reports

    def _level_warnings(self, section, text, language, level, other=None):
        payload = {**self._payload(section, language, level, self.max_words), "candidate": text}
        if other is not None:
            similarity = SequenceMatcher(None, without_citations(text), without_citations(other)).ratio()
            if similarity >= .92:
                payload.update(other_level_teaching=other, similarity_requires_review=True)
        check = verdict(self, LEVEL_POLICY, payload, LevelCheck, "teaching_v31_level", reviewer=True)
        if check.suitable:
            return []
        if not self.draft_review_recovery:
            self._reject(section, level, {0: [check.reason]}, 0)
        return [ReviewIssue(category="clarity", pages=section["pages"], message=(
            "Difficulty adaptation incomplete; teacher editing required for " + level + ": " + check.reason)[:2000])]

    def revise_chunk(self, section, original, feedback, neighbours, *, learner_level, **kwargs):
        # A7 uses the same evidence-ID author contract, with no source-excerpt
        # recovery. The revision pipeline independently checks final translation
        # and alignment and retains every other published chunk unchanged.
        section, source_warnings = self.select_source(section)
        language = section["source_language"]
        payload = {**self._payload(section, language, learner_level, self.max_words),
            "current_written": original, "teacher_feedback": feedback, "neighbours": neighbours,
            "instruction": "Revise ONLY this current teaching unit according to teacher feedback. "
                "Preserve its required meaning, source scope and complete thought boundaries. "
                "Return one or more paragraphs with actual evidence_ids; no numeric citation tags."}
        for rounds in range(3):
            draft = self._author(section, payload, EvidenceDraft, "teaching_v31_revision")
            text = "\n\n".join(p.text for p in draft.paragraphs)
            chosen = {r["chunk_id"]: r for p in draft.paragraphs
                      for r in self._bindings[self._binding_key(section, p.text)]}
            self._bindings[self._binding_key(section, text)] = list(chosen.values())
            if notify := kwargs.get("notify"):
                notify("REVIEW")
            try:
                warnings = self.check_written(section, [text], language, learner_level, [original],
                                               correction_rounds=rounds)
                warnings.extend(self._level_warnings(section, text, language, learner_level))
            except TeachingMaterialRejected as error:
                if rounds == 2:
                    raise
                payload.update(previous_candidate=text, corrections=error.review.model_dump())
                continue
            report = self.retain_warnings(ReviewReport(approved=True,
                summary="V3.1 source-bound teacher revision; human review is required."),
                [*source_warnings, *warnings])
            if notify:
                notify("FINALIZE")
            return text, report, rounds
        raise AssertionError("unreachable")

    def finish_course(self, values, data, level, signature, folder, progress):
        values = super().finish_course(values, data, level, signature, folder, progress)
        result = []
        for index, value in enumerate(values):
            other = next((texts[index] for texts in self._finished_levels.values() if index in texts), None)
            warnings = self._level_warnings(self.course_sections[value[4]], value[1], data.language, level, other)
            result.append((*value[:3], self.retain_warnings(value[3], warnings), *value[4:]))
        self._finished_levels[level] = {i: v[1] for i, v in enumerate(result)}
        return result
