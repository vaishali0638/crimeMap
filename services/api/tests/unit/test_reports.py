"""Exports: CSV injection protection, watermark and metadata, and a structurally valid PDF."""

import csv
import io
import re
import zlib
from datetime import datetime

from crimemap_api.reports.pdf import PdfDocument
from crimemap_api.reports.render import WATERMARK, render_csv, render_pdf

META = {
    "title": "Overview",
    "explanation": "Headline counts.",
    "classification": "RESTRICTED",
    "environment": "DEMO",
    "filters_applied": {"region": "PDY", "locality": '=HYPERLINK("x")'},
    "record_count": 5,
    "excluded_count": 1,
    "excluded_reasons": {"no_usable_location_on_map": 1},
    "suppressed": None,
    "data_freshness": "2026-10-12T00:00:00Z",
    "data_quality_note": "fine",
    "method": "counts",
    "method_parameters": {"hex_size_m": 1000},
    "caveats": ["Synthetic."],
}
DATA = {
    "kpis": {
        "selected": 5,
        "comparison_selected": 4,
        "change_pct": 25.0,
        "usable_location_pct": 80.0,
        "open_review_items": 1,
    },
    "trend": {"grain": "week", "points": [{"bucket": "2026-07-06", "count": 5}]},
    "categories": [
        {"code": "X", "label": "=cmd|' /C calc'!A0", "count": 5},
        {"code": "Y", "label": "+1+1", "count": 0},
    ],
    "quality": [{"status": "OK", "count": 5}],
}
WHO = {
    "user": "demo.analyst",
    "generated_at": datetime(2026, 10, 12, 18, 30),
    "snapshot_id": "00000000-0000-0000-0000-000000000000",
}


def test_csv_neutralises_formulas_and_carries_metadata() -> None:
    text = render_csv("overview", DATA, META, **WHO).decode("utf-8-sig")
    cells = [c for row in csv.reader(io.StringIO(text)) for c in row]
    assert all(not c.startswith(("=", "+", "@")) for c in cells)
    assert all(not c.startswith("-") or re.fullmatch(r"-\d+(\.\d+)?", c) for c in cells)
    assert "'=cmd|' /C calc'!A0" in cells
    assert "'+1+1" in cells
    assert text.splitlines()[0] == f"# {WATERMARK}"
    for needle in (
        "Requested by",
        "demo.analyst",
        "Generated (IST)",
        "2026-10-12 18:30",
        "Classification",
        "expires 24 hours",
    ):
        assert needle in text


def test_pdf_is_structurally_valid_and_watermarked_on_every_page() -> None:
    content = render_pdf(
        "overview",
        DATA
        | {
            "trend": {
                "grain": "day",
                "points": [{"bucket": f"d{i}", "count": i} for i in range(200)],
            }
        },
        META,
        **WHO,
    )
    assert content.startswith(b"%PDF-1.4")
    xref = int(re.search(rb"startxref\n(\d+)\n%%EOF", content).group(1))  # type: ignore[union-attr]
    assert content[xref : xref + 4] == b"xref"
    offsets = [int(o) for o in re.findall(rb"(\d{10}) 00000 n ", content)]
    for n, off in enumerate(offsets, start=1):
        assert content[off:].startswith(f"{n} 0 obj".encode())
    pages = re.findall(rb"stream\n(.*?)\nendstream", content, re.S)
    assert len(pages) >= 2
    for page in pages:
        assert zlib.decompress(page).decode("latin-1").count(WATERMARK) >= 2


def test_pdf_escapes_text_and_transliterates() -> None:
    doc = PdfDocument(header="h", watermark="w", footer="f")
    doc.line("a (b) \\ c — d ▲")
    body = zlib.decompress(re.findall(rb"stream\n(.*?)\nendstream", doc.render(), re.S)[0]).decode(
        "latin-1"
    )
    assert "a \\(b\\) \\\\ c - d up" in body


def test_csv_keeps_negative_numbers_numeric_and_missing_values_empty() -> None:
    data = {
        "kind": "PERIOD",
        "a": {"label": "A", "count": 5},
        "b": {"label": "B", "count": 10},
        "difference": {"difference": -5, "change_pct": -50.0},
        "table": [{"label": "Theft", "a": 1, "b": 3, "difference": -2, "change_pct": None}],
    }
    cells = [
        c
        for row in csv.reader(
            io.StringIO(render_csv("comparison", data, META, **WHO).decode("utf-8-sig"))
        )
        for c in row
    ]
    assert "-5" in cells
    assert "-50" in cells
    assert "-2" in cells
    assert "'-" not in cells


def test_year_comparison_on_29_february() -> None:
    from datetime import date

    from crimemap_api.analytics.query import _window, comparison_window

    w = comparison_window(_window(date(2024, 2, 1), date(2024, 2, 29)), "YEAR")
    assert (w.start, w.end) == (date(2023, 2, 1), date(2023, 2, 28))  # type: ignore[union-attr]
