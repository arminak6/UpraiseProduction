"""Native slide regions and geometric reading order, independent of XML z-order."""

import html
import re
from collections import Counter
from io import BytesIO
from unicodedata import bidirectional, normalize

from PIL import Image


def text_tokens(text):
    return Counter(re.findall(r'\w+', normalize('NFKC', text).casefold()))


def number_tokens(text):
    return Counter(re.findall(r'(?<!\w)[+\-\u2212]?\d+(?:[.,]\d+)*(?:[%‰])?', normalize('NFKC', text)))


def reading_order(regions, *, rtl=False):
    """Recursive XY cut: full-width headings, then columns, then their rows.

    Text paragraphs and explicit groups remain atomic at each outer cut. Where
    objects overlap and no unambiguous cut exists, preserve geometric order and
    let the full-slide comparison decide whether that order is faithful.
    """
    if len(regions) < 2:
        return list(regions)
    for axis in (0, 1):
        ordered = sorted(regions, key=lambda r: (r['box'][axis], r['index']))
        end, gaps = ordered[0]['box'][axis + 2], []
        for index, region in enumerate(ordered[1:], 1):
            start = region['box'][axis]
            if start - end > .006:
                gaps.append((start - end, index))
            end = max(end, region['box'][axis + 2])
        if gaps:
            _, cut = max(gaps)
            first = reading_order(ordered[:cut], rtl=rtl)
            second = reading_order(ordered[cut:], rtl=rtl)
            return second + first if rtl and axis == 0 else first + second
    return sorted(regions, key=lambda r: (round(r['box'][1], 3), -r['box'][0] if rtl else r['box'][0], r['index']))


def slide_inventory(source, artifacts=None):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(source)
    width, height = presentation.slide_width, presentation.slide_height
    slides = []
    for number, slide in enumerate(presentation.slides, 1):
        regions, pictures, unsupported, evidence_warnings = [], [], [], []

        def shape_text(shapes):
            return '\n'.join(shape_text(s.shapes) if s.shape_type == MSO_SHAPE_TYPE.GROUP else
                             s.text if s.has_text_frame else '' for s in shapes)

        directions = Counter(bidirectional(c) for c in shape_text(slide.shapes))
        rtl = directions['R'] + directions['AL'] > directions['L']

        def walk(shapes, transform=(1., 1., 0., 0.), parent='', covers=(), transformed=False):
            sx, sy, tx, ty = transform
            shapes = list(shapes)
            def bounds(shape):
                return [(tx + sx * shape.left) / width, (ty + sy * shape.top) / height,
                        (tx + sx * (shape.left + shape.width)) / width,
                        (ty + sy * (shape.top + shape.height)) / height]
            nodes = []
            for shape_index, shape in enumerate(shapes):
                props = shape._element.xpath('.//p:cNvPr')
                if props and props[0].get('hidden') in {'1', 'true'}:
                    continue
                ref = f'{parent}/{shape.shape_id}'
                box = bounds(shape)
                higher_covers = ([*covers, *[bounds(s) for s in shapes[shape_index + 1:]]]
                                 if shape.shape_type in {MSO_SHAPE_TYPE.GROUP, MSO_SHAPE_TYPE.PICTURE} else covers)
                node = {'box': box, 'index': len(nodes), 'reference': f'slide:{number}:shape:{ref}'}
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    xfrm = shape._element.grpSpPr.xfrm
                    if xfrm is not None and xfrm.chExt is not None and xfrm.chExt.cx and xfrm.chExt.cy:
                        gx, gy = shape.width / xfrm.chExt.cx, shape.height / xfrm.chExt.cy
                        child_transform = (sx * gx, sy * gy,
                            tx + sx * (shape.left - gx * xfrm.chOff.x),
                            ty + sy * (shape.top - gy * xfrm.chOff.y))
                    else:
                        child_transform = transform
                    flipped = xfrm is not None and any(xfrm.get(k) in {'1', 'true'} for k in ('flipH', 'flipV'))
                    node['children'] = walk(shape.shapes, child_transform, ref, higher_covers,
                                            transformed or bool(shape.rotation) or flipped)
                    # Group rotation/flip and connectors need a visual reading.
                    node['visual'] = True
                    nodes.append(node)
                    continue
                paragraphs = []
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        if paragraph.text.strip():
                            runs = paragraph.runs
                            size = max((r.font.size.pt for r in runs if r.font.size), default=0)
                            paragraphs.append({'text': paragraph.text, 'level': paragraph.level,
                                'font_size': size, 'bold': any(r.font.bold for r in runs),
                                'bullet': bool(paragraph._p.xpath('./a:pPr/a:buChar | ./a:pPr/a:buAutoNum'))})
                if shape.has_table:
                    rows, text = [], []
                    for row in shape.table.rows:
                        cells = []
                        for cell in row.cells:
                            if cell.is_spanned:
                                continue
                            value = cell.text.strip()
                            text.append(value)
                            cells.append(f'<td rowspan="{cell.span_height}" colspan="{cell.span_width}">'
                                         + html.escape(value).replace('\n', '<br>') + '</td>')
                        rows.append('<tr>' + ''.join(cells) + '</tr>')
                    node.update(kind='table', text='\n'.join(text), html='<table>' + ''.join(rows) + '</table>')
                elif paragraphs:
                    node.update(kind='text', text='\n'.join(p['text'] for p in paragraphs), paragraphs=paragraphs)
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    node['visual'] = True
                    if artifacts is not None:
                        # Use only the visible crop, never hidden cropped-out text.
                        # Original pixels supplement the rendered slide at full resolution.
                        path = artifacts / f'slide-{number}-picture-{len(pictures) + 1}.png'
                        geometry = shape._element.spPr.find('{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom')
                        custom = shape._element.spPr.find('{http://schemas.openxmlformats.org/drawingml/2006/main}custGeom')
                        xfrm = shape._element.spPr.xfrm
                        flipped = xfrm is not None and any(xfrm.get(k) in {'1', 'true'} for k in ('flipH', 'flipV'))
                        effects = shape._element.xpath('./p:blipFill/a:blip/*[not(self::a:extLst)] | ./p:spPr/a:effectLst/*')
                        masked = (custom is not None or (geometry is not None and geometry.get('prst') != 'rect')
                                  or transformed or bool(shape.rotation) or flipped or bool(effects))
                        covered = any(min(box[2], b[2]) > max(box[0], b[0]) + .001
                                      and min(box[3], b[3]) > max(box[1], b[1]) + .001 for b in higher_covers)
                        if masked or covered:
                            # Raw images would reveal masked/redacted/covered pixels.
                            # Compare only their visible rendered appearance instead.
                            evidence_warnings.append('POWERPOINT_PICTURE_RENDER_ONLY')
                            if any(v < 0 or v > 1 for v in box):
                                unsupported.append('POWERPOINT_OFF_SLIDE_CONTENT')
                            nodes.append(node)
                            continue
                        try:
                            with Image.open(BytesIO(shape.image.blob)) as image:
                                w, h = image.size
                                if w > 8000 or h > 8000 or w * h > 32_000_000:
                                    raise ValueError('picture evidence too large')
                                image.load()
                                crop = (round(w * max(0, shape.crop_left)), round(h * max(0, shape.crop_top)),
                                        round(w * (1 - max(0, shape.crop_right))),
                                        round(h * (1 - max(0, shape.crop_bottom))))
                                if crop[2] <= crop[0] or crop[3] <= crop[1]:
                                    raise ValueError('empty visible picture')
                                visible = image.crop(crop)
                                visible.save(path, format='PNG')
                                visible.close()
                            pictures.append({'path': path, 'reference': node['reference'], 'box': box})
                        except (OSError, ValueError, Image.DecompressionBombError):
                            evidence_warnings.append('POWERPOINT_PICTURE_EVIDENCE_UNAVAILABLE')
                elif shape.shape_type in {MSO_SHAPE_TYPE.MEDIA, MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT,
                                           MSO_SHAPE_TYPE.LINKED_OLE_OBJECT}:
                    unsupported.append('POWERPOINT_EMBEDDED_CONTENT_REQUIRES_REVIEW')
                    node['visual'] = True
                elif shape.has_chart or shape.shape_type in {MSO_SHAPE_TYPE.DIAGRAM, MSO_SHAPE_TYPE.LINE}:
                    node['visual'] = True
                elif shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                    node['visual'] = True
                if (node.get('text') or node.get('visual')) and any(v < 0 or v > 1 for v in box):
                    unsupported.append('POWERPOINT_OFF_SLIDE_CONTENT')
                nodes.append(node)
            def contains_text(node):
                return bool(node.get('text')) or any(contains_text(c) for c in node.get('children', []))
            # A full-slide background or decorative logo must not collapse columns.
            content = [n for n in nodes if contains_text(n)]
            return reading_order(content, rtl=rtl) + [n for n in nodes if not contains_text(n)]

        def flatten(nodes):
            for node in nodes:
                if 'children' in node:
                    yield from flatten(node['children'])
                elif node.get('text'):
                    yield node

        nodes = walk(slide.shapes)
        regions = list(flatten(nodes))
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape else ''
        if not title:
            candidates = [r for r in regions if r.get('kind') == 'text' and r['box'][1] < .3
                          and r['box'][3] - r['box'][1] < .25 and len(r['text'].split()) <= 25]
            if candidates:
                title = min(candidates, key=lambda r: (r['box'][1], -max(p['font_size'] for p in r['paragraphs'])))['text']
        notes = slide.notes_slide.notes_text_frame if slide.has_notes_slide else None

        def visual(nodes):
            return any(n.get('visual') or visual(n.get('children', [])) for n in nodes)

        slides.append({'slide_number': number, 'title': title,
            'hidden': slide._element.get('show', '1') in {'0', 'false'},
            'notes': notes.text.strip() if notes is not None else '', 'has_visual': visual(nodes),
            'regions': regions, 'pictures': pictures, 'issues': list(dict.fromkeys(unsupported)),
            'evidence_warnings': list(dict.fromkeys(evidence_warnings)), 'reading_direction': 'rtl' if rtl else 'ltr',
            'native_text': '\n'.join(r['text'] for r in regions)})
    return slides
