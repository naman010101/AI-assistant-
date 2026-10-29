"""Turning files and images into something a model can read.

    att = load_file(r"C:\\Users\\me\\Documents\\notes.pdf")   # -> Attachment
    att = load_image(pil_image)                              # Ctrl+V screenshot
    paths = find_paths(message_text)                         # pasted paths
    content, notes = build_message(question, [att, ...], context_tokens=8192)

What can be read
  text and code   .py .js .java .c .cpp .md .txt .csv .json .html … read directly
  notebooks       .ipynb, cell by cell
  PDF             text layer via pypdf (scanned PDFs have no text yet: OCR later)
  Word            .docx via python-docx (paragraphs and tables)
  Excel           .xlsx via openpyxl, first rows of each sheet
  images          .png .jpg .webp .gif .bmp, shrunk to fit and sent to a vision model

Access checks are NOT done here — the caller asks access.AccessGate first.
"""
import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field

MAX_FILE_MB = 25
IMAGE_MAX_SIDE = 1600   # px; bigger images cost a lot of tokens for little gain
XLSX_MAX_ROWS = 200

TEXT_EXT = {
    # code
    ".py", ".pyw", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".kts",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".cs", ".go", ".rs", ".rb", ".php", ".swift",
    ".scala", ".r", ".m", ".jl", ".lua", ".pl", ".dart", ".sql", ".sh", ".bash", ".zsh", ".ps1",
    ".bat", ".cmd", ".vb", ".asm", ".s", ".v", ".sv", ".vhd", ".hs", ".ex", ".exs", ".erl", ".clj",
    ".groovy", ".gradle", ".cmake", ".mk", ".makefile", ".dockerfile", ".tf", ".proto",
    # web / data / config
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".xml", ".svg",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".csv", ".tsv", ".env.example", ".gitignore", ".editorconfig",
    # writing
    ".txt", ".md", ".markdown", ".rst", ".tex", ".bib", ".log", ".srt", ".vtt",
}
TEXT_NAMES = {"makefile", "dockerfile", "readme", "license", "requirements.txt", "procfile"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java", ".c": "c",
        ".cpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".sql": "sql", ".html": "html",
        ".css": "css", ".json": "json", ".sh": "bash", ".ps1": "powershell", ".md": "markdown"}


class AttachError(Exception):
    """A file that can't be attached; the message is shown to the user."""


@dataclass
class Attachment:
    name: str
    kind: str                  # text | code | pdf | docx | xlsx | notebook | image
    path: str = None           # None for pasted images
    size: int = 0              # bytes on disk
    text: str = ""             # extracted text (not for images)
    image_url: str = None      # data: URL for images
    detail: str = ""           # "12 pages", "1280×720", "3 sheets"
    source: str = "file"       # file | paste | path
    sha: str = None            # fingerprint of the file when attached (to spot later changes)
    cut: bool = False          # True if only part of it fit in the message

    @property
    def is_image(self):
        return self.kind == "image"

    def label(self):
        bits = [self.name]
        if self.detail:
            bits.append(self.detail)
        elif self.size:
            bits.append(human_size(self.size))
        return " · ".join(bits)

    @property
    def editable(self):
        """Can the assistant propose changes to it? Only whole text/code files."""
        return self.kind in ("code", "text") and bool(self.path) and not self.cut

    def meta(self):
        """What's saved with the chat (no file contents, no image data)."""
        return {"name": self.name, "kind": self.kind, "path": self.path, "size": self.size,
                "detail": self.detail, "sha": self.sha, "editable": self.editable}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ---------------------------------------------------------------- loading
def load_file(path, source="file"):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise AttachError(f"Not a file: {path}")
    size = os.path.getsize(path)
    if size > MAX_FILE_MB * 1024 * 1024:
        raise AttachError(f"{os.path.basename(path)} is {human_size(size)}; the limit is {MAX_FILE_MB} MB.")
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()

    if ext in IMAGE_EXT:
        from PIL import Image
        try:
            with Image.open(path) as img:
                att = load_image(img, name=name)
        except AttachError:
            raise
        except Exception as e:
            raise AttachError(f"Couldn't open the image {name}: {e}") from None
        att.path, att.size, att.source = path, size, source
        return att

    if ext == ".pdf":
        text, detail = _read_pdf(path)
        kind = "pdf"
    elif ext == ".docx":
        text, detail = _read_docx(path)
        kind = "docx"
    elif ext in (".xlsx", ".xlsm"):
        text, detail = _read_xlsx(path)
        kind = "xlsx"
    elif ext == ".ipynb":
        text, detail = _read_notebook(path)
        kind = "notebook"
    elif ext in (".doc", ".xls", ".ppt", ".pptx"):
        raise AttachError(f"{ext} files aren't supported yet. Save it as "
                          f"{'.docx' if ext == '.doc' else '.xlsx' if ext == '.xls' else 'PDF'} and attach that.")
    else:
        if ext not in TEXT_EXT and name.lower() not in TEXT_NAMES and not _looks_like_text(path):
            raise AttachError(f"{name} doesn't look like a text file, PDF, Word file or image.")
        text = _read_text(path)
        detail = f"{text.count(chr(10)) + 1} lines"
        kind = "code" if ext in LANG or ext in {".kt", ".swift", ".rb", ".php", ".cpp", ".h"} else "text"

    if not text.strip():
        hint = " It may be a scanned PDF (no text layer); OCR comes in a later version." if ext == ".pdf" else ""
        raise AttachError(f"No readable text in {name}.{hint}")
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return Attachment(name=name, kind=kind, path=path, size=size, text=text, detail=detail,
                      source=source, sha=sha)


def load_image(img, name=None):
    """A PIL image (from a file or the clipboard) -> Attachment with a data URL."""
    from PIL import Image
    name = name or time.strftime("pasted-image-%H%M%S.png")
    w, h = img.size
    img = img.copy()
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    buf = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, "JPEG", quality=88)
        mime = "image/jpeg"
    data = buf.getvalue()
    return Attachment(name=name, kind="image", size=len(data), source="paste",
                      image_url=f"data:{mime};base64,{base64.b64encode(data).decode()}",
                      detail=f"{w}×{h}")


# ---------------------------------------------------------------- readers
def _looks_like_text(path):
    with open(path, "rb") as f:
        head = f.read(8192)
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return sum(32 <= b < 127 or b in (9, 10, 13) for b in head) / max(len(head), 1) > 0.9


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _need(module, pip_name):
    try:
        return __import__(module)
    except ImportError:
        raise AttachError(f"Reading this needs the {pip_name} package. In the project folder run:\n"
                          f"venv\\Scripts\\python -m pip install {pip_name}") from None


def _read_pdf(path):
    pypdf = _need("pypdf", "pypdf")
    try:
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise AttachError("This PDF is password-protected.") from None
        pages = []
        for i, page in enumerate(reader.pages, 1):
            t = (page.extract_text() or "").strip()
            if t:
                pages.append(f"--- page {i} ---\n{t}")
        n = len(reader.pages)
    except AttachError:
        raise
    except Exception as e:
        raise AttachError(f"Couldn't read the PDF: {e}") from None
    return "\n\n".join(pages), f"{n} page{'s' if n != 1 else ''}"


def _read_docx(path):
    docx = _need("docx", "python-docx")
    try:
        doc = docx.Document(path)
    except Exception as e:
        raise AttachError(f"Couldn't read the Word file: {e}") from None
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            style = (p.style.name or "").lower() if p.style is not None else ""
            prefix = "#" * int(style[-1]) + " " if style.startswith("heading") and style[-1:].isdigit() else ""
            parts.append(prefix + p.text)
    for ti, table in enumerate(doc.tables, 1):
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        parts.append(f"[table {ti}]\n" + "\n".join(rows))
    words = sum(len(p.split()) for p in parts)
    return "\n\n".join(parts), f"{words:,} words"


def _read_xlsx(path):
    openpyxl = _need("openpyxl", "openpyxl")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise AttachError(f"Couldn't read the Excel file: {e}") from None
    out = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= XLSX_MAX_ROWS:
                rows.append(f"… (more rows not shown; sheet has {ws.max_row} rows)")
                break
            if any(v is not None for v in row):
                rows.append(",".join("" if v is None else str(v) for v in row))
        out.append(f"[sheet: {ws.title}]\n" + "\n".join(rows))
    n = len(wb.worksheets)
    wb.close()
    return "\n\n".join(out), f"{n} sheet{'s' if n != 1 else ''}"


def _read_notebook(path):
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
    except Exception as e:
        raise AttachError(f"Couldn't read the notebook: {e}") from None
    parts = []
    cells = nb.get("cells", [])
    for i, cell in enumerate(cells, 1):
        src = "".join(cell.get("source", []))
        if not src.strip():
            continue
        if cell.get("cell_type") == "code":
            parts.append(f"# [cell {i}: code]\n{src}")
        else:
            parts.append(f"# [cell {i}: {cell.get('cell_type', 'text')}]\n{src}")
    return "\n\n".join(parts), f"{len(cells)} cells"


# ---------------------------------------------------------------- pasted paths
_DRIVE_START = re.compile(r'(?<![\w/\\])([A-Za-z]:[\\/])')


def find_paths(text):
    """Existing files whose full paths appear in the text. Handles quotes and
    paths with spaces (C:\\Users\\NAMAN SALUJA\\Documents\\a b.pdf)."""
    found = []
    for m in re.finditer(r'"([A-Za-z]:[\\/][^"\n]+)"|\'([A-Za-z]:[\\/][^\'\n]+)\'', text):
        p = m.group(1) or m.group(2)
        if os.path.isfile(p) and p not in found:
            found.append(p)
    for m in _DRIVE_START.finditer(text):
        rest = text[m.start():].split("\n", 1)[0]
        best = None
        # try every place an extension could end, longest first
        ends = [e.end() for e in re.finditer(r'\.[A-Za-z0-9]{1,8}(?=$|[\s,;:!?)\]"\'])', rest)]
        for end in sorted(ends, reverse=True):
            cand = rest[:end].rstrip(".,;:")
            if os.path.isfile(cand):
                best = cand
                break
        if best and best not in found and not any(best in f or f in best for f in found):
            found.append(best)
    return found


# ---------------------------------------------------------------- message
def build_message(question, attachments, context_tokens=8192):
    """Combine the question with the attachments.

    Returns (content, notes):
      content  a string, or a list of parts when there are images
               (the OpenAI-style format Ollama and Gemini accept)
      notes    plain-language warnings for the user, e.g. a file was cut
    """
    notes = []
    texts = [a for a in attachments if not a.is_image]
    images = [a for a in attachments if a.is_image]

    # Leave room for the question, the reply and earlier messages: files get
    # about 60% of the window. ~3.5 characters per token is a safe estimate.
    budget = int(context_tokens * 0.6 * 3.5)
    shares = _fair_shares([len(a.text) for a in texts], budget)

    blocks = []
    for a, share in zip(texts, shares):
        body = a.text
        cut = ""
        a.cut = len(body) > share
        if a.cut:
            body = body[:share]
            cut = f' note="cut: first {share:,} of {len(a.text):,} characters"'
            notes.append(f"{a.name} is long, so only the first {share / max(len(a.text), 1):.0%} "
                         f"was included, so it can't be edited here. Ask about a specific part, or "
                         f"raise the context size in Settings.")
        where = f' path="{a.path}"' if a.path else ""
        info = f' info="{a.detail}"' if a.detail else ""
        blocks.append(f'<file name="{a.name}"{where}{info}{cut}>\n{body}\n</file>')

    q = question.strip()
    if not q:
        q = ("Describe this image." if images and not texts else
             "Explain what's in the attached file." if len(attachments) == 1 else
             "Explain what's in the attached files.")
    if images:
        names = ", ".join(a.name for a in images)
        blocks.append(f"(Attached image{'s' if len(images) > 1 else ''}: {names})")
    text = ("\n\n".join(blocks) + "\n\n" + q) if blocks else q

    if not images:
        return text, notes
    parts = [{"type": "text", "text": text}]
    parts += [{"type": "image_url", "image_url": {"url": a.image_url}} for a in images]
    return parts, notes


def _fair_shares(lengths, budget):
    """Split `budget` characters so short files are kept whole and long ones
    share what's left equally."""
    shares = [0] * len(lengths)
    remaining, left = budget, len(lengths)
    for i in sorted(range(len(lengths)), key=lambda i: lengths[i]):
        share = remaining // left if left else 0
        shares[i] = min(lengths[i], share)
        remaining -= shares[i]
        left -= 1
    return shares
