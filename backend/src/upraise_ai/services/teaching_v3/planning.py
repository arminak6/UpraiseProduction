"""Source-grounded outline, ordered semantic grouping and replayable teaching state."""

import json

from upraise_ai.schemas.lessons import ReviewIssue
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.teaching_v2.contracts import structured
from upraise_ai.workers.budgets import notify_workload

from .contracts import CourseError, SectionPlan, fingerprint
from .prompts import PLAN_POLICY, VERSION


def plan_course(models, sections, data, folder, progress):
    # Planning itself calls source selectors/models. Announce that bounded work
    # before those calls, rather than leaving it under the startup/init allowance.
    notify_workload(progress, "lesson", len(sections))
    key = fingerprint([VERSION, sections, data.model_dump(mode="json"),
                       {k: v for k, v in models.teaching_versions.items() if k != "course_plan"},
                       models.settings.lesson_generation_model_id, models.settings.lesson_review_model_id])
    path = folder / "teaching-v3-plan.json"
    saved = json.loads(path.read_text()) if path.is_file() else None
    if saved and saved.get("key") == key:
        models.course_sections, models.outline = saved["sections"], saved["outline"]
    else:
        selected, cards = [], []
        pending = []
        for index, section in enumerate(sections):
            if len(json.dumps(section, ensure_ascii=False)) > models.settings.lesson_max_source_characters:
                raise CourseError("SOURCE_WINDOW_TOO_LARGE")
            progress("GENERATE", .02 + .025 * index / len(sections))
            source, warnings = models.select_source(section)
            if all(c["text"] == "[No readable substantive teaching content in this source window.]" for c in source["chunks"]):
                pending.extend(ReviewIssue(category="source", message=w.message).model_dump() for w in warnings)
                continue
            sources = [{"source_index": i, "text": c["text"]} for i, c in enumerate(source["chunks"])]
            next_section = sections[index + 1] if index + 1 < len(sections) else None
            # Only a bounded, same-document lookahead; it is not required coverage.
            following = ([c["text"] for c in next_section["chunks"][:2]
                          if len(c["text"]) <= 6000] if next_section and
                         next_section["document_id"] == section["document_id"] else [])
            payload = {"sources": sources, "following_source": following, "language": source["source_language"]}
            for attempt in range(2):
                card = structured(models, PLAN_POLICY, payload, SectionPlan, "teaching_v3_section_plan")
                if all(0 <= c.source_index < len(sources) for c in card.concepts):
                    break
                payload["corrections"] = "Select valid explicit zero-based source indices. Never retype source equations."
            else:
                raise CourseError("PLAN_EVIDENCE_INVALID")
            concepts, seen_concepts = [], set()
            for concept in card.concepts:
                selected_chunk = source["chunks"][concept.source_index]
                # Equations and their definitions often occupy different PDF
                # chunks on the same page. Copy bounded local evidence together,
                # retaining the actual text and resolvable source chunk IDs.
                neighbours = [c for c in source["chunks"] if c["chunk_id"] == selected_chunk["chunk_id"] or
                              set(c["page_numbers"]) & set(selected_chunk["page_numbers"])]
                size = 0
                for chunk in sorted(neighbours, key=lambda c: c["chunk_id"] != selected_chunk["chunk_id"]):
                    symbol = concept.symbol if chunk is selected_chunk and concept.symbol in chunk["text"] else ""
                    if chunk["chunk_id"] in seen_concepts:
                        if symbol:
                            next(c for c in concepts if c["chunk_id"] == chunk["chunk_id"])["symbol"] = symbol
                        continue
                    if len(chunk["text"]) > 16000 or (chunk is not selected_chunk and size + len(chunk["text"]) > 6000):
                        continue
                    concepts.append({"quote": chunk["text"], "symbol": symbol, "chunk_id": chunk["chunk_id"]})
                    seen_concepts.add(chunk["chunk_id"])
                    size += len(chunk["text"])
            selected.append({**source, "v3_warnings": [w.model_dump() for w in warnings]})
            cards.append({"title": card.title, "concepts": concepts, "join_next": card.join_next and bool(following),
                          "document_id": source["document_id"], "document_version_id": source["document_version_id"]})
        if not selected:
            raise CourseError("SOURCE_EMPTY")
        merged, outline = [], []
        for source, card in zip(selected, cards, strict=True):
            can_join = (merged and outline[-1]["join_next"] and
                        all(merged[-1].get(k) == source.get(k) for k in
                            ("document_id", "document_version_id", "source_language", "source_format")))
            candidate = {**source}
            if can_join:
                previous = merged[-1]
                candidate.update(chunks=[*previous["chunks"], *source["chunks"]],
                                 pages=sorted(set(previous["pages"] + source["pages"])),
                                 window=[previous["window"][0], source["window"][1]],
                                 v3_warnings=[*previous["v3_warnings"], *source["v3_warnings"]])
                # The semantic budget measures teaching, not duplicated geometry,
                # quality and provenance metadata retained for source resolution.
                # Keep the full configured payload limit as a separate hard bound.
                teaching_size = sum(len(c["text"]) for c in candidate["chunks"])
                can_join = (teaching_size <= 24000 and
                            len(json.dumps(candidate, ensure_ascii=False)) <= models.settings.lesson_max_source_characters)
            if can_join:
                merged[-1] = candidate
                outline[-1].update(concepts=outline[-1]["concepts"] + card["concepts"],
                                   join_next=card["join_next"], windows=outline[-1]["windows"] + 1,
                                   subtopics=[*outline[-1]["subtopics"], card["title"]])
            else:
                merged.append(source)
                outline.append({**card, "windows": 1, "subtopics": [card["title"]]})
        for i, source in enumerate(merged):
            source["v3_index"] = i
        merged[-1]["v3_warnings"].extend(pending)
        models.course_sections, models.outline = merged, outline
        write_json(path, {"key": key, "sections": merged, "outline": outline})
    # Definitions used for continuity need honest, resolvable source references.
    # Extend evidence only with bounded exact definitions from this document;
    # preserve the primary teaching sequence/coverage separately.
    pool = {(s["document_id"], s["document_version_id"], c["chunk_id"]): c
            for s in models.course_sections for c in s["chunks"]}
    for index, source in enumerate(models.course_sections):
        source.setdefault("v3_primary_chunk_ids", [c["chunk_id"] for c in source["chunks"]])
        primary_ids = set(source["v3_primary_chunk_ids"])
        source.setdefault("v3_primary_pages", sorted({n for c in source["chunks"] if c["chunk_id"] in primary_ids
                                                       for n in c["page_numbers"]}))
        seen = {c["chunk_id"] for c in source["chunks"]}
        count, size = 0, 0
        for definition in context(models, index).get("shared_definitions", []):
            chunk = pool.get((source["document_id"], source["document_version_id"], definition["chunk_id"]))
            if chunk is None or chunk["chunk_id"] in seen or count >= 6 or size + len(chunk["text"]) > 12000:
                continue
            candidate = {**source, "chunks": [*source["chunks"], chunk],
                         "pages": sorted(set(source["pages"] + chunk["page_numbers"]))}
            if len(json.dumps(candidate, ensure_ascii=False)) <= models.settings.lesson_max_source_characters:
                source.update(candidate)
                seen.add(chunk["chunk_id"])
                count += 1
                size += len(chunk["text"])
    models.taught = {}
    models.max_words = getattr(data.options, "max_chunk_words", max(500, data.options.target_chunk_words * 2))
    models.teaching_versions["course_plan"] = fingerprint([models.course_sections, models.outline])
    return models.course_sections


def context(models, index):
    total = len(models.outline)
    if not total:
        return {"position": "teacher_revision", "max_words_per_unit": models.max_words}
    outline = [{"index": i, "title": card["title"], "subtopics": card.get("subtopics", [card["title"]])[:8]}
               for i, card in enumerate(models.outline)]
    # Full outline is persisted; each call has bounded ordered navigation and exact
    # definitions relevant to this and neighbouring topics, even on long courses.
    visible = sorted(set([0, total - 1, *range(max(0, index - 3), min(total, index + 4))]))
    concepts = []
    document = (models.outline[index].get("document_id"), models.outline[index].get("document_version_id"))
    for i in range(max(0, index - 2), min(total, index + 2)):
        card = models.outline[i]
        if (card.get("document_id"), card.get("document_version_id")) == document:
            concepts.extend(card["concepts"])
    symbols = {c["symbol"] for c in concepts if c["symbol"]}
    for card in models.outline:
        if (card.get("document_id"), card.get("document_version_id")) != document:
            continue
        for c in card["concepts"]:
            if c["symbol"] in symbols and c not in concepts:
                concepts.append(c)
    bounded, size = [], 0
    for item in concepts:
        if size + len(item["quote"]) <= 16000:
            bounded.append(item)
            size += len(item["quote"])
    previous = []
    size = 0
    for i in range(index - 1, max(-1, index - 4), -1):
        for text in models.taught.get(i, []):
            if size + len(text) <= 12000:
                previous.append({"section": i, "text": text})
                size += len(text)
    return {"section_index": index, "section_count": total, "is_first": index == 0,
            "is_last": index == total - 1, "outline": [outline[i] for i in visible],
            "already_taught": sorted(previous, key=lambda item: item["section"]),
            "shared_definitions": bounded, "max_words_per_unit": models.max_words}
