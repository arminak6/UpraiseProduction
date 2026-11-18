"""Default completeness guard, with an explicit available-content generation choice."""

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import SourceReadiness

AVAILABLE_CONTENT_POLICY = (
    'This request explicitly permits a draft from the available ingested content. '
    'Use only source text actually present in chunks. Some source content is missing or unchecked. '
    'Do not infer the contents of source-region or unverified-visual placeholders, '
    'and do not claim complete coverage of the original source.')


def add_source_readiness(section, document):
    if readiness := document.get('generation_source_readiness'):
        section['ingestion_readiness'] = readiness
        section['source_content_policy'] = AVAILABLE_CONTENT_POLICY


def powerpoint_readiness(row):
    if row['metadata'].get('source_format') != 'pptx' or row['status'] != 'DONE':
        return None
    result = row.get('result') or {}
    if saved := result.get('ingestion_readiness'):
        return SourceReadiness.model_validate(saved)
    return SourceReadiness(status='INCOMPLETE', issues=['POWERPOINT_REINGESTION_REQUIRED'])


def require_complete_powerpoint(row, *, allow_incomplete_source=False):
    readiness = powerpoint_readiness(row)
    if readiness and readiness.status != 'COMPLETE' and allow_incomplete_source is not True:
        raise IngestionError('POWERPOINT_INGESTION_REVIEW_REQUIRED', status=409)
