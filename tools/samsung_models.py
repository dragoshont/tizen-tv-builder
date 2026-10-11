"""Model year from a Samsung TV model code, for display only.

Source: Samsung "TV Model Groups" (developer.samsung.com/smarttv/develop/specifications/
tv-model-groups.html, read 2026-10) and docs/compatibility.md. The year letter is a naming
convention, not a platform fact: Samsung lists regional/entry exceptions (for example a 2017
entry group on Tizen 2.4 and RU7000 sets inside a 2018 group), and TVs receive OS updates.
So a parsed year is "inferred from the model code", and the Tizen value for that year is a
baseline only. Never present either as the TV's observed OS version.
"""
import re

YEAR_LETTERS = {"J": 2015, "K": 2016, "M": 2017, "N": 2018, "R": 2019, "T": 2020,
                "A": 2021, "B": 2022, "C": 2023, "D": 2024, "F": 2025, "H": 2026}
BASELINE_TIZEN = {2015: "2.3", 2016: "2.4", 2017: "3.0", 2018: "4.0", 2019: "5.0", 2020: "5.5",
                  2021: "6.0", 2022: "6.5", 2023: "7.0", 2024: "8.0", 2025: "9.0", 2026: "10.0"}

# Region/brand prefix, then a two-digit panel size. GU/GQ are German-market prefixes.
_PREFIX = r"(?:UE|UN|UA|GU|QE|QN|QA|GQ)(?:\d{2})"
_FAMILIES = (
    # Crystal/UHD/FHD LED, 2015-2024: UE48J6200, UE55JU6400, UE55KS7000, UE55MU6172,
    # UE55NU7400, UE43RU7092, UE43TU7000, UE43AU7100, UE43BU8000, UE43CU7100, UE43DU7100.
    ("LED", re.compile(_PREFIX + r"(?P<y>[JKMNRTABCD])(?:U|S)?\d{4}[A-Z0-9]*")),
    # 2025+ LED puts the year letter after the series number: UE55U8000F.
    ("LED", re.compile(_PREFIX + r"U\d{4}(?P<y>[FH])[A-Z0-9]*")),
    # QLED 2019+: Q60R, Q80T, Q60A, Q60B, Q60C, Q60D; Neo QLED QN85A, QN90B, QN900C, QN90F.
    ("QLED", re.compile(_PREFIX + r"QN?\d{2,3}(?P<y>[RTABCDFH])[A-Z0-9]*")),
    # QLED 2018: Q6FN, Q7FN, Q8DN, Q9FN.
    ("QLED", re.compile(_PREFIX + r"Q\d[FCD]N[A-Z0-9]*", ), 2018),
    # QLED 2017 curved Q7C/Q8C only; flat Q7F/Q8F/Q9F is ambiguous with 2025 names.
    ("QLED", re.compile(_PREFIX + r"Q[78]C[A-Z0-9]*"), 2017),
    # OLED: S95B, S90C, S95D, S90F, S95H.
    ("OLED", re.compile(_PREFIX + r"S\d{2}(?P<y>[BCDFH])[A-Z0-9]*")),
    # The Frame / Serif / Sero / Terrace: LS03N, LS01R, LS03T, LS03A ... LS03H.
    ("Lifestyle", re.compile(_PREFIX + r"LS\d{2}(?P<y>[NRTABCDFH])[A-Z0-9]*")),
    # The Frame 2017: LS003.
    ("Lifestyle", re.compile(_PREFIX + r"LS003[A-Z0-9]*"), 2017),
)


def model_year(model):
    """{"year": int|None, "family": str|None, "baseline_tizen": str|None}; unknown when unsure."""
    code = (model or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,20}", code):
        return {"year": None, "family": None, "baseline_tizen": None}
    for family, pattern, *fixed in _FAMILIES:
        match = pattern.fullmatch(code)
        if match:
            year = fixed[0] if fixed else YEAR_LETTERS[match.group("y")]
            return {"year": year, "family": family, "baseline_tizen": BASELINE_TIZEN[year]}
    return {"year": None, "family": None, "baseline_tizen": None}
