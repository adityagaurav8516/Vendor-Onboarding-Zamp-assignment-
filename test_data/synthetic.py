from __future__ import annotations

import textwrap
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

DOC_DIR = Path(__file__).resolve().parent / "documents"


def render_pdf(lines: list[str]) -> bytes:
    """Turn a short labelled document into a real, text-based PDF."""
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    page.setFont("Helvetica-Bold", 14)
    page.drawString(24 * mm, height - 28 * mm, lines[0])
    page.setFont("Helvetica", 10.5)
    y = height - 42 * mm
    for line in lines[1:]:
        for chunk in textwrap.wrap(line, 88) or [""]:
            if y < 24 * mm:
                page.showPage()
                page.setFont("Helvetica", 10.5)
                y = height - 28 * mm
            page.drawString(24 * mm, y, chunk)
            y -= 6.5 * mm
    page.showPage()
    page.save()
    return buffer.getvalue()


def document_bytes(stem: str, lines: list[str]) -> bytes:
    """Render once into test_data/documents so the files can be inspected."""
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    path = DOC_DIR / f"{stem}.pdf"
    if not path.exists():
        path.write_bytes(render_pdf(lines))
    return path.read_bytes()
