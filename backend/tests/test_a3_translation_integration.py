"""Real PostgreSQL/Qdrant checkpoints and target-language TTS, with fake providers."""

import json

from test_a2_integration import submit
from test_a3 import Polly, request_data
from test_a3_integration import accept, process
from test_a3_translation import TranslationClient, review_check

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.lesson_translation import LessonTranslator

pytest_plugins = ["test_a1_integration", "test_a3_integration"]


class ItalianPolly(Polly):
    def describe_voices(self, **kwargs):
        return {"Voices": [{"Id": "Bianca", "LanguageCode": "it-IT", "SupportedEngines": ["neural"]}]}


def install_translation(lesson, client):
    settings, _, worker, _, _, models = lesson
    translator = LessonTranslator(settings, client)
    models.translate_section = lambda draft, source, target, *, learner_level: translator.translate(
        draft, source, target, learner_level)
    worker.speech.client = ItalianPolly()
    original = models.generate_section
    languages = []

    def generate(section, language, *args, **kwargs):
        languages.append(language)
        return original(section, language, *args, **kwargs)

    models.generate_section = generate
    return languages


def test_italian_translation_is_committed_before_italian_speech_and_returned_by_a5(integration, lesson):
    languages = install_translation(lesson, TranslationClient())
    target, doc, _ = accept(integration, lesson, language="it", voice="Bianca",
                            options={"learner_level": "beginner", "target_chunk_words": 30})
    original = lesson[2].speech.synthesize

    def synthesize(parts, voice, language):
        saved = lesson[1].prepared_chunks(target["job_id"])
        assert saved and all("Una frase didattica supportata." in row["text"] for row in saved)
        assert all("A supported teaching sentence." not in row["text"] for row in saved)
        assert all(row["versions"]["source_languages"] == "en" for row in saved)
        assert all(row["versions"]["translation_applied"] == "true" for row in saved)
        assert all(row["sources"][0]["document_id"] == doc["document_id"] for row in saved)
        assert "Una frase didattica supportata." in " ".join(parts)
        assert voice == "Bianca" and language == "it-IT"
        return original(parts, voice, language)

    lesson[2].speech.synthesize = synthesize
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    result = lesson[3].result(row)
    assert result.language == "it" and result.voice == "Bianca" and result.learner_level == "beginner"
    assert languages == ["en"]
    saved = lesson[1].prepared_chunks(target["job_id"])
    assert [r["text"] for r in saved] == [c.text for c in result.chunks]
    assert all("Una frase didattica supportata." in c.ssml for c in result.chunks)
    folder = lesson[2].staging.job_dir(target["job_id"])
    assert "A supported teaching sentence." in json.loads((folder / "source-section-0.json").read_text())["draft"]["paragraphs"][0]["text"]
    assert "Una frase didattica supportata." in json.loads((folder / "section-0.json").read_text())["draft"]["paragraphs"][0]["text"]


def test_rejected_translation_never_reaches_pg_or_tts_and_retry_reuses_generation(integration, lesson):
    client = TranslationClient(check=review_check("target_language", quote="Una frase didattica supportata."))
    languages = install_translation(lesson, client)
    target, _, _ = accept(integration, lesson, language="it", voice="Bianca")
    assert process(lesson)["error_code"] == "LESSON_TRANSLATION_REJECTED"
    assert not lesson[1].prepared_chunks(target["job_id"])
    assert not lesson[2].speech.client.calls and languages == ["en"]
    assert len(client.calls) == 4
    client.check["target_language"] = True
    client.check["issues"] = []
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert languages == ["en"] and len(client.calls) == 6


def test_corrected_translation_is_rechecked_before_any_pg_or_speech_write(integration, lesson):
    client = TranslationClient(checks=[
        review_check("faithful", quote="Una frase didattica supportata."), review_check()])
    languages = install_translation(lesson, client)
    target, _, _ = accept(integration, lesson, language="it", voice="Bianca")
    original = lesson[2].speech.synthesize

    def synthesize(parts, voice, language):
        assert len(client.calls) == 4
        saved = lesson[1].prepared_chunks(target["job_id"])
        assert saved and all("Una frase didattica supportata." in row["text"] for row in saved)
        assert all("A supported teaching sentence." not in row["text"] for row in saved)
        return original(parts, voice, language)

    lesson[2].speech.synthesize = synthesize
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert languages == ["en"] and len(client.calls) == 4
    saved = lesson[1].prepared_chunks(target["job_id"])
    assert [chunk["text"] for chunk in saved] == [chunk.text for chunk in lesson[3].result(row).chunks]


def test_invalid_review_evidence_never_reaches_pg_or_tts(integration, lesson):
    check = review_check("target_language", quote="This quote is absent from the generated translation.")
    client = TranslationClient(check=check)
    languages = install_translation(lesson, client)
    target, _, _ = accept(integration, lesson, language="it", voice="Bianca")
    assert process(lesson)["error_code"] == "LESSON_TRANSLATION_INVALID"
    assert not lesson[1].prepared_chunks(target["job_id"])
    assert not lesson[2].speech.client.calls
    assert languages == ["en"] and len(client.calls) == 2


def test_speech_retry_reuses_checked_translation(integration, lesson):
    client = TranslationClient()
    languages = install_translation(lesson, client)
    target, _, _ = accept(integration, lesson, language="it", voice="Bianca")
    original = lesson[2].speech.synthesize

    def fail(*args):
        raise IngestionError("TEMPORARY_SPEECH", retryable=True)

    lesson[2].speech.synthesize = fail
    assert process(lesson)["status"] == "QUEUED"
    saved = lesson[1].prepared_chunks(target["job_id"])
    lesson[2].speech.synthesize = original
    with lesson[1].connect() as conn:
        conn.execute("UPDATE lesson_jobs SET available_at=clock_timestamp() WHERE job_id=%s", (target["job_id"],))
    assert process(lesson)["status"] == "DONE"
    assert languages == ["en"] and len(client.calls) == 2
    after = lesson[1].prepared_chunks(target["job_id"])
    assert [(r["text"], r["created_at"]) for r in after] == [(r["text"], r["created_at"]) for r in saved]


def test_mixed_document_labels_translate_only_the_other_language(integration, lesson):
    client = TranslationClient()
    languages = install_translation(lesson, client)
    english, italian = submit(integration), submit(integration)
    for doc in (english, italian):
        integration[4].process(integration[1].claim(integration[0].lease_seconds))
    with integration[1].connect() as conn:
        conn.execute("UPDATE ingestion_jobs SET metadata=jsonb_set(metadata,'{language}','\"it\"'::jsonb) WHERE job_id=%s",
                     (italian["job_id"],))
    original = lesson[5].draft

    def generate_source(section, language):
        draft = original(section, language)
        if language == "it":
            for paragraph in draft.paragraphs:
                paragraph.text = paragraph.text.replace("A supported teaching sentence.", "Una frase didattica supportata.")
        return draft

    lesson[5].draft = generate_source
    data = LessonInput.model_validate(request_data(document_ids=[english["document_id"], italian["document_id"]],
                                                   language="it", voice="Bianca"))
    lesson[3].submit(data, lesson[4], None)
    row = process(lesson)
    assert row["status"] == "DONE", row["error_code"]
    assert languages == ["en", "it"] and len(client.calls) == 2
    result = lesson[3].result(row)
    assert result.versions["source_languages"] == "en,it"
    assert all("Una frase didattica supportata." in c.text for c in result.chunks)
