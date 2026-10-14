"""Native Docling Word extraction with retained rendering and source-block provenance."""
import json
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from difflib import SequenceMatcher
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid, sha256_file
from upraise_ai.schemas.documents import (
    ContentQuality,
    DocumentElement,
    ElementType,
    ProcessedDocument,
    QualityStatus,
)
from upraise_ai.services.aws import aws_session
from upraise_ai.services.chunking import StructureAwareChunker
from upraise_ai.services.embeddings import BedrockTitanEmbeddingProvider
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.pdf.parser import LABEL_MAP, DoclingPdfParser
from upraise_ai.services.pdf.vision import BedrockImageDescriber
from upraise_ai.services.quality import aggregate_quality, quality_summary
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.workers.budgets import notify_workload

WORD_PARSER_VERSION = "docling-word-blocks-v1"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def validate_word_input(source, settings, source_format):
    if source.stat().st_size > settings.max_word_size_mb * 1024**2:
        raise IngestionError("WORD_TOO_LARGE", status=413)
    if source_format == "doc":
        with source.open('rb') as stream:
            if stream.read(8) != bytes.fromhex('d0cf11e0a1b11ae1'):
                raise IngestionError("WORD_INVALID_DOCUMENT", status=422)
        return
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if (len(entries) > 10000 or len({e.filename for e in entries}) != len(entries)
                    or sum(e.file_size for e in entries) > settings.max_word_uncompressed_mb * 1024**2):
                raise IngestionError("WORD_ARCHIVE_LIMIT_EXCEEDED", status=422)
            if any(e.flag_bits & 1 for e in entries):
                raise IngestionError("WORD_ENCRYPTED_UNSUPPORTED", status=422)
            if any(e.filename.lower().endswith('vbaproject.bin') for e in entries):
                raise IngestionError("WORD_MACROS_UNSUPPORTED", status=422)
            content_types = archive.read('[Content_Types].xml')
            if b'wordprocessingml.document.main+xml' not in content_types:
                raise IngestionError("WORD_INVALID_DOCUMENT", status=422)
            raw = archive.read('word/document.xml')
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                raise IngestionError("WORD_INVALID_DOCUMENT", status=422)
            root = ElementTree.fromstring(raw)
            if root.tag not in {f'{{{WORD_NS}}}document', '{http://purl.oclc.org/ooxml/wordprocessingml/main}document'}:
                raise IngestionError("WORD_INVALID_DOCUMENT", status=422)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError):
        raise IngestionError("WORD_INVALID_DOCUMENT", status=422) from None


def convert_word(source, destination, target, settings):
    """Bounded headless conversion in a per-job profile, never modify the upload."""
    executable = shutil.which('soffice')
    if not executable:
        raise IngestionError("WORD_CONVERTER_UNAVAILABLE", status=503, retryable=True)
    destination.mkdir(parents=True, exist_ok=True)
    profile = destination / 'office-profile'
    try:
        subprocess.run([executable, '-env:UserInstallation=' + profile.resolve().as_uri(), '--headless',
                        '--convert-to', target, '--outdir', str(destination), str(source)],
                       capture_output=True, timeout=settings.word_conversion_timeout_seconds, check=True)
    except subprocess.TimeoutExpired:
        raise IngestionError("WORD_CONVERSION_TIMEOUT", status=422) from None
    except subprocess.CalledProcessError:
        raise IngestionError("WORD_CONVERSION_FAILED", status=422) from None
    result = destination / (source.stem + '.' + target)
    if not result.is_file() or result.stat().st_size == 0:
        raise IngestionError("WORD_CONVERSION_FAILED", status=422)
    return result


def recover_native_paragraphs(source, elements, source_hash):
    """Preserve Word text clipped out of its rendered view, explicitly unverified."""
    with zipfile.ZipFile(source) as archive:
        root = ElementTree.fromstring(archive.read('word/document.xml'))
    # AlternateContent stores two representations of the same object. Read its
    # preferred branch once, avoiding duplicate text from its legacy fallback.
    mc = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
    for alternate in root.iter(f'{{{mc}}}AlternateContent'):
        children = list(alternate)
        preferred = next((c for c in children if c.tag == f'{{{mc}}}Choice'), children[0] if children else None)
        for child in children:
            if child is not preferred:
                alternate.remove(child)
    namespace = root.tag.rsplit('}', 1)[0] + '}'
    paragraphs = root.findall(f'.//{namespace}body/{namespace}p')
    def normalize(text):
        return ''.join(re.findall(r'\w+', text.casefold()))
    reference = normalize(' '.join(e.original_text for e in elements))
    additions = []
    seen = set()
    for index, paragraph in enumerate(paragraphs, 1):
        text = ''.join(part.text or '' for part in paragraph.iter(namespace + 't')).strip()
        compact = normalize(text)
        if len(text.split()) < 4 or not compact or compact in reference or compact in seen:
            continue
        seen.add(compact)
        locator = f'word/document.xml/body/p[{index}]'
        extra = DocumentElement(element_id=deterministic_uuid(f'{source_hash}:word-recovery:{index}'),
            element_index=0, element_type=ElementType.TEXT, original_text=text, source_text=text,
            source_reference=locator, metadata={'word_extraction_path': 'native_word_recovery'},
            quality=ContentQuality(status=QualityStatus.NEEDS_REVIEW, checks=['native_word_text_preserved'],
                issues=['Native paragraph is not fully represented in the rendering; mathematical layout is unverified.']))
        # Place recovery beside its closest rendered paragraph instead of moving
        # omitted definitions to the end of the document.
        best = max(range(len(elements)), key=lambda i: SequenceMatcher(None, compact,
                   normalize(elements[i].original_text)).ratio(), default=-1)
        additions.append((best, extra))
    grouped = {}
    for index, element in additions:
        grouped.setdefault(index, []).append(element)
    result = []
    for index, element in enumerate(elements):
        result.append(element)
        result.extend(grouped.get(index, []))
    if not elements:
        result.extend(grouped.get(-1, []))
    for index, element in enumerate(result):
        element.element_index = index
    return result, len(additions)


class WordProcessor:
    def __init__(self, settings, *, embedder=None, converter=None, describer=None, verifier=None, renderer=None):
        self.settings, self.embedder, self.converter = settings, embedder, converter
        self.describer, self.verifier, self.renderer = describer, verifier, renderer

    def prepare(self, source, metadata, document_id, job_dir, token, progress):
        settings = self.settings
        validate_word_input(source, settings, metadata.source_format)
        digest, refs = sha256_file(source), VersionCatalog(settings).references()
        checkpoint = job_dir / 'prepared.json'
        if checkpoint.is_file():
            try:
                saved = json.loads(checkpoint.read_text())
                processed = ProcessedDocument.model_validate(saved['document'])
                if (processed.document_id == document_id and processed.metadata == metadata
                        and processed.source_hash == digest and processed.pipeline_version == settings.pipeline_signature
                        and processed.parser_version == WORD_PARSER_VERSION and saved['versions'] == refs):
                    return processed, saved['vectors']
            except (ValueError, KeyError, OSError):
                pass
        notify_workload(progress, 'parse', 1)
        progress('parse', 0.1, 'Reading Word structure with Docling.')
        artifacts = job_dir / 'word-artifacts'
        artifacts.mkdir(parents=True, exist_ok=True)
        native_source = (convert_word(source, artifacts / 'converted', 'docx', settings)
                         if metadata.source_format == 'doc' else source)
        validate_word_input(native_source, settings, 'docx')
        if self.converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter

            self.converter = DocumentConverter(allowed_formats=[InputFormat.DOCX])
        converted = self.converter.convert(native_source)
        if str(getattr(converted.status, 'value', converted.status)) != 'success':
            raise IngestionError('WORD_CONVERSION_INCOMPLETE')
        document = converted.document
        write_json(artifacts / 'docling.json', document.model_dump(mode='json'))
        elements, warnings, headings = [], [], []
        items = list(document.iterate_items())
        for item, level in items:
            label = DoclingPdfParser._label_value(getattr(item, 'label', None))
            kind = LABEL_MAP.get(label, ElementType.OTHER)
            text = DoclingPdfParser._text_for_item(item, document, kind)
            if not text.strip() and kind is not ElementType.FIGURE:
                continue
            ref = DoclingPdfParser._source_reference(item)
            if kind in {ElementType.HEADING, ElementType.TITLE}:
                depth = getattr(item, 'level', None) or level or 1
                while headings and headings[-1][0] >= depth:
                    headings.pop()
                headings.append((depth, text.strip()))
            index = len(elements)
            element = DocumentElement(element_id=deterministic_uuid(f'{digest}:word:{ref}:{index}'),
                element_index=index, element_type=kind, original_text=text, source_text=text,
                parser_label=label, source_reference=ref, heading_path=[h[1] for h in headings],
                caption=DoclingPdfParser._caption_for_item(item, document),
                metadata={'source_format': metadata.source_format, 'docling_ref': ref},
                quality=ContentQuality(checks=['native_word_structure', 'parser_output_preserved']))
            if kind is ElementType.TABLE:
                element.structured_content = DoclingPdfParser._table_html(item, document)
                element.source_structured_content = element.structured_content
            if kind is ElementType.FIGURE:
                image = item.get_image(document)
                if image is not None:
                    path = artifacts / f'figure-{index}.png'
                    image.save(path)
                    element.asset_path = str(path)
                else:
                    element.quality = ContentQuality(status=QualityStatus.NEEDS_REVIEW,
                        issues=['Word picture could not be decoded.'])
            elements.append(element)
        if not elements:
            raise IngestionError('NO_RETRIEVABLE_CONTENT')
        progress('parse', 0.36, 'Rendering a PDF copy for extraction checks.')
        renderer = self.renderer or convert_word
        rendered = renderer(native_source, artifacts / 'rendered', 'pdf', settings)
        from pypdf import PdfReader

        pdf = PdfReader(rendered)
        if len(pdf.pages) > settings.max_pdf_pages:
            raise IngestionError('WORD_TOO_MANY_RENDERED_PAGES', status=422)
        rendered_text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        native_text = '\n'.join(e.original_text for e in elements)
        native_counts = Counter(re.findall(r'\w+', native_text.casefold()))
        rendered_counts = Counter(re.findall(r'\w+', rendered_text.casefold()))
        missing = rendered_counts - native_counts
        missing_ratio = sum(missing.values()) / max(1, sum(rendered_counts.values()))
        text_elements = [e for e in elements if e.original_text.strip()]
        fragmented = len(text_elements) > 100 and sum(len(e.original_text.split()) < 4 for e in text_elements) > len(text_elements) / 2
        audit = {'rendered_page_count': len(pdf.pages), 'native_text_tokens': sum(native_counts.values()),
                 'rendered_text_tokens': sum(rendered_counts.values()), 'rendered_tokens_absent_from_native': dict(missing),
                 'missing_token_ratio': missing_ratio, 'fragmented_native_text': fragmented,
                 'rendered_pdf_sha256': sha256_file(rendered),
                 'method': 'Token-count comparison detects possible omissions; does not prove visual or semantic fidelity.'}
        write_json(artifacts / 'render-audit.json', audit)
        if missing_ratio > 0.05:
            warnings.append('WORD_RENDER_TEXT_MISMATCH')
            for element in elements:
                element.quality.status = QualityStatus.NEEDS_REVIEW
                element.quality.issues.append('Rendered Word text differs from native extraction; inspect render-audit.json.')
        fallback = settings.word_visual_fallback and (fragmented or missing_ratio > 0.05)
        if fragmented:
            warnings.append('WORD_FRAGMENTED_NATIVE_TEXT')
        if fallback:
            write_json(artifacts / 'native-elements.json', [e.model_dump(mode='json') for e in elements])
            progress('parse', 0.38, 'Recovering Word layout and mathematics from the rendered copy.')
            notify_workload(progress, 'parse', len(pdf.pages))
            parsed = DoclingPdfParser(settings, metadata.language, page_count=len(pdf.pages)).parse(
                rendered, source_hash=digest, artifact_dir=artifacts / 'visual-fallback', progress=progress)
            from upraise_ai.services.pdf.enrichment import PdfEnricher

            session = aws_session(settings)
            def provider(model_id):
                return BedrockImageDescriber(session=session, model_id=model_id, cache_dir=artifacts / 'cache')
            notify_workload(progress, 'enrich', max(1, len(parsed.elements)))
            enriched_warnings = PdfEnricher(settings, provider(settings.vision_model_id),
                provider(settings.math_vision_model_id), verifier=provider(settings.fidelity_vision_model_id)).enrich(parsed, progress)
            warnings.extend(enriched_warnings)
            warnings.append('WORD_RENDERED_LAYOUT_FALLBACK')
            elements = parsed.elements
            for element in elements:
                element.metadata['word_extraction_path'] = 'rendered_pdf_fallback'
                element.metadata['source_format'] = metadata.source_format
                # Page locations describe the retained rendering, not native Word pagination.
                element.metadata['rendered_pages'] = element.page_numbers.copy()
            elements, recovered = recover_native_paragraphs(native_source, elements, digest)
            audit['recovered_native_paragraphs'] = recovered
            if recovered:
                warnings.append('WORD_NATIVE_TEXT_RECOVERED_REQUIRES_REVIEW')
            audit['extraction_path'] = 'rendered_pdf_fallback'
        else:
            audit['extraction_path'] = 'native_docling_word'
        write_json(artifacts / 'render-audit.json', audit)
        figures = [e for e in elements if e.element_type is ElementType.FIGURE]
        notify_workload(progress, 'enrich', max(1, len(figures)))
        if figures and settings.describe_images and not fallback:
            session = aws_session(settings) if self.describer is None or self.verifier is None else None
            describer = self.describer or BedrockImageDescriber(session=session,
                model_id=settings.vision_model_id, cache_dir=artifacts / 'cache')
            verifier = self.verifier or BedrockImageDescriber(session=session,
                model_id=settings.fidelity_vision_model_id, cache_dir=artifacts / 'cache')
            for pos, element in enumerate(figures):
                progress('enrich', 0.42 + 0.2 * pos / max(1, len(figures)), 'Checking Word figures.')
                if not element.asset_path:
                    continue
                outcome = describer.describe(Path(element.asset_path), page_number=None,
                    caption=element.caption, heading_path=element.heading_path)
                element.candidate_description = outcome.description
                verification = (verifier.verify_source([Path(element.asset_path)], candidate=outcome.description,
                    content_type='figure', page_numbers=[], source_references=[element.source_reference])
                    if outcome.description else None)
                if verification and verification.passed:
                    element.derived_description = outcome.description
                    element.quality.checks.append('model_source_comparison')
                    element.quality.evidence_complete = True
                else:
                    element.quality.status = QualityStatus.NEEDS_REVIEW
                    element.quality.issues.append('Word figure description lacks a passing source comparison.')
                    warnings.append('WORD_FIGURE_REQUIRES_REVIEW')
        version_id = deterministic_uuid(f'{document_id}:version:{digest}:{settings.pipeline_signature}')
        progress('chunk', 0.64, 'Creating Word source blocks.')
        # Retain headings as retrievable content as well as section metadata.
        chunk_elements = [e.model_copy(update={'element_type': ElementType.TEXT})
                          if e.element_type in {ElementType.HEADING, ElementType.TITLE} else e for e in elements]
        chunks = StructureAwareChunker(max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens).chunk(chunk_elements, metadata=metadata,
                document_id=document_id, document_version_id=version_id, source_hash=digest)
        by_id = {e.element_id: e for e in elements}
        for index, chunk in enumerate(chunks, 1):
            chunk.source_format = metadata.source_format
            chunk.source_blocks = [index]
            chunk.word_references = [{'block': index, 'element_refs': [by_id[e].source_reference for e in chunk.element_ids],
                                      'heading_path': chunk.heading_path,
                                      'extraction_path': audit['extraction_path'],
                                      'rendered_pages': chunk.page_numbers.copy()}]
            chunk.word_references[0]['element_extraction_paths'] = {
                by_id[e].source_reference: by_id[e].metadata.get('word_extraction_path', 'native_docling_word')
                for e in chunk.element_ids}
            chunk.page_numbers = []
            chunk.text = f'[Word block {index}]\n' + chunk.text.replace('consult the original PDF', 'consult the original Word document')
            prefix = StructureAwareChunker._context_prefix(metadata, chunk.heading_path)
            chunk.embedding_text = prefix + '\n\n' + chunk.text
            chunk.token_count = StructureAwareChunker().codec.count(chunk.embedding_text)
        if not chunks:
            raise IngestionError('NO_RETRIEVABLE_CONTENT')
        embedder = self.embedder or BedrockTitanEmbeddingProvider(session=aws_session(settings),
            model_id=settings.embedding_model_id, dimension=settings.embedding_dimension)
        notify_workload(progress, 'embed', len(chunks))
        vectors = []
        for start in range(0, len(chunks), 8):
            progress('embed', 0.7 + 0.2 * start / len(chunks), 'Embedding Word blocks.')
            vectors.extend(embedder.embed_texts([c.embedding_text for c in chunks[start:start + 8]]))
        result = ProcessedDocument(document_id=document_id, document_version_id=version_id, source_hash=digest,
            metadata=metadata, parser_name='docling-word-' + version('docling'), parser_version=WORD_PARSER_VERSION,
            pipeline_version=settings.pipeline_signature, embedding_model_id=settings.embedding_model_id,
            embedding_dimension=settings.embedding_dimension, page_count=0, source_block_count=len(chunks),
            elements=elements, chunks=chunks, warnings=list(dict.fromkeys(warnings)), quality=aggregate_quality(elements),
            quality_summary=quality_summary(elements))
        write_json(checkpoint, {'document': result.model_dump(mode='json'), 'vectors': vectors, 'versions': refs})
        return result, vectors
