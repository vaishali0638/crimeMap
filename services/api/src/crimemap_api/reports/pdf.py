"""A minimal, dependency-free PDF writer for text briefings (PDF 1.4, Helvetica, A4).

Enough for a watermarked, paginated report of headings, key/value lines and tables; no images,
no fonts beyond the 14 standard ones. Text is Latin-1: other characters are transliterated
(dashes, arrows) or replaced with "?". Every page carries the classification header, the
watermark and a page footer.
"""

import zlib

A4 = (595, 842)
MARGIN = 48
LINE = 12
_TRANSLIT = {
    "\u2014": "-",  # em dash
    "\u2013": "-",  # en dash
    "\u2019": "'",
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u00b2": "2",
    "\u25b2": "up",
    "\u25bc": "down",
    "\uff1d": "=",
    "\u2192": "->",
    "\u2265": ">=",
    "\u2264": "<=",
    "\u20b9": "Rs",
}


def _latin1(value: str) -> str:
    return (
        "".join(_TRANSLIT.get(ch, ch) for ch in value)
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def _escape(value: str) -> str:
    return (
        _latin1(value)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


class PdfDocument:
    def __init__(self, *, header: str, watermark: str, footer: str) -> None:
        self.header = header
        self.watermark = watermark
        self.footer = footer
        self.pages: list[list[str]] = []
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = A4[1] - MARGIN - 2 * LINE

    def _ensure(self, lines: int = 1) -> None:
        if self.y - lines * LINE < MARGIN + 2 * LINE:
            self._new_page()

    def _text(self, x: float, y: float, size: int, value: str, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        self.pages[-1].append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({_escape(value)}) Tj ET")

    def heading(self, value: str) -> None:
        self._ensure(3)
        self.y -= LINE / 2
        self._text(MARGIN, self.y, 13, value, bold=True)
        self.y -= LINE * 1.5

    def line(self, value: str, *, bold: bool = False, size: int = 9) -> None:
        width = int((A4[0] - 2 * MARGIN) / (size * 0.5))
        chunks = [value[i : i + width] for i in range(0, max(len(value), 1), width)] or [""]
        for chunk in chunks:
            self._ensure()
            self._text(MARGIN, self.y, size, chunk, bold=bold)
            self.y -= LINE

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        cols = max(len(headers), 1)
        col_w = (A4[0] - 2 * MARGIN) / cols
        max_chars = max(int(col_w / 4.6), 4)

        def row(values: list[str], bold: bool) -> None:
            self._ensure()
            for i, v in enumerate(values):
                text = v if len(v) <= max_chars else v[: max_chars - 1] + "~"
                self._text(MARGIN + i * col_w, self.y, 8, text, bold=bold)
            self.y -= LINE

        row(headers, True)
        for r in rows:
            row(r, False)
        self.y -= LINE / 2

    def _decorate(self, index: int, total: int) -> str:
        h = A4[1]
        ops = [
            # watermark: large, light grey, diagonal
            "q 0.85 g BT /F2 40 Tf 0.7071 0.7071 -0.7071 0.7071 150 250 Tm "
            f"({_escape(self.watermark)}) Tj ET Q",
            f"BT /F2 8 Tf {MARGIN} {h - MARGIN + 6:.1f} Td ({_escape(self.header)}) Tj ET",
            f"BT /F1 7 Tf {MARGIN} {MARGIN - 18:.1f} Td ({_escape(self.footer)} - page {index} of {total}) Tj ET",
        ]
        return "\n".join(ops)

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # placeholder 1
        pages_id = add(b"")  # placeholder 2
        f1 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        )
        f2 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        )
        kids = []
        total = len(self.pages)
        for i, ops in enumerate(self.pages, start=1):
            stream = zlib.compress(
                (self._decorate(i, total) + "\n" + "\n".join(ops)).encode("latin-1")
            )
            content = add(
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                + stream
                + b"\nendstream"
            )
            kids.append(
                add(
                    (
                        f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {A4[0]} {A4[1]}] "
                        f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {content} 0 R >>"
                    ).encode()
                )
            )
        objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objects[pages_id - 1] = (
            f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
        )
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for n, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
