"""Cache individual complete verdicts within the existing job erasure lifetime."""

import json
from time import monotonic

from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.teaching_v2.contracts import structured
from upraise_ai.services.teaching_v3.contracts import fingerprint

from .prompts import VERSION


def _record(models, stage, *, cached=False, seconds=0):
    profile = getattr(models, "v31_review_profile", {})
    item = profile.setdefault(stage, {"cache_hits": 0, "provider_checks": 0, "provider_seconds": 0.0})
    item["cache_hits" if cached else "provider_checks"] += 1
    item["provider_seconds"] = round(item["provider_seconds"] + seconds, 3)
    models.v31_review_profile = profile
    if models.diagnostic_dir:
        write_json(models.diagnostic_dir.parent / "teaching-v31-review-profile.json", profile)


def verdict(models, policy, payload, schema, stage, *, reviewer=False, max_tokens=2500):
    identity = [VERSION, models.teaching_versions, models.draft_review_recovery, policy, payload,
                schema.model_json_schema(), stage, reviewer, models.settings.lesson_generation_model_id,
                models.settings.lesson_review_model_id]
    key = fingerprint(identity)
    path = (models.diagnostic_dir.parent / "teaching-v31-verdicts" / f"{key}.json"
            if models.diagnostic_dir else None)
    if path and path.is_file():
        try:
            saved = json.loads(path.read_text())
            if saved.get("key") == key and saved.get("hash") == fingerprint(saved["verdict"]):
                value = schema.model_validate(saved["verdict"])
                _record(models, stage, cached=True)
                return value
        except (KeyError, ValueError, TypeError):
            pass
    start = monotonic()
    try:
        value = structured(models, policy, payload, schema, stage, reviewer=reviewer, max_tokens=max_tokens)
    finally:
        _record(models, stage, seconds=monotonic() - start)
    if path:
        parsed = value.model_dump(mode="json")
        write_json(path, {"key": key, "hash": fingerprint(parsed), "verdict": parsed})
    return value
