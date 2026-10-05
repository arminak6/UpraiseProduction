from types import SimpleNamespace

import pytest
from PIL import Image

from upraise_ai.core.config import Settings
from upraise_ai.core.utils import resolve_project_path
from upraise_ai.services.pdf.parser import DoclingPdfParser


def bbox(left, top, right, bottom, origin="TOPLEFT"):
    return SimpleNamespace(l=left, t=top, r=right, b=bottom, coord_origin=SimpleNamespace(value=origin))


def provenance(page, bounds, span=(0, 5)):
    return SimpleNamespace(page_no=page, bbox=bounds, charspan=span)


def page(image, width=100, height=100):
    return SimpleNamespace(
        size=SimpleNamespace(width=width, height=height),
        image=SimpleNamespace(pil_image=image),
    )


def parse(tmp_path, items, pages):
    settings = Settings(data_dir=tmp_path)
    document = SimpleNamespace(pages=pages, iterate_items=lambda **_: [(item, 1) for item in items])
    parser = DoclingPdfParser(settings, "en", page_count=len(pages))
    parser._converter = SimpleNamespace(convert=lambda _: SimpleNamespace(status="success", document=document))
    result = parser.parse(tmp_path / "source.pdf", source_hash="hash", artifact_dir=tmp_path / "attempt")
    return result, settings


def item(label, text, spans, **extra):
    return SimpleNamespace(label=label, text=text, prov=spans, self_ref="#/texts/0", **extra)


def crop(region, settings):
    assert region.asset_path
    with Image.open(resolve_project_path(region.asset_path, settings.project_root)) as saved:
        return saved.copy()


def test_native_docling_types_retain_both_page_crops_and_text_spans(tmp_path):
    core = pytest.importorskip("docling_core.types.doc")
    document = core.DoclingDocument(name="General two-page source")
    document.add_page(
        page_no=1, size=core.Size(width=100, height=100),
        image=core.ImageRef.from_pil(Image.new("RGB", (200, 200), "red"), dpi=144),
    )
    document.add_page(
        page_no=2, size=core.Size(width=100, height=100),
        image=core.ImageRef.from_pil(Image.new("RGB", (300, 200), "blue"), dpi=144),
    )
    source = "x = 2\ny = 3"
    native_item = document.add_text(
        label=core.DocItemLabel.TEXT, text=source,
        prov=core.ProvenanceItem(
            page_no=1, charspan=(0, 5),
            bbox=core.BoundingBox(l=0, t=0, r=50, b=50, coord_origin=core.CoordOrigin.TOPLEFT),
        ),
    )
    native_item.prov.append(core.ProvenanceItem(
        page_no=2, charspan=(6, len(source)),
        bbox=core.BoundingBox(l=50, t=100, r=100, b=50, coord_origin=core.CoordOrigin.BOTTOMLEFT),
    ))
    settings = Settings(data_dir=tmp_path)
    parser = DoclingPdfParser(settings, "en", page_count=2)
    parser._converter = SimpleNamespace(convert=lambda _: SimpleNamespace(status="success", document=document))
    parsed = parser.parse(tmp_path / "source.pdf", source_hash="hash", artifact_dir=tmp_path / "attempt")
    assert not parsed.warnings and parsed.page_count == 2
    extracted = parsed.elements[0]
    assert extracted.original_text == extracted.source_text == source
    assert extracted.page_numbers == [1, 2]
    assert [region.charspan for region in extracted.source_regions] == [[0, 5], [6, len(source)]]
    assert crop(extracted.source_regions[0], settings).size == (100, 100)
    assert crop(extracted.source_regions[1], settings).size == (150, 100)
    assert crop(extracted.source_regions[0], settings).getpixel((0, 0)) == (255, 0, 0)
    assert crop(extracted.source_regions[1], settings).getpixel((0, 0)) == (0, 0, 255)


def test_cross_page_formula_crops_every_provenance_and_preserves_source(tmp_path):
    first = provenance(1, bbox(10, 10, 30, 20), (0, 5))
    second = provenance(2, bbox(40, 90, 60, 70, "BOTTOMLEFT"), (5, 12))
    image_calls = []
    formula = item(
        "formula", "a + b = c^2", [first, second],
        get_image=lambda _: image_calls.append("ambiguous fallback") or Image.new("RGB", (1, 1)),
    )
    second_image = Image.new("RGB", (100, 200), "white")
    second_image.paste("blue", (40, 20, 60, 60))
    result, settings = parse(tmp_path, [formula], {
        1: page(Image.new("RGB", (200, 300), "red")),
        2: page(second_image),
    })
    element = result.elements[0]
    assert not result.warnings and not image_calls
    assert element.page_numbers == [1, 2]
    assert element.source_text == element.original_text == "a + b = c^2"
    assert [region.page_number for region in element.source_regions] == [1, 2]
    assert [region.charspan for region in element.source_regions] == [[0, 5], [5, 12]]
    assert crop(element.source_regions[0], settings).size == (40, 30)
    assert crop(element.source_regions[1], settings).size == (20, 40)
    assert crop(element.source_regions[0], settings).getpixel((0, 0)) == (255, 0, 0)
    assert crop(element.source_regions[1], settings).getpixel((0, 0)) == (0, 0, 255)
    assert element.asset_path == element.source_regions[0].asset_path
    element.original_text = "untrusted generated replacement"
    assert element.source_text == "a + b = c^2"


def test_same_page_inline_math_keeps_distinct_regions_and_character_spans(tmp_path):
    image = Image.new("RGB", (200, 200), "white")
    image.paste("red", (0, 0, 100, 100))
    image.paste("blue", (100, 100, 200, 200))
    inline = item("text", "x = 1 and y = 2", [
        provenance(1, bbox(0, 0, 50, 50), (0, 5)),
        provenance(1, bbox(50, 50, 100, 100), (10, 15)),
    ])
    result, settings = parse(tmp_path, [inline], {1: page(image)})
    element = result.elements[0]
    assert element.metadata["inline_math_candidate"]
    assert len(element.source_regions) == 2
    assert element.page_numbers == [1]
    assert element.source_regions[0].asset_path != element.source_regions[1].asset_path
    assert crop(element.source_regions[0], settings).getpixel((0, 0)) == (255, 0, 0)
    assert crop(element.source_regions[1], settings).getpixel((0, 0)) == (0, 0, 255)
    assert element.metadata["provenance"][1]["charspan"] == [10, 15]


@pytest.mark.parametrize("label", ["figure", "picture", "formula", "table", "text"])
def test_parser_source_snapshot_is_present_for_every_element(tmp_path, label):
    extra = {}
    if label == "table":
        extra = {
            "export_to_markdown": lambda **_: "| value |\n| 7 |",
            "export_to_html": lambda **_: "<table><tr><td>7</td></tr></table>",
        }
    source_item = item(label, "x = 7", [provenance(1, bbox(10, 10, 90, 90))], **extra)
    result, _ = parse(tmp_path, [source_item], {1: page(Image.new("RGB", (100, 100), "white"))})
    element = result.elements[0]
    assert element.source_text == element.original_text
    assert element.source_structured_content == element.structured_content
    if label == "table":
        assert element.source_structured_content == "<table><tr><td>7</td></tr></table>"
        element.structured_content = "<table><tr><td>999</td></tr></table>"
        assert "999" not in element.source_structured_content


@pytest.mark.parametrize("bounds", [
    bbox(-1, 0, 20, 20),
    bbox(0, 0, 101, 20),
    bbox(10, 20, 10, 30),
    bbox(10, 30, 20, 20),
    bbox(0, 0, 20, 20, "UNKNOWN"),
    bbox(float("nan"), 0, 20, 20),
    None,
])
def test_invalid_or_partial_region_is_retained_without_claiming_crop_coverage(tmp_path, bounds):
    source_item = item("formula", "x^2", [provenance(1, bounds)])
    result, _ = parse(tmp_path, [source_item], {1: page(Image.new("RGB", (100, 100), "white"))})
    element = result.elements[0]
    assert len(element.source_regions) == 1
    assert element.source_regions[0].page_number == 1
    assert element.source_regions[0].asset_path is None
    assert element.asset_path is None and result.warnings
    assert not list((tmp_path / "attempt").rglob("*.png"))


@pytest.mark.parametrize("missing", ["page", "image", "page_size", "page_number"])
def test_missing_source_data_warns_and_does_not_use_ambiguous_embedded_image(tmp_path, missing):
    image_calls = []
    span = provenance(None if missing == "page_number" else 2, bbox(0, 0, 20, 20))
    pages = {1: page(Image.new("RGB", (100, 100), "white"))}
    if missing in {"image", "page_size"}:
        pages[2] = page(Image.new("RGB", (100, 100), "red"))
        setattr(pages[2], "image" if missing == "image" else "size", None)
    figure = item(
        "picture", "", [span],
        get_image=lambda _: image_calls.append(True) or Image.new("RGB", (20, 20), "green"),
    )
    result, _ = parse(tmp_path, [figure], pages)
    assert result.warnings and not image_calls
    assert result.elements[0].source_regions[0].asset_path is None
    assert result.elements[0].asset_path is None
    if missing == "page_number":
        assert result.elements[0].source_regions[0].page_number == 0
        assert result.elements[0].page_number is None


def test_first_valid_crop_keeps_legacy_asset_but_missing_region_stays_visible(tmp_path):
    formula = item("formula", "x + y", [
        provenance(2, bbox(10, 10, 20, 20)),
        provenance(1, bbox(20, 20, 40, 40)),
    ])
    result, _ = parse(tmp_path, [formula], {1: page(Image.new("RGB", (100, 100), "white"))})
    element = result.elements[0]
    assert element.source_regions[0].asset_path is None
    assert element.asset_path == element.source_regions[1].asset_path
    assert element.asset_path and result.warnings
    assert element.page_number == 2 and element.page_numbers == [1, 2]


def test_fractional_coordinates_round_outwards_without_downsampling(tmp_path):
    formula = item("formula", "x", [provenance(1, bbox(10.1, 10.1, 10.2, 10.2))])
    result, settings = parse(tmp_path, [formula], {1: page(Image.new("RGB", (200, 300), "red"))})
    assert not result.warnings
    assert crop(result.elements[0].source_regions[0], settings).size == (1, 1)


def test_no_provenance_cannot_claim_source_image_coverage(tmp_path):
    formula = item("formula", "x^2", [], get_image=lambda _: Image.new("RGB", (20, 20)))
    result, _ = parse(tmp_path, [formula], {1: page(Image.new("RGB", (100, 100), "white"))})
    assert result.elements[0].source_regions == []
    assert result.elements[0].asset_path is None
    assert any("no source provenance" in warning for warning in result.warnings)


def test_regular_text_records_provenance_without_creating_unneeded_crops(tmp_path):
    text = item("text", "Source paragraph without mathematical notation.", [
        provenance(1, bbox(10, 10, 40, 40)),
        provenance(2, bbox(10, 10, 40, 40)),
    ])
    result, _ = parse(tmp_path, [text], {
        1: page(Image.new("RGB", (100, 100))), 2: page(Image.new("RGB", (100, 100))),
    })
    element = result.elements[0]
    assert not result.warnings
    assert element.source_text == "Source paragraph without mathematical notation."
    assert len(element.source_regions) == 2
    assert not any(region.asset_path for region in element.source_regions)
    assert not list((tmp_path / "attempt").rglob("*.png"))
