from upraise_ai.core.errors import IngestionError

# EasyOCR recognition languages; English can be combined with these scripts.
SUPPORTED = set(
    "af ar az be bg bn bs cs cy da de en es et fa fr ga hi hr hu id is it ja kn ko ku la lt lv mi mn mr ms mt ne nl no oc pl pt ro ru sk sl sq sv sw ta te th tr uk ur uz vi".split()
)


def ocr_languages(tag: str) -> list[str]:
    tag = tag.lower()
    primary = tag.split("-")[0]
    if primary == "zh":
        code = "ch_tra" if "hant" in tag or tag.endswith(("-tw", "-hk")) else "ch_sim"
    elif primary == "sr":
        code = "rs_latin" if "latn" in tag else "rs_cyrillic"
    elif primary in SUPPORTED:
        code = primary
    else:
        raise IngestionError("UNSUPPORTED_OCR_LANGUAGE", status=422)
    return [code] if code == "en" else [code, "en"]
