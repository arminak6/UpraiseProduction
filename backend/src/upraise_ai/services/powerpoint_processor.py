"""Native PPTX content, explicit notes policy, and source-bound rendered slide checks."""

import json
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid, sha256_file
from upraise_ai.schemas.documents import (
    BoundingBox,
    ContentQuality,
    DocumentElement,
    ElementType,
    ProcessedDocument,
    QualityStatus,
    SourceReadiness,
)
from upraise_ai.services.aws import aws_session
from upraise_ai.services.chunking import StructureAwareChunker
from upraise_ai.services.embeddings import BedrockTitanEmbeddingProvider
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.pdf.parser import LABEL_MAP, DoclingPdfParser
from upraise_ai.services.pdf.vision import SLIDE_VERIFICATION_PROMPT_VERSION, BedrockImageDescriber
from upraise_ai.services.powerpoint_layout import number_tokens, slide_inventory, text_tokens
from upraise_ai.services.powerpoint_vision import PowerPointImageDescriber
from upraise_ai.services.quality import aggregate_quality, quality_summary
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.workers.budgets import notify_workload

POWERPOINT_PARSER_VERSION = 'powerpoint-layout-and-complete-slides-v2'
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'


def validate_powerpoint_input(source, settings):
    if source.stat().st_size > settings.max_powerpoint_size_mb * 1024**2:
        raise IngestionError('POWERPOINT_TOO_LARGE', status=413)
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if (len(entries) > 10000 or len({e.filename for e in entries}) != len(entries)
                    or sum(e.file_size for e in entries) > settings.max_powerpoint_uncompressed_mb * 1024**2):
                raise IngestionError('POWERPOINT_ARCHIVE_LIMIT_EXCEEDED', status=422)
            if any(e.flag_bits & 1 for e in entries):
                raise IngestionError('POWERPOINT_ENCRYPTED_UNSUPPORTED', status=422)
            if any(e.filename.lower().endswith('vbaproject.bin') for e in entries):
                raise IngestionError('POWERPOINT_MACROS_UNSUPPORTED', status=422)
            for entry in entries:
                if entry.filename.startswith(('/', '\\')) or '..' in Path(entry.filename).parts:
                    raise IngestionError('POWERPOINT_INVALID_DOCUMENT', status=422)
                if entry.filename.endswith(('.xml', '.rels')):
                    raw = archive.read(entry)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                        raise IngestionError('POWERPOINT_INVALID_DOCUMENT', status=422)
                    if entry.filename.endswith('.rels'):
                        for relation in ElementTree.fromstring(raw):
                            if (relation.get('TargetMode') == 'External'
                                    and not relation.get('Type', '').endswith('/hyperlink')):
                                raise IngestionError('POWERPOINT_EXTERNAL_CONTENT_UNSUPPORTED', status=422)
            types = archive.read('[Content_Types].xml')
            if b'presentationml.presentation.main+xml' not in types:
                raise IngestionError('POWERPOINT_INVALID_DOCUMENT', status=422)
            root = ElementTree.fromstring(archive.read('ppt/presentation.xml'))
            if root.tag != '{http://schemas.openxmlformats.org/presentationml/2006/main}presentation':
                raise IngestionError('POWERPOINT_INVALID_DOCUMENT', status=422)
            count = sum(1 for item in root.iter() if item.tag.endswith('}sldId'))
            if count < 1:
                raise IngestionError('POWERPOINT_EMPTY_PRESENTATION', status=422)
            if count > settings.max_powerpoint_slides:
                raise IngestionError('POWERPOINT_TOO_MANY_SLIDES', status=422)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError):
        raise IngestionError('POWERPOINT_INVALID_DOCUMENT', status=422) from None


def render_powerpoint(source, destination, settings):
    executable = shutil.which('soffice')
    if not executable:
        raise IngestionError('POWERPOINT_CONVERTER_UNAVAILABLE', status=503, retryable=True)
    destination.mkdir(parents=True, exist_ok=True)
    # Render all slides, including hidden ones, to keep original slide ordinals.
    # The ingestion policy selects which of those slides may enter the course.
    options = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"},"ExportNotesPages":{"type":"boolean","value":"false"}}'
    try:
        subprocess.run([executable, '-env:UserInstallation=' + (destination / 'profile').resolve().as_uri(),
                        '--headless', '--convert-to', options, '--outdir', str(destination), str(source)],
                       capture_output=True, timeout=settings.powerpoint_conversion_timeout_seconds, check=True)
    except subprocess.TimeoutExpired:
        raise IngestionError('POWERPOINT_CONVERSION_TIMEOUT', status=422) from None
    except subprocess.CalledProcessError:
        raise IngestionError('POWERPOINT_CONVERSION_FAILED', status=422) from None
    rendered = destination / (source.stem + '.pdf')
    if not rendered.is_file() or not rendered.stat().st_size:
        raise IngestionError('POWERPOINT_CONVERSION_FAILED', status=422)
    return rendered


def native_elements(slide, digest):
    """Text boxes/groups in geometric order, with native table merges retained."""
    elements, number = [], slide['slide_number']
    for region in slide['regions']:
        paragraphs = region.get('paragraphs', [{'text': region['text'], 'level': 0}])
        for index, paragraph in enumerate(paragraphs):
            ref = region['reference'] + f':paragraph:{index}'
            kind = ElementType.TABLE if region['kind'] == 'table' else (
                ElementType.TITLE if paragraph['text'] == slide['title'] else
                ElementType.LIST_ITEM if paragraph['level'] or paragraph.get('bullet') else ElementType.TEXT)
            box = region['box']
            element = DocumentElement(element_id=deterministic_uuid(f'{digest}:pptx:{ref}'),
                element_index=0, element_type=kind, original_text=paragraph['text'], source_text=paragraph['text'],
                source_reference=ref, heading_path=[f'Slide {number}', slide['title']],
                bounding_box=BoundingBox(left=box[0], top=box[1], right=box[2], bottom=box[3], coordinate_origin='TOPLEFT'),
                metadata={'source_format': 'pptx', 'slide_number': number, 'content_layer': 'slide',
                          'native_level': paragraph['level'], 'coordinate_units': 'normalized_slide',
                          'shape_reference': region['reference']},
                quality=ContentQuality(checks=['native_powerpoint_structure', 'geometric_reading_order']))
            if kind is ElementType.TABLE:
                element.structured_content = element.source_structured_content = region['html']
            elements.append(element)
    return elements


def checked(elements, model_id):
    for element in elements:
        element.quality = ContentQuality(status=QualityStatus.AUTO_CHECKED,
            checks=[*element.quality.checks, 'complete_slide_source_comparison', 'native_text_coverage'],
            evidence_complete=True, verification_model_id=model_id,
            verification_prompt_version=SLIDE_VERIFICATION_PROMPT_VERSION)


def flag(elements, message):
    for element in elements:
        element.quality.status = QualityStatus.NEEDS_REVIEW
        element.quality.issues.append(message)


class PowerPointProcessor:
    def __init__(self, settings, *, embedder=None, converter=None, renderer=None, describer=None, verifier=None):
        self.settings, self.embedder, self.converter = settings, embedder, converter
        self.renderer, self.describer, self.verifier = renderer, describer, verifier

    def prepare(self, source, metadata, document_id, job_dir, token, progress):
        import pypdfium2 as pdfium
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import ContentLayer
        from pypdf import PdfReader

        settings = self.settings
        validate_powerpoint_input(source, settings)
        digest, versions = sha256_file(source), VersionCatalog(settings).references()
        checkpoint = job_dir / 'prepared.json'
        if checkpoint.is_file():
            try:
                saved = json.loads(checkpoint.read_bytes())
                document = ProcessedDocument.model_validate(saved['document'])
                if (document.document_id == document_id and document.metadata == metadata
                        and document.source_hash == digest and document.pipeline_version == settings.pipeline_signature
                        and document.parser_version == POWERPOINT_PARSER_VERSION and saved['versions'] == versions):
                    return document, saved['vectors']
            except (ValueError, KeyError, OSError):
                pass
        artifacts = job_dir / 'powerpoint-artifacts'
        artifacts.mkdir(parents=True, exist_ok=True)
        slides = slide_inventory(source, artifacts)
        included = {s['slide_number']: s for s in slides if metadata.include_hidden_slides or not s['hidden']}
        if not included:
            raise IngestionError('NO_RETRIEVABLE_CONTENT')
        notify_workload(progress, 'parse', len(slides))
        progress('parse', .08, 'Reading native PowerPoint slides.')
        warnings = []
        native, docling_complete = None, False
        try:
            converted = (self.converter or DocumentConverter(allowed_formats=[InputFormat.PPTX])).convert(source)
            docling_complete = str(getattr(converted.status, 'value', converted.status)) == 'success'
            native = converted.document
            write_json(artifacts / 'docling.json', native.model_dump(mode='json'))
        except Exception:
            # Docling is an independent extraction check, not the only way to
            # read a valid deck. Native XML plus full visual comparison remains
            # available; a failed comparison still blocks generation.
            native = None
        if not docling_complete:
            warnings.append('POWERPOINT_DOCLING_FALLBACK')
        docling_by_slide = {n: [] for n in included}
        items = native.iterate_items(included_content_layers={ContentLayer.BODY}) if native is not None else []
        for item, level in items:
            label = DoclingPdfParser._label_value(getattr(item, 'label', None))
            kind = LABEL_MAP.get(label, ElementType.OTHER)
            text = DoclingPdfParser._text_for_item(item, native, kind)
            if not text.strip():
                continue
            numbers = {p.page_no for p in getattr(item, 'prov', [])}
            if len(numbers) != 1:
                docling_complete = False
                warnings.append('POWERPOINT_DOCLING_PROVENANCE_INCOMPLETE')
                continue
            number = numbers.pop()
            if number not in included:
                continue
            ref = DoclingPdfParser._source_reference(item)
            element = DocumentElement(element_id=deterministic_uuid(f'{digest}:pptx:{number}:{ref}'),
                element_index=0, element_type=kind, original_text=text, source_text=text,
                source_reference=ref, parser_label=label, heading_path=[f'Slide {number}', included[number]['title']],
                metadata={'source_format': 'pptx', 'slide_number': number, 'content_layer': 'slide', 'native_level': level},
                quality=ContentQuality(checks=['native_powerpoint_structure']))
            if kind is ElementType.TABLE:
                element.structured_content = DoclingPdfParser._table_html(item, native)
                element.source_structured_content = element.structured_content
            docling_by_slide[number].append(element)
        by_slide = {n: native_elements(s, digest) for n, s in included.items()}
        write_json(artifacts / 'native-elements.json', {
            'source_hash': digest, 'slides': {n: [e.model_dump(mode='json') for e in es] for n, es in by_slide.items()}})
        progress('parse', .30, 'Rendering slides for source checks.')
        rendered = (self.renderer or render_powerpoint)(source, artifacts / 'rendered', settings)
        pdf = PdfReader(rendered)
        if len(pdf.pages) != len(slides):
            raise IngestionError('POWERPOINT_RENDER_SLIDE_COUNT_MISMATCH', status=422)
        audits, manifest, elements = [], [], []
        describer, verifier = self.describer, self.verifier
        if settings.describe_images and (describer is None or verifier is None):
            session = aws_session(settings)
            describer = describer or PowerPointImageDescriber(session=session, model_id=settings.vision_model_id,
                                                          cache_dir=artifacts / 'cache')
            verifier = verifier or BedrockImageDescriber(session=session, model_id=settings.fidelity_vision_model_id,
                                                        cache_dir=artifacts / 'cache')
        notify_workload(progress, 'enrich', len(included) * 4)
        rendered_doc = pdfium.PdfDocument(str(rendered))
        try:
            for number, slide in included.items():
                progress('enrich', .35 + .25 * (number - 1) / len(slides), 'Checking slide content against its rendering.')
                selected = by_slide[number]
                image_path = artifacts / f'slide-{number}.png'
                page = rendered_doc[number - 1]
                bitmap = page.render(scale=1.5)
                image = bitmap.to_pil()
                image.save(image_path)
                image.close()
                bitmap.close()
                page.close()
                body = '\n'.join(e.structured_content or e.original_text for e in selected)
                native_tokens = text_tokens(slide['native_text'])
                rendered_tokens = Counter(re.findall(r'\w+', (pdf.pages[number - 1].extract_text() or '').casefold()))
                missing = sum((rendered_tokens - native_tokens).values()) / max(1, sum(rendered_tokens.values()))
                docling_extra = text_tokens('\n'.join(e.original_text for e in docling_by_slide[number])) - native_tokens
                audit = {'slide_number': number, 'missing_rendered_token_ratio': missing,
                         'text_source_comparison': None, 'visual_source_comparison': None,
                         'native_editable_token_count': sum(native_tokens.values()), 'issues': list(slide['issues'])}
                audit['evidence_warnings'] = slide['evidence_warnings']
                warnings.extend(slide['evidence_warnings'])
                if missing > .05:
                    warnings.append('POWERPOINT_RENDER_TEXT_MISMATCH')
                paths = [image_path, *[p['path'] for p in slide['pictures']]]
                refs = [f'slide:{number}', *[p['reference'] for p in slide['pictures']]]
                complete, candidate = False, None
                needs_visual = slide['has_visual'] or bool(docling_extra) or missing > .05 or not docling_complete
                if settings.describe_images:
                    def compare(candidate):
                        return verifier.verify_source(paths, candidate=candidate, content_type='slide',
                            page_numbers=[], source_references=refs, slide_numbers=[number] * len(paths))
                    if body.strip() and not needs_visual:
                        check = compare(body)
                        audit['text_source_comparison'] = complete = bool(check.passed)
                        audit['native_comparison_issues'] = list(getattr(check, 'issues', []))
                    if not complete and (body.strip() or slide['has_visual']):
                        outcome = describer.transcribe_slide(paths, slide_number=number, source_references=refs,
                                                            native_text=body)
                        candidate = outcome.description
                        if candidate:
                            absent = native_tokens - text_tokens(candidate)
                            absent_numbers = number_tokens(slide['native_text']) - number_tokens(candidate)
                            audit['missing_candidate_native_tokens'] = sum(absent.values())
                            if not absent and not absent_numbers:
                                check = compare(candidate)
                                audit['visual_source_comparison'] = complete = bool(check.passed)
                                audit['transcription_comparison_issues'] = list(getattr(check, 'issues', []))
                                if complete:
                                    selected = [DocumentElement(
                                        element_id=deterministic_uuid(f'{digest}:pptx:{number}:verified-slide'),
                                        element_index=0, element_type=ElementType.TEXT, original_text=candidate,
                                        source_text=body, candidate_text=candidate, asset_path=str(image_path),
                                        source_reference=f'slide:{number}:verified-slide',
                                        heading_path=[f'Slide {number}', slide['title']],
                                        metadata={'source_format': 'pptx', 'slide_number': number,
                                                  'content_layer': 'verified_slide', 'native_references': [
                                                      e.source_reference for e in by_slide[number]]})]
                            else:
                                audit['issues'].append('POWERPOINT_TRANSCRIPTION_NATIVE_TEXT_MISSING')
                        else:
                            audit['issues'].append(outcome.warning or 'POWERPOINT_TRANSCRIPTION_UNAVAILABLE')
                else:
                    audit['issues'].append('POWERPOINT_SOURCE_CHECK_DISABLED')
                # A genuinely empty slide has no teaching content to verify.
                if not body.strip() and not slide['has_visual']:
                    complete = True
                complete = complete and not slide['issues']
                if complete:
                    checked(selected, settings.fidelity_vision_model_id)
                else:
                    audit['issues'].append('POWERPOINT_SOURCE_REQUIRES_REVIEW')
                    flag(selected, 'Complete slide content or layout could not be verified; source review is required.')
                    if slide['has_visual']:
                        visual = DocumentElement(element_id=deterministic_uuid(f'{digest}:pptx:{number}:visual'),
                            element_index=0, element_type=ElementType.FIGURE, original_text='',
                            candidate_description=candidate, asset_path=str(image_path),
                            source_reference=f'slide:{number}:visual', heading_path=[f'Slide {number}', slide['title']],
                            metadata={'source_format': 'pptx', 'slide_number': number, 'content_layer': 'visual'})
                        flag([visual], 'No complete, verified slide transcription enters retrieval.')
                        selected.append(visual)
                        audit['issues'].append('POWERPOINT_VISUAL_REQUIRES_REVIEW')
                audit['complete'] = complete
                warnings.extend(audit['issues'])
                if metadata.include_speaker_notes and slide['notes']:
                    selected.append(DocumentElement(element_id=deterministic_uuid(f'{digest}:pptx:{number}:notes'),
                        element_index=0, element_type=ElementType.TEXT, original_text=slide['notes'], source_text=slide['notes'],
                        source_reference=f'slide:{number}:speaker_notes', heading_path=[f'Slide {number}', slide['title'], 'Speaker notes'],
                        metadata={'source_format': 'pptx', 'slide_number': number, 'content_layer': 'speaker_notes'},
                        quality=ContentQuality(checks=['native_speaker_notes', 'teacher_opted_in'])))
                elements.extend(selected)
                audits.append(audit)
                manifest.append({'slide_number': number, 'title': slide['title'], 'hidden': slide['hidden'],
                                 'image_sha256': sha256_file(image_path)})
        finally:
            rendered_doc.close()
        for index, element in enumerate(elements):
            element.element_index = index
        write_json(artifacts / 'render-audit.json', {'source_hash': digest, 'slides': audits})
        version_id = deterministic_uuid(f'{document_id}:version:{digest}:{settings.pipeline_signature}')
        write_json(artifacts / 'slides.json', {'source_hash': digest, 'document_id': document_id,
                   'document_version_id': version_id,
                   'pipeline_version': settings.pipeline_signature, 'slides': manifest})
        chunks = []
        chunker = StructureAwareChunker(max_tokens=settings.chunk_max_tokens, overlap_tokens=settings.chunk_overlap_tokens)
        for number, slide in included.items():
            slide_elements = [e for e in elements if e.metadata['slide_number'] == number]
            list_base = min((e.metadata.get('native_level', 0) for e in slide_elements
                             if e.element_type is ElementType.LIST_ITEM), default=0)
            normalized = [e.model_copy(update={'original_text': 'Speaker notes:\n' + e.original_text})
                          if e.metadata['content_layer'] == 'speaker_notes' else
                          e.model_copy(update={'original_text': '  ' * min(20, max(0, e.metadata.get('native_level', 0) - list_base)) + '- ' + e.original_text})
                          if e.element_type is ElementType.LIST_ITEM else
                          e.model_copy(update={'element_type': ElementType.TEXT})
                          if e.element_type in {ElementType.TITLE, ElementType.HEADING} else e for e in slide_elements]
            source_chunks = chunker.chunk(normalized, metadata=metadata, document_id=document_id,
                document_version_id=version_id, source_hash=digest)
            for local_index, chunk in enumerate(source_chunks):
                chunk.chunk_index = len(chunks)
                chunk.chunk_id = deterministic_uuid(f'{version_id}:slide:{number}:chunk:{local_index}:{chunk.chunk_id}')
                chunk.source_format, chunk.source_blocks, chunk.slide_numbers = 'pptx', [number], [number]
                chunk.slide_references = [{'slide_number': number, 'title': slide['title'], 'hidden': slide['hidden'],
                    'includes_speaker_notes': any(e.metadata['content_layer'] == 'speaker_notes'
                                                 for e in slide_elements if e.element_id in chunk.element_ids),
                    'reading_direction': slide['reading_direction'],
                    'native_regions': [{'source_reference': r['reference'], 'box': r['box']}
                                       for r in slide['regions']],
                    'element_refs': [e.source_reference for e in slide_elements if e.element_id in chunk.element_ids]}]
                chunk.page_numbers = []
                # Keep a visible distinction between slide content and opted-in presenter notes.
                if chunk.slide_references[0]['includes_speaker_notes'] and 'Speaker notes:' not in chunk.text:
                    chunk.text = 'Speaker notes:\n' + chunk.text
                chunk.text = f'[Slide {number}]\n' + chunk.text.replace('original PDF', 'original PowerPoint slide')
                chunk.embedding_text = chunker._context_prefix(metadata, chunk.heading_path) + '\n\n' + chunk.text
                chunk.token_count = chunker.codec.count(chunk.embedding_text)
                chunks.append(chunk)
        if not chunks:
            raise IngestionError('NO_RETRIEVABLE_CONTENT')
        embedder = self.embedder or BedrockTitanEmbeddingProvider(session=aws_session(settings),
            model_id=settings.embedding_model_id, dimension=settings.embedding_dimension)
        notify_workload(progress, 'embed', len(chunks))
        vectors = []
        for start in range(0, len(chunks), 8):
            progress('embed', .7 + .2 * start / len(chunks), 'Embedding PowerPoint slide content.')
            vectors.extend(embedder.embed_texts([c.embedding_text for c in chunks[start:start + 8]]))
        document = ProcessedDocument(document_id=document_id, document_version_id=version_id, source_hash=digest,
            metadata=metadata, parser_name='docling-powerpoint-' + version('docling'), parser_version=POWERPOINT_PARSER_VERSION,
            pipeline_version=settings.pipeline_signature, embedding_model_id=settings.embedding_model_id,
            embedding_dimension=settings.embedding_dimension, page_count=0, slide_count=len(slides),
            source_block_count=len(slides), elements=elements, chunks=chunks, warnings=list(dict.fromkeys(warnings)),
            ingestion_readiness=SourceReadiness(status='COMPLETE' if all(a['complete'] for a in audits) else 'INCOMPLETE',
                blocking_slides=[a['slide_number'] for a in audits if not a['complete']],
                issues=list(dict.fromkeys(issue for a in audits for issue in a['issues']))),
            quality=aggregate_quality(elements), quality_summary=quality_summary(elements))
        write_json(checkpoint, {'document': document.model_dump(mode='json'), 'vectors': vectors, 'versions': versions})
        return document, vectors
