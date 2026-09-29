import io
import json
import threading
import zipfile
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from jarvix.capabilities import document_parsers as parsers
from jarvix.capabilities.documents import setup
from jarvix.runtime import operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    if not hasattr(service, "documents"):
        setup(service, service.registry)
    yield service
    service.close()


def office_file(path, parts):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            entry = zipfile.ZipInfo(name)
            # ZipInfo normalizes Windows separators at construction; preserve
            # deliberately hostile names to exercise the reader's boundary.
            entry.filename = name
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, text)
    return str(path)


def pdf_file(path):
    writer = PdfWriter()
    for text in ("Project launch on Friday", "Delivery is confirmed for Monday"):
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as stream:
        writer.write(stream)
    return str(path)


def test_pdf_extracts_every_page_with_exact_citations(services):
    path = pdf_file(services.test_root / "report.pdf")
    result = services.documents.extract(path)
    assert [item["citation"]["page"] for item in result["items"]] == [1, 2]
    assert "Friday" in result["items"][0]["text"]
    assert "Monday" in result["items"][1]["text"]
    assert result["next_cursor"] is None and result["local_only"]
    assert result["items"][0]["citation"]["revision"] == result["revision"]


def test_docx_sections_tables_and_notes(services):
    path = office_file(services.test_root / "report.docx", {
        "word/document.xml": f'''<w:document xmlns:w="{parsers.W[1:-1]}"><w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Delivery plan</w:t></w:r></w:p>
        <w:p><w:r><w:t>Deliver on Monday</w:t><w:tab/><w:t>at nine.</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Ada</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>''',
        "word/footnotes.xml": f'''<w:footnotes xmlns:w="{parsers.W[1:-1]}">
        <w:footnote w:id="1"><w:p><w:r><w:t>Source reference</w:t></w:r></w:p></w:footnote></w:footnotes>''',
    })
    doc = services.documents.extract(path)
    assert "Deliver on Monday\tat nine." in [item["text"] for item in doc["items"]]
    heading = services.documents.sections(path)["items"][0]
    assert heading["text"] == "Delivery plan" and heading["citation"]["paragraph"] == 1
    cells = services.documents.tables(path)["items"]
    assert [cell["text"] for cell in cells] == ["Owner", "Ada"]
    assert cells[1]["citation"]["column"] == 2
    assert doc["items"][-1]["citation"]["note_id"] == "1"


def test_pptx_uses_presentation_order_and_table_coordinates(services):
    path = office_file(services.test_root / "deck.pptx", {
        "ppt/presentation.xml": f'''<p:presentation xmlns:p="{parsers.P[1:-1]}" xmlns:r="{parsers.R[1:-1]}">
        <p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>''',
        "ppt/_rels/presentation.xml.rels": f'''<Relationships xmlns="{parsers.REL[1:-1]}">
        <Relationship Id="first" Target="slides/slide1.xml"/>
        <Relationship Id="second" Target="slides/slide2.xml"/></Relationships>''',
        "ppt/slides/slide1.xml": f'''<p:sld xmlns:p="{parsers.P[1:-1]}" xmlns:a="{parsers.A[1:-1]}">
        <p:sp><a:p><a:r><a:t>Last slide</a:t></a:r></a:p></p:sp></p:sld>''',
        "ppt/slides/slide2.xml": f'''<p:sld xmlns:p="{parsers.P[1:-1]}" xmlns:a="{parsers.A[1:-1]}">
        <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <a:p><a:r><a:t>First slide</a:t></a:r></a:p></p:sp>
        <a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Budget</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></p:sld>''',
    })
    result = services.documents.extract(path)
    assert [item["text"] for item in result["items"]] == ["First slide", "Budget", "Last slide"]
    assert [item["citation"]["slide"] for item in result["items"]] == [1, 1, 2]
    assert services.documents.tables(path)["items"][0]["citation"]["column"] == 1


def test_xlsx_shared_inline_formula_and_empty_cached_values(services):
    path = office_file(services.test_root / "budget.xlsx", {
        "xl/workbook.xml": f'''<workbook xmlns="{parsers.S[1:-1]}" xmlns:r="{parsers.R[1:-1]}">
        <sheets><sheet name="Budget" r:id="sheet"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": f'''<Relationships xmlns="{parsers.REL[1:-1]}">
        <Relationship Id="sheet" Target="worksheets/sheet1.xml"/></Relationships>''',
        "xl/sharedStrings.xml": f'''<sst xmlns="{parsers.S[1:-1]}"><si><t>Total</t></si></sst>''',
        "xl/worksheets/sheet1.xml": f'''<worksheet xmlns="{parsers.S[1:-1]}"><sheetData><row r="4">
        <c r="A4" t="s"><v>0</v></c><c r="B4"><f>SUM(B1:B3)</f><v>120</v></c>
        <c r="C4" t="inlineStr"><is><t>Approved</t></is></c>
        <c r="D4"><f>WEBSERVICE("https://example.org")</f></c>
        </row></sheetData></worksheet>''',
    })
    result = services.documents.tables(path)
    assert [item["text"] for item in result["items"]] == [
        "Total", "=SUM(B1:B3) [cached value: 120]", "Approved",
        '=WEBSERVICE("https://example.org") [no cached value]',
    ]
    assert result["items"][1]["citation"]["cell"] == "B4"
    assert result["items"][1]["citation"]["sheet"] == "Budget"


@pytest.mark.parametrize("suffix", [".txt", ".md", ".json", ".py", ".ts", ".luau"])
def test_text_paging_loses_no_characters_and_rejects_changed_revision(services, suffix):
    path = services.test_root / ("source" + suffix)
    source = "# Planning\n" + "a" * 7500 + "\nLast line\n"
    path.write_bytes(source.encode("utf-8"))
    first = services.documents.extract(str(path), limit=2)
    assert first["truncated"] and first["next_cursor"] == 2
    items = first["items"][:]
    cursor = first["next_cursor"]
    while cursor is not None:
        page = services.documents.extract(str(path), cursor=cursor, limit=2, expected_revision=first["revision"])
        items.extend(page["items"])
        cursor = page["next_cursor"]
    assert "".join(item["text"] for item in items) == source
    assert len({item["citation"]["id"] for item in items}) == len(items)
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        services.documents.extract(str(path), cursor=2, expected_revision=first["revision"])


def test_csv_retains_multiline_cells_and_coordinates(services):
    path = services.test_root / "table.csv"
    path.write_bytes(b'name,description\nAda,"first line\nsecond line"\n')
    items = services.documents.tables(str(path))["items"]
    assert items[-1]["text"] == "first line\nsecond line"
    assert items[-1]["citation"]["row"] == 2 and items[-1]["citation"]["column"] == 2
    assert items[-1]["citation"]["line_end"] == 3


def test_evidence_summary_comparison_are_cited_local_and_not_fabricated(services):
    first, second = services.test_root / "first.txt", services.test_root / "second.txt"
    first.write_bytes(b"The rocket is blue.\nLaunch is scheduled on Friday.\n")
    second.write_bytes(b"The rocket is red.\nLaunch is scheduled on Monday.\n")
    response = services.documents.question(str(first), "When is the launch scheduled?")
    assert response["answer"] == "Launch is scheduled on Friday.\n"
    assert response["evidence"][0]["citation"]["line"] == 2
    assert services.documents.question(str(first), "hippopotamus")["answer"] is None
    summary = services.documents.summarize(str(first), limit=1)
    assert summary["excerpts"][0]["citation"]["path"] == str(first)
    assert str(first) in summary["text"] and "line:" in summary["text"]
    assert summary["excerpts"][0]["text"] in summary["text"]
    assert summary["selection_only"]
    compared = services.documents.compare([str(first), str(second)])
    assert "rocket" in compared["common_terms"]
    assert "friday" in compared["documents"][0]["unique_terms"]
    assert "monday" in compared["documents"][1]["unique_terms"]
    assert compared["documents"][1]["evidence"][0]["citation"]["path"] == str(second)


def test_roots_sensitive_paths_and_revoked_collection_access(services, tmp_path):
    path = services.test_root / "report.txt"
    path.write_text("private plan", encoding="utf-8")
    collection = services.documents.create_collection("Project references", [str(path)])
    saved = services.records.get("knowledge.collection", collection["id"])
    assert saved["paths"] == [str(path)] and "private plan" not in json.dumps(saved)
    for target in (tmp_path / "outside.txt", services.test_root / ".env", services.test_root / "credentials.json"):
        target.write_text("hidden", encoding="utf-8")
        with pytest.raises(ValueError):
            services.documents.extract(str(target))
    services.remove_file_root(str(services.test_root))
    with pytest.raises(ValueError):
        services.documents.search_collection(collection["id"], "private")


@pytest.mark.parametrize("xml", [
    '<!DOCTYPE doc [<!ENTITY entity "boom">]><doc>&entity;</doc>',
    '<!DOCTYPE doc SYSTEM "file:///secret"><doc/>',
    '<!DOCTYPE doc [<!ENTITY entity "boom">]><doc>&entity;</doc>'.encode("utf-16"),
])
def test_office_rejects_dtd_and_entities_in_utf8_and_utf16(services, xml):
    path = office_file(services.test_root / "hostile.docx", {"word/document.xml": xml})
    with pytest.raises(ValueError, match="DTD"):
        services.documents.extract(path)


@pytest.mark.parametrize("name", ["../escape.xml", "/absolute.xml", "C:/root.xml", "word\\secret.xml"])
def test_office_rejects_unsafe_archive_members(services, name):
    path = office_file(services.test_root / "hostile.docx", {name: "<x/>"})
    with pytest.raises(ValueError, match="Unsafe"):
        services.documents.extract(path)


def test_office_expansion_and_file_limits_fail_explicitly(services, monkeypatch):
    path = office_file(services.test_root / "bomb.docx", {"word/document.xml": "a" * 1_000_000})
    with pytest.raises(ValueError, match="decompression"):
        services.documents.extract(path)
    text = services.test_root / "long.txt"
    text.write_text("123456789", encoding="utf-8")
    monkeypatch.setattr(parsers, "MAX_FILE_BYTES", 5)
    with pytest.raises(ValueError, match="file limit"):
        services.documents.extract(str(text))


def test_office_does_not_follow_external_relationships(services):
    path = office_file(services.test_root / "external.xlsx", {
        "xl/workbook.xml": f'''<workbook xmlns="{parsers.S[1:-1]}" xmlns:r="{parsers.R[1:-1]}">
        <sheets><sheet name="External" r:id="remote"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": f'''<Relationships xmlns="{parsers.REL[1:-1]}">
        <Relationship Id="remote" Target="https://example.org/private" TargetMode="External"/></Relationships>''',
    })
    with pytest.raises(ValueError, match="relationship"):
        services.documents.extract(path)


def test_cancelled_reads_and_parsers_stop_without_saved_content(services):
    path = services.test_root / "large.txt"
    path.write_text("content" * 50_000, encoding="utf-8")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError), operation(cancel=cancel):
        services.documents.extract(str(path))
    assert services.records.list("knowledge.collection") == []


def test_tool_schemas_permissions_and_response_bounds(services):
    path = services.test_root / "many.txt"
    path.write_text(("x" * 1800 + "\n") * 60, encoding="utf-8")
    result = services.execute_tool("documents.extract", {"path": str(path), "limit": 30})
    assert result.ok and result.data["truncated"]
    assert len(json.dumps(result.as_dict(), ensure_ascii=False)) < 64000
    assert not services.execute_tool("documents.extract", {"path": str(path), "cursor": -1}).ok
    denied = services.execute_tool("knowledge.create", {"name": "Denied", "paths": [str(path)]}, approve=lambda _: False)
    assert not denied.ok and services.records.list("knowledge.collection") == []
    accepted = services.execute_tool("knowledge.create", {"name": "Approved", "paths": [str(path)]}, approve=lambda _: True)
    assert accepted.ok
    assert services.registry.get("documents.extract").permission_level == 1
    assert services.registry.get("knowledge.create").permission_level == 2


def test_knowledge_search_pages_files_and_rechecks_each_reference(services):
    paths = []
    for name in ("first", "second"):
        path = services.test_root / f"{name}.txt"
        path.write_text("alpha one\nalpha two\n", encoding="utf-8")
        paths.append(str(path))
    record = services.documents.create_collection("References", paths)
    first = services.documents.search_collection(record["id"], "alpha", limit=1)
    assert first["next_cursor"] == 1 and first["next_document_cursor"] is None
    second = services.documents.search_collection(record["id"], "alpha", limit=1, cursor=1)
    assert second["next_cursor"] is None and second["next_document_cursor"] == 1
    last = services.documents.search_collection(record["id"], "alpha", document_cursor=1)
    assert last["path"] == paths[1] and last["next_document_cursor"] is None


def test_encrypted_pdf_rejected_without_password_access(services):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("not-in-vault")
    output = io.BytesIO()
    writer.write(output)
    path = services.test_root / "encrypted.pdf"
    path.write_bytes(output.getvalue())
    with pytest.raises(ValueError, match="Encrypted"):
        services.documents.extract(str(path))


def test_summary_note_text_is_bounded_and_source_line_endings_survive(services):
    path = services.test_root / "long-report.md"
    data = b"# Report\r\n" + b"".join(f"Section {number}: ".encode() + b"context " * 300 + b"\r\n" for number in range(20))
    path.write_bytes(data)
    first = services.documents.extract(str(path), limit=1)
    assert first["items"][0]["text"] == "# Report\r\n"
    result = services.execute_tool("documents.summarize", {"path": str(path), "limit": 10})
    assert result.ok and len(result.data["text"]) <= 16000
    assert str(path) in result.data["text"] and result.data["revision"] in result.data["text"]
    assert all(item["text"] in result.data["text"] for item in result.data["excerpts"])
    assert result.data["selection_only"]


def test_source_changed_during_parse_is_rejected(services, monkeypatch):
    path = services.test_root / "changed.txt"
    path.write_bytes(b"approved source")
    parse = parsers.parse

    def changing(data, suffix):
        parsed = parse(data, suffix)
        path.write_bytes(b"different replacement source")
        return parsed

    monkeypatch.setattr(parsers, "parse", changing)
    with pytest.raises(ValueError, match="changed during extraction"):
        services.documents.extract(str(path))
