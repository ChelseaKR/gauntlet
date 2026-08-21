"""Corpus ingestion: load source files into :class:`~civic_rag.models.Document`s.

Supported formats (dispatched by file extension):

- **Markdown** (``.md`` / ``.markdown``) — the default. Title is an optional
  ``title:`` in a leading ``--- ... ---`` front-matter block, else the first
  ``# H1`` if present (else the file stem); an optional ``lang: xx`` line in that
  same front-matter block sets the language; heading markup is turned into plain
  sentences so it never leaks into a chunk.
- **Plain text** (``.txt`` / ``.text``) — body is the file verbatim; a short first
  line is used as the title.
- **HTML** (``.html`` / ``.htm``) — tags are stripped (``script``/``style`` dropped);
  the ``<title>`` (else first ``<h1>``, else the stem) becomes the title.
- **PDF** (``.pdf``) — text-extracted via the optional ``pdf`` extra
  (``pip install 'civic-rag-starter-kit[pdf]'``); a clear error is raised if the
  extra isn't installed.

Each file is one document; its ``source`` tag is the path relative to the corpus
root (this is what shows up in citations). The default ``corpus.glob`` is
``**/*.md``; broaden it (e.g. ``**/*``) to ingest the other formats — files whose
extension isn't supported are skipped. No PII is required or expected in a corpus.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from civic_rag.config import Config
from civic_rag.determinism import sha256_text
from civic_rag.models import Block, Document
from civic_rag.text import normalize_ws

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
# The closing ``---`` may sit at end-of-file with no trailing newline (`\Z`), not
# just mid-file; otherwise such a file's front matter silently leaks into the body.
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_LANG = re.compile(r"^lang:\s*([a-zA-Z-]+)\s*$", re.MULTILINE)
_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_TEXT_SUFFIXES = frozenset({".txt", ".text"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_PDF_SUFFIXES = frozenset({".pdf"})
#: Extensions ``load_corpus`` will ingest; anything else under the glob is skipped.
SUPPORTED_SUFFIXES = _MARKDOWN_SUFFIXES | _TEXT_SUFFIXES | _HTML_SUFFIXES | _PDF_SUFFIXES

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "title"})
_BLOCK_TAGS = frozenset({"p", "div", "li", "br", "tr", "section", "article", "header", "footer"})


def _unquote(value: str) -> str:
    """Strip a single pair of surrounding matching quotes from a front-matter value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_markdown_headings(text: str) -> str:
    """Turn ``# Heading`` lines into plain sentences (``Heading.``) so heading
    markup never leaks into a chunk — and so a heading reads as its own sentence
    rather than fusing with the paragraph that follows it."""

    def repl(m: re.Match[str]) -> str:
        title = m.group(1).strip()
        return title if title.endswith((".", "!", "?")) else f"{title}."

    return _HEADING.sub(repl, text)


_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_TABLE_SEP = re.compile(r"^\s*\|[\s\-:|]+\|\s*$", re.MULTILINE)


def _extract_table_cells(line: str) -> list[str]:
    """Extract cell contents from a markdown table row (|cell1|cell2|...)."""
    # Split on | and strip whitespace, ignoring leading/trailing empty cells
    parts = [p.strip() for p in line.split("|")]
    return [p for p in parts if p]


class _MarkdownBlockBuilder:
    """Line-at-a-time state machine backing :func:`_build_blocks_from_markdown`.

    Broken out of that function (rather than one large loop) so each markdown
    construct — heading, table separator, table row, list item, plain text — is
    its own small, independently-readable method instead of one function whose
    branching spans every construct at once.
    """

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.heading_stack: list[tuple[int, str]] = []  # (level, text) pairs
        self.paragraph_lines: list[str] = []
        self.table_header: list[str] | None = None

    def _heading_path(self, *, exclude_last: bool = False) -> tuple[str, ...]:
        stack = self.heading_stack[:-1] if exclude_last else self.heading_stack
        return tuple(level_and_text[1] for level_and_text in stack)

    def _flush_paragraph(self) -> None:
        """Join any pending paragraph lines into a single Block, then clear them."""
        if not self.paragraph_lines:
            return
        para_text = " ".join(ln.strip() for ln in self.paragraph_lines if ln.strip())
        if para_text:
            self.blocks.append(
                Block(kind="paragraph", text=para_text, heading_path=self._heading_path())
            )
        self.paragraph_lines = []

    def _handle_heading(self, match: re.Match[str]) -> None:
        self._flush_paragraph()
        self.table_header = None

        level = len(match.group(0)) - len(match.group(0).lstrip("#"))
        heading_text = match.group(1).strip()
        # Pop any deeper headings from the stack.
        while self.heading_stack and self.heading_stack[-1][0] >= level:
            self.heading_stack.pop()
        self.heading_stack.append((level, heading_text))
        self.blocks.append(
            Block(
                kind="heading",
                text=heading_text,
                heading_path=self._heading_path(exclude_last=True),
                heading_level=level,
            )
        )

    def _handle_table_separator(self, line_number: int) -> None:
        # The previous line (if present) is the header row.
        if line_number >= 2 and self.paragraph_lines:
            self.table_header = _extract_table_cells(self.paragraph_lines[-1])
            self.paragraph_lines.pop()  # it's the header, not paragraph content
            self._flush_paragraph()

    def _handle_table_row(self, line: str) -> bool:
        """Emit a table_row Block if a table header is active. Returns whether
        `line` was consumed as a table row (only if we're in a table)."""
        if not (self.table_header and _TABLE_ROW.match(line)):
            return False
        cells = _extract_table_cells(line)
        if cells:
            # Prefix with the header row for self-description.
            row_text = " | ".join(self.table_header) + " | " + " | ".join(cells)
            self.blocks.append(
                Block(kind="table_row", text=row_text, heading_path=self._heading_path())
            )
        return True

    def _handle_list_item(self, match: re.Match[str]) -> None:
        self._flush_paragraph()
        self.table_header = None
        item_text = match.group(1).strip()
        self.blocks.append(
            Block(kind="list_item", text=item_text, heading_path=self._heading_path())
        )

    def _handle_text_line(self, line: str) -> None:
        if line.strip():
            self.paragraph_lines.append(line)
        elif self.paragraph_lines:
            # A blank line ends a paragraph.
            self._flush_paragraph()
            self.table_header = None

    def build(self, text: str) -> tuple[Block, ...]:
        for line_number, line in enumerate(text.split("\n"), start=1):
            heading_match = _HEADING.match(line)
            if heading_match:
                self._handle_heading(heading_match)
                continue
            if _TABLE_SEP.match(line):
                self._handle_table_separator(line_number)
                continue
            if self._handle_table_row(line):
                continue
            list_match = _LIST_ITEM.match(line)
            if list_match:
                self._handle_list_item(list_match)
                continue
            self._handle_text_line(line)

        self._flush_paragraph()
        return tuple(self.blocks)


def _build_blocks_from_markdown(text: str) -> tuple[Block, ...]:
    """Parse markdown text into structural blocks (headings, paragraphs, lists, tables).

    Returns a tuple of Block objects. Each block preserves its heading context (heading_path)
    and kind. Table rows are prefixed with their header row for self-description.
    """
    return _MarkdownBlockBuilder().build(text)


def _split_markdown_front_matter(raw: str) -> tuple[str, str | None, str | None]:
    """Strip an optional leading ``--- ... ---`` front-matter block.

    Returns ``(body, front_title, language)`` where ``body`` still has its heading
    markup (``#``) intact — callers that need heading markers (e.g. block-building)
    should use this instead of :func:`_parse_markdown`, whose ``body`` has already
    had headings converted to plain sentences. ``front_title``/``language`` are
    ``None`` when not set in front matter.
    """
    fm = _FRONT.match(raw)
    if not fm:
        return raw, None, None
    block = fm.group(1)
    lang_match = _LANG.search(block)
    language = lang_match.group(1).lower() if lang_match else None
    title_match = _TITLE.search(block)
    front_title = (_unquote(title_match.group(1)) or None) if title_match else None
    return raw[fm.end() :], front_title, language


def _parse_markdown(raw: str, default_language: str) -> tuple[str | None, str, str]:
    body, front_title, language = _split_markdown_front_matter(raw)
    # Title precedence: front-matter ``title:`` > first ``# H1`` > (file stem, in _parse).
    h1 = _H1.search(body)
    title = front_title or (h1.group(1) if h1 else None)
    return title, _strip_markdown_headings(body), language or default_language


def _parse_text(raw: str, default_language: str) -> tuple[str | None, str, str]:
    body = raw.strip()
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), None)
    # Use the first non-empty line as a title only if it reads like a heading,
    # not a full paragraph.
    title = first if (first is not None and len(first) <= 80) else None
    return title, body, default_language


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text and a title from HTML, dropping script/style and
    giving headings/blocks sentence boundaries so words never fuse across them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []
        self._first_heading: str | None = None
        self._heading_buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _HEADING_TAGS:
            if self._first_heading is None and self._heading_buf is None:
                self._heading_buf = []
            self._parts.append("\n")
        elif tag in _BLOCK_TAGS:
            # Separate on the *start* tag too: void elements (<br>) never emit an
            # end-tag event, and HTML's implied end tags (<p>a<p>b, <li> without
            # </li>) don't either — without this, words fuse across those boundaries.
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            # Title text goes to the title only, so no sentence boundary to emit here.
            self._in_title = False
            return
        if tag in _HEADING_TAGS:
            if self._heading_buf is not None and self._first_heading is None:
                self._first_heading = "".join(self._heading_buf).strip() or None
            self._heading_buf = None
            self._parts.append(". ")  # headings end a sentence
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            # <title> is document metadata, not body copy — collect it for the title
            # and keep it out of the chunkable text.
            self._title_parts.append(data)
            return
        if self._heading_buf is not None:
            self._heading_buf.append(data)
        self._parts.append(data)

    @property
    def title(self) -> str | None:
        return "".join(self._title_parts).strip() or self._first_heading

    @property
    def text(self) -> str:
        return normalize_ws("".join(self._parts))


def _parse_html(raw: str, default_language: str) -> tuple[str | None, str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(raw)
    parser.close()
    return parser.title, parser.text, default_language


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # the optional 'pdf' extra isn't installed
        raise RuntimeError(
            f"PDF ingestion needs the 'pdf' extra to read {path.name!r}: "
            "pip install 'civic-rag-starter-kit[pdf]'"
        ) from exc
    reader = PdfReader(str(path))  # pragma: no cover - needs pypdf + a binary PDF
    return "\n".join(page.extract_text() or "" for page in reader.pages)  # pragma: no cover


def _parse(path: Path, root: Path, default_language: str) -> Document | None:
    """Parse one file into a Document, or return None if its format is unsupported."""
    suffix = path.suffix.lower()
    blocks: tuple[Block, ...] = ()
    if suffix in _MARKDOWN_SUFFIXES:
        raw = path.read_text(encoding="utf-8")
        title, body, language = _parse_markdown(raw, default_language)
        # Build structural blocks from the pre-heading-strip body: _parse_markdown's
        # `body` has already had `#` markers converted to plain sentences (so they
        # never leak into a chunk as raw markup), but block-building needs those
        # markers intact to detect headings and build the heading-path hierarchy.
        heading_intact_body, _, _ = _split_markdown_front_matter(raw)
        blocks = _build_blocks_from_markdown(heading_intact_body)
    elif suffix in _TEXT_SUFFIXES:
        title, body, language = _parse_text(path.read_text(encoding="utf-8"), default_language)
    elif suffix in _HTML_SUFFIXES:
        title, body, language = _parse_html(path.read_text(encoding="utf-8"), default_language)
    elif suffix in _PDF_SUFFIXES:
        title, body, language = None, _extract_pdf(path), default_language
    else:
        return None
    if title is None:
        title = path.stem.replace("-", " ").title()
    source = str(path.relative_to(root))
    return Document(
        doc_id=sha256_text(source)[:12],
        title=title,
        text=body.strip(),
        source=source,
        language=language,
        blocks=blocks,
    )


def load_corpus(config: Config) -> list[Document]:
    """Load and return every supported document in the configured corpus, sorted by
    source for deterministic ordering. Files whose extension isn't supported are
    skipped; an empty result is an explicit error (fail loud)."""
    root = Path(config.corpus.path)
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")
    docs: list[Document] = []
    for p in sorted(root.glob(config.corpus.glob)):
        if not p.is_file():
            continue
        doc = _parse(p, root, config.corpus.default_language)
        if doc is not None:
            docs.append(doc)
    if not docs:
        raise ValueError(
            f"no supported documents matched {config.corpus.glob!r} under {root} "
            f"(supported: {', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )
    return docs
