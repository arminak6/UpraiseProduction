"""Content-free version snapshots. No provider clients or storage connections."""

import ast
import hashlib
import json
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from upraise_ai.core.versions import (
    APPLICATION_VERSION,
    COGNITIVE_LOAD_POLICY_VERSION,
    COGNITIVE_LOAD_THRESHOLD,
    INTERRUPT_PIPELINE_VERSION,
)
from upraise_ai.schemas.versions import (
    ApplicationVersion,
    CognitiveLoadVersion,
    FusionRule,
    ModelVersion,
    PipelineVersion,
    ThresholdMethod,
    VersionItem,
    VersionStatus,
)

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parent.parent


def digest_files(root: Path, paths: list[str]) -> str | None:
    """Hash sorted relative names and normalized source bytes, never data files."""
    digest = hashlib.sha256()
    try:
        for name in sorted(set(paths)):
            content = (root / name).read_bytes().replace(b"\r\n", b"\n")
            digest.update(name.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    except OSError:
        return None
    return digest.hexdigest() if paths else None


@lru_cache(maxsize=1)
def build_identity() -> tuple[str | None, str]:
    paths = [path.relative_to(ROOT).as_posix() for path in SOURCE.rglob("*.py")]
    # These are the exact source/dependency manifests packaged in our image.
    manifests = ["pyproject.toml", "uv.lock", "math_runtime/package.json",
                 "math_runtime/package-lock.json", "math_runtime/math_speech.mjs"]
    complete = all((ROOT / path).is_file() for path in manifests)
    paths.extend(path for path in manifests if (ROOT / path).is_file())
    digest = digest_files(ROOT, paths)
    return ("sha256:" + digest, "source_and_locks_sha256" if complete else "source_sha256") if digest else (None, "UNAVAILABLE")


@lru_cache(maxsize=64)
def source_constant(name: str, constant: str, bindings: tuple[tuple[str, str | None], ...] = ()) -> str | None:
    # Read literal labels without importing the legacy AI/vision implementations.
    try:
        tree = ast.parse((SOURCE / name).read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == constant for t in node.targets):
                if isinstance(node.value, ast.JoinedStr):
                    context, parts = dict(bindings), []
                    for part in node.value.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            parts.append(part.value)
                        elif (isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                              and isinstance(context.get(part.value.id), str) and part.format_spec is None):
                            parts.append(context[part.value.id])
                        else:
                            return None
                    value = "".join(parts)
                else:
                    value = ast.literal_eval(node.value)
                return value if isinstance(value, str) else None
    except (OSError, SyntaxError, ValueError):
        pass
    return None


@lru_cache(maxsize=32)
def prompt_version(label: str | None, paths: tuple[str, ...]) -> VersionItem:
    digest = digest_files(SOURCE, list(paths))
    return VersionItem(version=label, sha256=digest,
                       status="AVAILABLE" if label is not None and digest is not None else "UNAVAILABLE")


@lru_cache(maxsize=1)
def dependency_versions() -> dict[str, str | None]:
    result = {}
    for name in ("docling", "langgraph", "boto3", "amazon-transcribe"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def fingerprint(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class VersionCatalog:
    def __init__(self, settings):
        build_id, kind = build_identity()
        level = source_constant("services/lesson_prompts.py", "LEARNER_LEVEL_PROMPT_VERSION")
        draft = source_constant("services/v3/models.py", "EASY_LECTURE_PROMPT_VERSION")
        labels = (("EASY_LECTURE_PROMPT_VERSION", draft), ("LEARNER_LEVEL_PROMPT_VERSION", level))
        common = ("services/lesson_models.py", "services/lesson_prompts.py", "services/v3/prompts.py",
                  "services/v3/bedrock_teacher.py")
        prompts = {
            "lesson_draft": prompt_version(source_constant("services/lesson_models.py", "DRAFT_PROMPT", labels), common),
            "lesson_review": prompt_version(source_constant("services/lesson_models.py", "REVIEW_PROMPT", labels), common),
            "lesson_finalizer": prompt_version(source_constant("services/lesson_models.py", "FINALIZER_PROMPT", labels), common),
            "learner_level": prompt_version(level, ("services/lesson_prompts.py",)),
            'alignment': prompt_version(source_constant('services/aligned_models.py', 'ALIGNMENT_PROMPT_VERSION'),
                                       ('services/aligned_models.py', 'services/aligned_pipeline.py', 'services/aligned_responses.py')),
            "translation": prompt_version(source_constant("services/lesson_translation.py", "TRANSLATION_PROMPT_VERSION"),
                                          ("services/lesson_translation.py", "services/lesson_models.py")),
            "revision": prompt_version(source_constant("services/revision_models.py", "REVISION_PROMPT_VERSION"),
                                       (*common, "services/revision_models.py")),
            "interrupts": prompt_version(INTERRUPT_PIPELINE_VERSION,
                                         ("services/interrupt_models.py", "services/lesson_prompts.py",
                                          "services/v3/prompts.py", "workflows/interrupts.py",
                                          "schemas/interrupts.py", "services/v3/bedrock_teacher.py")),
            "math_speech": prompt_version(source_constant("services/v3/math_speech_v4.py", "SEMANTIC_NARRATION_CACHE_VERSION"),
                                          ("services/v3/math_speech_v4.py", "services/v3/bedrock_teacher.py",
                                           "services/v3/prompts.py", "services/lesson_models.py")),
        }
        for role, constant in (("figure_description", "PROMPT_VERSION"), ("formula_transcription", "FORMULA_PROMPT_VERSION"),
                               ("inline_math", "INLINE_MATH_PROMPT_VERSION"), ("source_verification", "VERIFICATION_PROMPT_VERSION")):
            prompts[role] = prompt_version(source_constant("services/pdf/vision.py", constant), ("services/pdf/vision.py",))
        models = {role: ModelVersion(provider="AWS Bedrock", model_id=getattr(settings, field)) for role, field in (
            ("embeddings", "embedding_model_id"), ("figure_description", "vision_model_id"),
            ("formula_transcription", "math_vision_model_id"), ("source_verification", "fidelity_vision_model_id"),
            ("lesson_generator", "lesson_generation_model_id"), ("lesson_reviewer", "lesson_review_model_id"),
            ("lesson_finalizer", "lesson_generation_model_id"), ("translation", "lesson_generation_model_id"),
            ("translation_reviewer", "lesson_review_model_id"), ("revision_generator", "lesson_generation_model_id"),
            ("revision_reviewer", "lesson_review_model_id"), ("interrupt_router", "lesson_generation_model_id"),
            ("interrupt_teacher", "lesson_generation_model_id"), ("interrupt_output_check", "lesson_review_model_id"),
        )}
        models["tts"] = ModelVersion(provider="Amazon Polly", engine=settings.polly_engine, version_status="PROVIDER_MANAGED")
        models["stt"] = ModelVersion(provider="Amazon Transcribe", version_status="PROVIDER_MANAGED")
        payload = {
            "schema_version": "c5-v1",
            "application": ApplicationVersion(version=APPLICATION_VERSION, build_id=build_id, build_id_kind=kind,
                                              dependencies=dependency_versions()).model_dump(),
            "models": {key: value.model_dump() for key, value in models.items()},
            "prompts": {key: value.model_dump() for key, value in prompts.items()},
            "pipelines": {
                "ingestion": PipelineVersion(version=settings.pipeline_signature, parameters={
                    "aws_region": settings.aws_region, "embedding_dimension": settings.embedding_dimension,
                    "enable_ocr": settings.enable_ocr, "enable_formula_enrichment": settings.enable_formula_enrichment,
                    "describe_images": settings.describe_images, "chunk_max_tokens": settings.chunk_max_tokens,
                    "chunk_overlap_tokens": settings.chunk_overlap_tokens}).model_dump(),
                "generation": PipelineVersion(version=settings.lesson_signature, parameters={
                    "aws_region": settings.aws_region, "max_source_characters": settings.lesson_max_source_characters}).model_dump(),
                'aligned_generation': PipelineVersion(version=settings.lesson_v2_signature, parameters={
                    'levels': ','.join(settings.lesson_level_profiles), 'profiles_sha256': fingerprint(settings.lesson_level_profiles),
                    'plan_level': settings.lesson_plan_level, 'batch_units': settings.lesson_alignment_batch_units,
                    'chunk_plan': 'aligned-plan-v1', 'default_target_chunk_words': 200,
                    'default_max_chunk_words': 500,
                    'response_format': source_constant('services/aligned_responses.py', 'RESPONSE_FORMAT_VERSION'),
                    'review_strategy': source_constant('services/aligned_models.py', 'REVIEW_STRATEGY_VERSION'),
                    'post_edit_material_review': 'deferred',
                    'math_validation': source_constant('services/aligned_models.py', 'MATH_VALIDATION_VERSION')}).model_dump(),
                "revision": PipelineVersion(version=settings.lesson_signature, parameters={
                    "strategy": "three_role_single_chunk_revision"}).model_dump(),
                "interrupts": PipelineVersion(version=INTERRUPT_PIPELINE_VERSION, parameters={
                    "source_characters": settings.interrupt_source_characters, "grounding_verifier_enabled": False}).model_dump(),
            },
            "cognitive_load": CognitiveLoadVersion(fusion_rule=FusionRule(), threshold_method=ThresholdMethod(
                version=COGNITIVE_LOAD_POLICY_VERSION, value=COGNITIVE_LOAD_THRESHOLD)).model_dump(),
        }
        self._snapshot = VersionStatus(**payload, configuration_id=fingerprint(payload))

    def snapshot(self) -> VersionStatus:
        return self._snapshot.model_copy(deep=True)

    def references(self, prefix="") -> dict[str, str]:
        result = {prefix + "configuration_id": self._snapshot.configuration_id}
        if self._snapshot.application.build_id:
            result[prefix + "build_id"] = self._snapshot.application.build_id
        return result
