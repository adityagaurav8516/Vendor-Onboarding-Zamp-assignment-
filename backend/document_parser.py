from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


def parse_bytes(filename: str, content: bytes) -> tuple[str, str]:
    """Return (status, text). Status is 'parsed' or 'unreadable'."""
    name = (filename or "").lower()
    if name.endswith(".txt"):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return "unreadable", ""
        return ("parsed", text) if text.strip() else ("unreadable", "")

    if not content.startswith(b"%PDF") and not name.endswith(".pdf"):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return "unreadable", ""
        return ("parsed", text) if len(text.strip()) >= 20 else ("unreadable", "")

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            return "unreadable", ""
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return "unreadable", ""
    text = "\n".join(pages).strip()
    if len(text) < 20:
        return "unreadable", ""
    return "parsed", text
