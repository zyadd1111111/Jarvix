"""Local, bounded document parsers. No macros, formulas, links or code are executed."""
from __future__ import annotations

import csv
import io
import posixpath
import re
import stat
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from jarvix.runtime import check_cancelled

MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 12 * 1024 * 1024
MAX_CHARS = 2_000_000
MAX_SEGMENTS = 50_000
CHUNK_CHARS = 1800
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class Document:
    def __init__(self):
        self.segments = []
        self.characters = 0
        self.warnings = []

    def add(self, text, location, kind="text"):
        check_cancelled()
        if not text:
            return
        self.characters += len(text)
        if self.characters > MAX_CHARS:
            raise ValueError("Document exceeds the 2 million character extraction limit; split the document.")
        parts = (len(text) + CHUNK_CHARS - 1) // CHUNK_CHARS
        for part, start in enumerate(range(0, len(text), CHUNK_CHARS), 1):
            if len(self.segments) >= MAX_SEGMENTS:
                raise ValueError("Document contains too many text fragments; split the document.")
            source = dict(location)
            if parts > 1:
                source.update(part=part, parts=parts, character_start=start)
            self.segments.append({"kind": kind, "text": text[start:start + CHUNK_CHARS], "location": source})


class OfficeArchive:
    """Validate the entire ZIP before reading XML; never extract to the filesystem."""

    def __init__(self, data):
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        entries = self.zip.infolist()
        if len(entries) > 3000:
            raise ValueError("Office document contains too many archive entries.")
        self.names = set()
        total = 0
        for entry in entries:
            check_cancelled()
            name = entry.filename
            path = PurePosixPath(name)
            if (name != entry.orig_filename or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                    or name.casefold() in self.names or entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("Unsafe Office archive entry.")
            self.names.add(name.casefold())
            total += entry.file_size
            if (total > MAX_EXPANDED_BYTES or entry.file_size > MAX_XML_BYTES
                    or entry.file_size > max(1, entry.compress_size) * 250):
                raise ValueError("Office archive exceeds safe decompression limits.")
        self.loaded = 0
        self.cache = {}

    def xml(self, name, optional=False):
        check_cancelled()
        if name in self.cache:
            return self.cache[name]
        try:
            with self.zip.open(name) as stream:
                data = stream.read(MAX_XML_BYTES + 1)
        except KeyError:
            if optional:
                return None
            raise ValueError("Document is missing a required Office XML part.") from None
        self.loaded += len(data)
        if len(data) > MAX_XML_BYTES or self.loaded > MAX_EXPANDED_BYTES:
            raise ValueError("Office XML exceeds extraction limits.")
        # OOXML is UTF-8/UTF-16 XML. Normalize before rejecting DTD/entity declarations,
        # including UTF-16 encodings which evade a byte-level ASCII substring check.
        if data.startswith((b"\xff\xfe", b"\xfe\xff")) or data[:4] in (b"<\x00?\x00", b"\x00<\x00?"):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise ValueError("DTD and entity declarations are not allowed.")
        root = ET.fromstring(text)
        if sum(1 for _ in root.iter()) > 150_000:
            raise ValueError("Office XML contains too many elements.")
        self.cache[name] = root
        check_cancelled()
        return root

    def relationships(self, part):
        parent, filename = posixpath.split(part)
        root = self.xml(posixpath.join(parent, "_rels", filename + ".rels"), optional=True)
        result = {}
        if root is None:
            return result
        for item in root.findall(REL + "Relationship"):
            if item.get("TargetMode") == "External":
                continue
            target = item.get("Target", "")
            if not target or "\\" in target or ":" in target:
                raise ValueError("Invalid internal Office relationship.")
            resolved = posixpath.normpath(posixpath.join(parent, target)) if not target.startswith("/") else target[1:]
            if resolved.startswith("../") or resolved not in self.zip.namelist():
                raise ValueError("Internal Office relationship points outside the archive.")
            result[item.get("Id")] = resolved
        return result


def _paragraph(node, ns):
    pieces = []
    for child in node.iter():
        if child.tag == ns + "t":
            pieces.append(child.text or "")
        elif child.tag == ns + "tab":
            pieces.append("\t")
        elif child.tag in {ns + "br", ns + "cr"}:
            pieces.append("\n")
    return "".join(pieces)


def docx(data, document):
    archive = OfficeArchive(data)
    try:
        body = archive.xml("word/document.xml").find(W + "body")
        if body is None:
            raise ValueError("Word document has no body.")
        paragraph, table, section = 0, 0, ""
        for node in body:
            check_cancelled()
            if node.tag == W + "p":
                paragraph += 1
                text = _paragraph(node, W)
                style = node.find(W + "pPr/" + W + "pStyle")
                heading = style is not None and style.get(W + "val", "").casefold().startswith(("heading", "title"))
                if heading:
                    section = text[:200]
                document.add(text, {"paragraph": paragraph, "section": section}, "heading" if heading else "text")
            elif node.tag == W + "tbl":
                table += 1
                for row, tr in enumerate(node.findall(W + "tr"), 1):
                    for column, tc in enumerate(tr.findall(W + "tc"), 1):
                        text = "\n".join(_paragraph(p, W) for p in tc.iter(W + "p"))
                        document.add(text, {"table": table, "row": row, "column": column, "section": section}, "table_cell")
        # Footnotes/endnotes are real source text, not comments or tracked deletions.
        for part, tag in (("footnotes", "footnote"), ("endnotes", "endnote")):
            root = archive.xml(f"word/{part}.xml", optional=True)
            if root is not None:
                for note in root.findall(W + tag):
                    if note.get(W + "type") in {"separator", "continuationSeparator"}:
                        continue
                    note_id = note.get(W + "id", "")
                    if len(note_id) > 80:
                        raise ValueError("Invalid Word note identifier.")
                    for paragraph, node in enumerate(note.findall(W + "p"), 1):
                        document.add(_paragraph(node, W), {"note_type": tag, "note_id": note_id, "paragraph": paragraph})
        document.warnings.append("Headers, footers, comments, drawings, embedded objects and tracked deletions are not extracted.")
    finally:
        archive.zip.close()


def pptx(data, document):
    archive = OfficeArchive(data)
    try:
        root = archive.xml("ppt/presentation.xml")
        relationships = archive.relationships("ppt/presentation.xml")
        for slide, item in enumerate(root.findall(P + "sldIdLst/" + P + "sldId"), 1):
            check_cancelled()
            target = relationships.get(item.get(R + "id"))
            if not target:
                raise ValueError("Presentation slide relationship is unavailable.")
            tree = archive.xml(target)
            shape = 0
            for node in tree.iter():
                if node.tag == P + "sp":
                    shape += 1
                    placeholder = node.find(".//" + P + "ph")
                    title = placeholder is not None and placeholder.get("type") in {"title", "ctrTitle"}
                    for paragraph, p in enumerate(node.iter(A + "p"), 1):
                        document.add(_paragraph(p, A), {"slide": slide, "shape": shape, "paragraph": paragraph},
                                     "heading" if title else "text")
                elif node.tag == A + "tbl":
                    shape += 1
                    for row, tr in enumerate(node.findall(A + "tr"), 1):
                        for column, tc in enumerate(tr.findall(A + "tc"), 1):
                            document.add("\n".join(_paragraph(p, A) for p in tc.iter(A + "p")),
                                         {"slide": slide, "table": shape, "row": row, "column": column}, "table_cell")
        document.warnings.append("Images, charts, speaker notes and embedded objects are not extracted.")
    finally:
        archive.zip.close()


def xlsx(data, document):
    archive = OfficeArchive(data)
    try:
        root = archive.xml("xl/workbook.xml")
        relationships = archive.relationships("xl/workbook.xml")
        strings_root = archive.xml("xl/sharedStrings.xml", optional=True)
        strings = [] if strings_root is None else ["".join(t.text or "" for t in item.iter(S + "t"))
                                                   for item in strings_root.findall(S + "si")]
        for sheet in root.findall(S + "sheets/" + S + "sheet"):
            check_cancelled()
            name = sheet.get("name", "Sheet")
            target = relationships.get(sheet.get(R + "id"))
            if not target:
                raise ValueError("Worksheet relationship is unavailable.")
            for row_number, row in enumerate(archive.xml(target).findall(S + "sheetData/" + S + "row"), 1):
                row_reference = row.get("r", str(row_number))
                if not row_reference.isascii() or not row_reference.isdecimal() or len(row_reference) > 7 or not 1 <= int(row_reference) <= 1048576:
                    raise ValueError("Invalid spreadsheet row coordinate.")
                for column, cell in enumerate(row.findall(S + "c"), 1):
                    check_cancelled()
                    reference = cell.get("r", f"column-{column}/row-{row_number}")
                    value = cell.findtext(S + "v", "")
                    kind = cell.get("t", "n")
                    if kind == "s":
                        try:
                            index = int(value)
                            if index < 0:
                                raise ValueError("Negative shared string index.")
                            value = strings[index]
                        except (ValueError, IndexError):
                            raise ValueError("Invalid spreadsheet shared string index.") from None
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(S + "t"))
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    formula = cell.findtext(S + "f")
                    if formula is not None:
                        value = f"={formula}" + (f" [cached value: {value}]" if value else " [no cached value]")
                    document.add(value, {"sheet": name[:200], "cell": reference[:60], "row": row_reference,
                                         "column": column}, "table_cell")
        document.warnings.append("Cell values and formulas are extracted literally; formulas are never evaluated. Date serials and formatting remain raw.")
    finally:
        archive.zip.close()


def pdf(data, document):
    import pypdf

    if not hasattr(pypdf, "apply_configuration"):
        raise ValueError("Safe PDF extraction requires pypdf 6.19 or newer.")
    # Context-local bounds also apply to object streams, font maps and nested forms.
    with pypdf.apply_configuration(
        maximum_declared_stream_length=MAX_XML_BYTES,
        array_based_stream_maximum_output_length=MAX_XML_BYTES,
        zlib_maximum_output_length=MAX_XML_BYTES,
        lzw_maximum_output_length=MAX_XML_BYTES,
        run_length_maximum_output_length=MAX_XML_BYTES,
        zlib_maximum_recovery_input_length=200_000,
        page_tree_maximum_entries=1000, page_tree_maximum_depth=30,
        xform_maximum_invocations_per_extraction=100,
        jbig2dec_binary=None,
    ):
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported; provide an unencrypted document explicitly.")
        if len(reader.pages) > 500:
            raise ValueError("PDF exceeds 500 pages; split the document.")
        empty = []
        for number, page in enumerate(reader.pages, 1):
            check_cancelled()
            text = page.extract_text() or ""
            if not text.strip():
                empty.append(number)
            for line_number, line in enumerate(text.splitlines(keepends=True), 1):
                document.add(line, {"page": number, "line": line_number})
        if empty:
            document.warnings.append(f"Pages with no extractable text: {', '.join(map(str, empty))}. Local OCR is not performed.")
        document.warnings.append("PDF text follows the embedded text order; visual table boundaries and images are not inferred.")


def text_document(data, suffix, document):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("Binary content is not supported as a text document.")
    if len(text) > MAX_CHARS:
        raise ValueError("Text exceeds the 2 million character extraction limit; split the document.")
    if suffix in {".csv", ".tsv"}:
        reader = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
        for row, cells in enumerate(reader, 1):
            for column, value in enumerate(cells, 1):
                document.add(value, {"table": 1, "row": row, "column": column, "line_end": reader.line_num}, "table_cell")
        return
    section = ""
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        heading = suffix in {".md", ".markdown"} and bool(re.match(r"^#{1,6}\s+\S", line))
        if heading:
            section = line.strip().lstrip("#").strip()[:200]
        document.add(line, {"line": number, "section": section}, "heading" if heading else "text")


def parse(data, suffix):
    check_cancelled()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Document exceeds the 24 MiB file limit.")
    document = Document()
    parser = {".pdf": pdf, ".docx": docx, ".pptx": pptx, ".xlsx": xlsx}.get(suffix)
    if parser:
        parser(data, document)
    else:
        text_document(data, suffix, document)
    check_cancelled()
    return document
