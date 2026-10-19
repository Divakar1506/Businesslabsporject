"""Turn uploaded files into text for the knowledge base. Free and offline.

Supported:
- .txt / .md   read as UTF-8
- .docx        Word: paragraphs, headings and tables, read straight from the file's XML (no library needed)
- .pdf         `pypdf` if it is installed; otherwise a basic built-in reader for normal text PDFs.
               Scanned PDFs (pictures of pages) need OCR and come back with a warning.
- .jpg / .png  stored and shown with answers. Text inside the picture is read only if an offline OCR
               library (`rapidocr_onnxruntime`) is installed; otherwise the uploader's description is used.
Old binary .doc files are not supported: save them as .docx.
"""
import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
TEXT_EXT = {".txt", ".md"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SUPPORTED = TEXT_EXT | IMAGE_EXT | {".pdf", ".docx"}


@dataclass
class Extracted:
    name: str
    kind: str                  # text | image | unsupported
    text: str = ""
    method: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)


def safe_name(name: str) -> str:
    """Keep only a plain file name (no folders, no odd characters)."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    return base[:120]


def extract(name: str, data: bytes) -> Extracted:
    ext = Path(name).suffix.lower()
    if len(data) > MAX_FILE_BYTES:
        return Extracted(name, "unsupported", warnings=[f"larger than {MAX_FILE_BYTES // 1024 // 1024} MB"])
    if ext not in SUPPORTED:
        hint = " Save it as .docx." if ext == ".doc" else ""
        return Extracted(name, "unsupported", warnings=[f"{ext or 'this file type'} is not supported.{hint}"])
    try:
        if ext in TEXT_EXT:
            return Extracted(name, "text", data.decode("utf-8", errors="replace").strip(), "plain text")
        if ext == ".docx":
            return _docx(name, data)
        if ext == ".pdf":
            return _pdf(name, data)
        return _image(name, data)
    except Exception as e:  # a broken file must not break the upload of the others
        return Extracted(name, "unsupported", warnings=[f"could not read the file: {type(e).__name__}"])


# --- Word ---------------------------------------------------------------------------
def _docx(name: str, data: bytes) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    lines = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        text = unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))).strip()
        if not text:
            continue
        style = re.search(r'<w:pStyle w:val="([^"]+)"', para)
        if style and re.match(r"(Heading|Title)", style.group(1), re.I):
            lines.append(f"## {text}")
        elif "<w:numPr>" in para:
            lines.append(f"- {text}")
        else:
            lines.append(text)
    text = "\n".join(lines)
    warn = [] if text else ["no text found in the Word document"]
    return Extracted(name, "text", text, "Word (.docx) XML", warn)


# --- PDF ------------------------------------------------------------------------------
def _pdf(name: str, data: bytes) -> Extracted:
    try:
        from pypdf import PdfReader  # optional, better quality
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "").strip() for p in reader.pages).strip()
        method = f"pypdf ({len(reader.pages)} pages)"
    except ImportError:
        text, method = _basic_pdf_text(data), "built-in basic PDF reader"
    warn = []
    if len(text) < 20:
        warn.append("almost no text found: this may be a scanned PDF (needs OCR) or uses unusual fonts")
    elif method.startswith("built-in"):
        warn.append("read with the basic reader; installing `pypdf` gives better results for complex PDFs")
    return Extracted(name, "text", text, method, warn)


def _basic_pdf_text(data: bytes) -> str:
    """Text-drawing operators (Tj, TJ, ', ") from every content stream, decompressed with zlib."""
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        if b"BT" not in raw:
            continue
        for block in re.findall(rb"BT(.*?)ET", raw, re.S):
            parts = []
            for op in re.finditer(rb"\[(.*?)\]\s*TJ|\((.*?)(?<!\\)\)\s*(?:Tj|'|\")|(T\*|Td|TD)", block, re.S):
                if op.group(3):
                    parts.append("\n")
                    continue
                if op.group(1) is not None:
                    pieces = re.findall(rb"\((.*?)(?<!\\)\)", op.group(1), re.S)
                    parts.append("".join(_pdf_string(p) for p in pieces))
                else:
                    parts.append(_pdf_string(op.group(2)))
            chunks.append("".join(parts))
    text = "\n".join(chunks)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _pdf_string(s: bytes) -> str:
    s = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"", b"t": b"\t", b"b": b"", b"f": b""}.get(
        m.group(1), m.group(1)), s)
    s = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), s)
    return s.decode("latin-1", errors="replace")


# --- images ---------------------------------------------------------------------------
def _image(name: str, data: bytes) -> Extracted:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
        fmt = im.format
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    try:
        from rapidocr_onnxruntime import RapidOCR  # optional offline OCR
        result, _ = RapidOCR()(data)
        text = "\n".join(r[1] for r in (result or []))
        return Extracted(name, "image", text, f"OCR (RapidOCR) on {fmt} {w}x{h}",
                         [] if text else ["no text found in the image"])
    except ImportError:
        return Extracted(name, "image", "", f"{fmt} {w}x{h}, stored",
                         ["text inside the image is not read (no OCR installed): describe it in the form"])
