"""Read-only structure-candidate tool: synthetic self-check, footnote regression, CLI."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from reader import store
from reader.importer import import_document
from reader.structure_candidates import Line, assemble, starts_footnote_mark

try:
    from test_pdf import make_pdf
except ImportError:
    from tests.test_pdf import make_pdf


def _synthetic_lines():
    """Four-page synthetic fixture from the prototype self_check (version 20261005b)."""
    pw, ph = 600.0, 800.0
    pages = {p: {"page": p, "width": pw, "height": ph} for p in (1, 2, 3, 4)}
    lines = []

    def add(i, page, text, x0, y0, x1, y1, kind="prose"):
        lines.append(Line(f"a{i:05d}", page, text, [x0, y0, x1, y1], kind))

    # Page 1 two-column. Title full width, then 3 tight left lines, a gap, 1 left line,
    # and 2 tight right lines aligned with the first left lines.
    add(1, 1, "A Generic Title Spanning Both Columns Of This Page", 40, 40, 560, 54)
    add(2, 1, "Left column starts here and continues", 40, 80, 270, 92)
    add(3, 1, "on the next left line without a break", 40, 93, 270, 105)
    add(4, 1, "and finishes the left paragraph.", 40, 106, 200, 118)
    add(5, 1, "Second left paragraph after a gap.", 40, 150, 260, 162)
    add(6, 1, "Right column begins at the same height", 320, 80, 560, 92)
    add(7, 1, "and keeps going in the right column.", 320, 93, 540, 105)
    add(17, 1, "Another right line stays in reading order", 320, 120, 550, 132)
    add(18, 1, "after the whole left column is done.", 320, 133, 520, 145)
    add(8, 1, "x", 180, 96, 188, 104)  # subscript inside a left line
    add(9, 1, "", 0, 0, pw, ph, "page")
    # Page 2 single column with an inline hole and an author-like row that must NOT merge.
    add(10, 2, "This single column line is wide enough to count as body text here.", 50, 80, 540, 92)
    add(11, 2, "states stay on the left of a hole", 50, 110, 280, 122)
    add(12, 2, "and the sentence continues after it.", 360, 110, 540, 122)
    add(13, 2, "Ada Lovelace", 50, 160, 180, 172)
    add(14, 2, "Grace Hopper", 230, 160, 360, 172)
    add(15, 2, "Next paragraph is separate.", 50, 200, 400, 212)
    add(16, 2, "", 0, 0, pw, ph, "page")
    # Page 3 regressions: short last line vs centered, subscript vs next-row
    # digit, hanging wrap vs two short rows.
    add(19, 3, "A wrapped sentence continues onto the following", 50, 80, 540, 92)
    add(20, 3, "short last line.", 50, 93, 160, 105)
    add(21, 3, "centered close.", 250, 106, 380, 118)
    add(22, 3, "Cell label", 50, 200, 140, 212)
    add(23, 3, "9.9", 80, 214, 110, 226)
    add(24, 3, "value x stays inline here", 50, 300, 280, 312)
    add(25, 3, "i", 120, 304, 128, 311)
    add(26, 3, "Vinyals and Kaiser 2014 discriminative", 50, 400, 300, 412)
    add(27, 3, "Petrov and others 2006", 72, 413, 220, 425)
    add(28, 3, "A long reference line that wraps because it is wide enough to be full measure text", 50, 500, 540, 512)
    add(29, 3, "and the continuation is indented on purpose.", 72, 513, 360, 525)
    add(30, 3, "", 0, 0, pw, ph, "page")
    # Page 4 footnotes: two marked items tight under each other with the same
    # left edge must stay two candidates; an unmarked aligned continuation of a
    # marked footnote still joins it.
    add(31, 4, "\u2217Equal contribution and a footnote that wraps onto", 62, 700, 540, 709)
    add(32, 4, "the next line without any footnote mark at all.", 62, 710, 300, 719)
    add(33, 4, "\u2020Work performed while at Lab Alpha.", 62, 720, 260, 729)
    add(34, 4, "\u2021Work performed while at Lab Beta.", 62, 729.5, 270, 738.5)
    add(35, 4, "", 0, 0, pw, ph, "page")
    return lines, pages


def test_synthetic_self_check():
    lines, pages = _synthetic_lines()
    payload = assemble(lines, pages, {"kind": "self-check"})
    assert payload["coverage"]["coverage_ok"], payload["coverage"]
    assert payload["pages"][0]["mode"] == "two", payload["pages"][0]
    assert payload["pages"][1]["mode"] == "single", payload["pages"][1]
    texts = [c["text_preview"] for c in payload["candidates"] if c["page"] == 1 and c["kind_hint"] != "page"]
    # subscript joined into a left line
    assert any("Left column starts" in t and "finishes the left paragraph" in t for t in texts), texts
    assert any(t.startswith("Second left") for t in texts), texts
    assert any(t.startswith("Right column") and "right column." in t for t in texts), texts
    # left block before right block
    left_i = next(i for i, t in enumerate(texts) if t.startswith("Left column"))
    right_i = next(i for i, t in enumerate(texts) if t.startswith("Right column"))
    assert left_i < right_i, texts
    # second left paragraph is NOT inside the first
    first = next(t for t in texts if t.startswith("Left column"))
    assert "Second left" not in first
    p2 = [c["text_preview"] for c in payload["candidates"] if c["page"] == 2 and c["kind_hint"] != "page"]
    assert any("states stay" in t and "continues after" in t for t in p2), p2
    assert not any("Ada Lovelace" in t and "Grace Hopper" in t for t in p2), p2
    assert sum(1 for t in p2 if "Ada Lovelace" in t or "Grace Hopper" in t) == 2
    # no atom dropped
    ids = [a for c in payload["candidates"] for a in c["atom_ids"]]
    assert len(ids) == 35 and len(set(ids)) == 35
    assert payload["pages"][2]["mode"] == "single", payload["pages"][2]
    p3 = [c for c in payload["candidates"] if c["page"] == 3 and c["kind_hint"] != "page"]
    texts3 = [c["text_preview"] for c in p3]
    assert any("wrapped sentence" in t and "short last line." in t for t in texts3), texts3
    assert any(t.strip() == "centered close." for t in texts3), texts3
    assert not any("centered close." in t and "short last line." in t for t in texts3), texts3
    assert not any("Cell label" in t and "9.9" in t for t in texts3), texts3
    assert any(c["kind_hint"] == "figure_fragment" and "9.9" in c["text_preview"] for c in p3), p3
    sub = next(c for c in p3 if "value x stays inline" in c["text_preview"])
    assert " i" in sub["text_preview"] or sub["text_preview"].rstrip().endswith("i"), sub
    assert "possible_subscript" in sub["hard_spots"], sub
    assert not any("Vinyals" in t and "Petrov" in t for t in texts3), texts3
    assert any("long reference line" in t and "continuation is indented" in t for t in texts3), texts3
    # Footnote regression (Attention c00034): \u2020 and \u2021 items stay apart.
    p4 = [c for c in payload["candidates"] if c["page"] == 4 and c["kind_hint"] != "page"]
    dagger = [c for c in p4 if "Lab Alpha" in c["text_preview"]]
    ddagger = [c for c in p4 if "Lab Beta" in c["text_preview"]]
    assert len(dagger) == 1 and len(ddagger) == 1, p4
    assert dagger[0]["id"] != ddagger[0]["id"], p4
    assert dagger[0]["atom_ids"] == ["a00033"] and ddagger[0]["atom_ids"] == ["a00034"], p4
    assert "footnote_marker_split" in dagger[0]["hard_spots"], dagger
    assert "footnote_marker_split" in ddagger[0]["hard_spots"], ddagger
    star = next(c for c in p4 if "Equal contribution" in c["text_preview"])
    assert star["atom_ids"] == ["a00031", "a00032"], star
    # The \u2020 line sits tight and aligned under the star item, so the split
    # is flagged on the star item too.
    assert "Lab Alpha" not in star["text_preview"], star


def test_footnote_dagger_split_regression():
    lines, pages = _synthetic_lines()
    payload = assemble(lines, pages, {"kind": "self-check"})
    p4 = [c for c in payload["candidates"] if c["page"] == 4 and c["kind_hint"] != "page"]
    dagger = [c for c in p4 if "Lab Alpha" in c["text_preview"]]
    ddagger = [c for c in p4 if "Lab Beta" in c["text_preview"]]
    assert len(dagger) == 1 and len(ddagger) == 1, p4
    assert dagger[0]["id"] != ddagger[0]["id"], p4
    assert dagger[0]["atom_ids"] == ["a00033"] and ddagger[0]["atom_ids"] == ["a00034"], p4
    assert "footnote_marker_split" in dagger[0]["hard_spots"], dagger
    assert "footnote_marker_split" in ddagger[0]["hard_spots"], ddagger
    star = next(c for c in p4 if "Equal contribution" in c["text_preview"])
    assert star["atom_ids"] == ["a00031", "a00032"], star
    assert "footnote_marker_split" in star["hard_spots"], star
    assert "Lab Alpha" not in star["text_preview"], star


def test_footnote_regression_catches_old_merge(monkeypatch):
    from reader import structure_candidates
    monkeypatch.setattr(structure_candidates, "starts_footnote_mark", lambda text: False)
    lines, pages = _synthetic_lines()
    payload = assemble(lines, pages, {"kind": "self-check"})
    p4 = [c for c in payload["candidates"] if c["page"] == 4 and c["kind_hint"] != "page"]
    dagger = [c for c in p4 if "Lab Alpha" in c["text_preview"]]
    ddagger = [c for c in p4 if "Lab Beta" in c["text_preview"]]
    assert len(dagger) == 1 and len(ddagger) == 1, p4
    assert dagger[0]["id"] == ddagger[0]["id"], p4


def test_superscript_digit_footnote_not_detected():
    assert not starts_footnote_mark("4To illustrate this known limit we keep going.")
    pw, ph = 600.0, 800.0
    pages = {1: {"page": 1, "width": pw, "height": ph}}
    lines = [
        Line("a00001", 1, "Previous footnote body continues here with enough words.", [50, 100, 540, 112]),
        Line("a00002", 1, "4To illustrate this known limit we keep going.", [50, 113, 400, 125]),
        Line("a00003", 1, "", [0, 0, pw, ph], "page"),
    ]
    payload = assemble(lines, pages, {"kind": "self-check"})
    body = [c for c in payload["candidates"] if c["kind_hint"] != "page"]
    assert any(
        "4To illustrate" in c["text_preview"] and "Previous footnote" in c["text_preview"]
        for c in body
    ), body
    for c in body:
        assert "footnote_marker_split" not in c["hard_spots"], c


def _run_cli(monkeypatch, capsys, *argv):
    from reader.cli import main
    monkeypatch.setattr(sys, "argv", ["paper", *argv])
    try:
        main()
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    return code, capsys.readouterr().out


def test_cli_is_read_only(tmp_path, monkeypatch, capsys):
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    doc_path = store.folder(d["id"]) / "document.json"
    before_bytes = doc_path.read_bytes()
    before_sha = hashlib.sha256(before_bytes).hexdigest()
    before_revision = d["revision"]
    before_blocks = copy.deepcopy(d["blocks"])
    before_stage = d["stage"]
    before_submissions = copy.deepcopy(d["submissions"])
    preview = tmp_path / "p.md"
    code, raw = _run_cli(monkeypatch, capsys, "structure-candidates", d["id"], "--preview", str(preview))
    assert code == 0
    envelope = json.loads(raw)
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["coverage_ok"] is True
    assert result["document_id"] == d["id"]
    out = Path(result["path"])
    assert out.parent == tmp_path / "candidates"
    assert out.name == f'{d["id"]}-structure-candidates.json'
    assert store.folder(d["id"]) not in out.parents
    payload = json.loads(out.read_text(encoding="utf-8"))
    got = [a for c in payload["candidates"] for a in c["atom_ids"]]
    expected = [a["id"] for a in d["atoms"]]
    assert sorted(got) == sorted(expected)
    assert len(got) == len(set(got)) == len(expected)
    after_bytes = doc_path.read_bytes()
    assert after_bytes == before_bytes
    assert hashlib.sha256(after_bytes).hexdigest() == before_sha
    after = json.loads(after_bytes)
    assert after["revision"] == before_revision
    assert after["blocks"] == before_blocks
    assert after["stage"] == before_stage
    assert after["submissions"] == before_submissions
    assert preview.is_file()
    assert result["preview"] == str(preview)


def test_cli_rejects_output_inside_data(tmp_path, monkeypatch, capsys):
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    doc_path = store.folder(d["id"]) / "document.json"
    before = doc_path.read_bytes()
    message = "structure candidates output must not be inside the document data directory"
    code, raw = _run_cli(monkeypatch, capsys, "structure-candidates", d["id"], "--out", str(doc_path))
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert doc_path.read_bytes() == before
    assert not (tmp_path / "candidates").exists()
    preview = store.folder(d["id"]) / "preview.md"
    code, raw = _run_cli(monkeypatch, capsys, "structure-candidates", d["id"], "--preview", str(preview))
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert doc_path.read_bytes() == before
    assert not preview.exists()
    assert not (tmp_path / "candidates").exists()


def test_import_unchanged_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    assert not (tmp_path / "candidates").exists()
    assert not (store.ROOT / "candidates" / f'{d["id"]}-structure-candidates.json').exists()
    doc = json.loads((store.folder(d["id"]) / "document.json").read_text(encoding="utf-8"))
    assert "candidates" not in doc
    assert "structure_candidates" not in doc
    assert not any(store.folder(d["id"]).glob("*candidates*"))


def test_non_pdf_document_rejected(tmp_path, monkeypatch, capsys):
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    p = tmp_path / "note.md"
    p.write_text("# Title\n\nA paragraph.\n", encoding="utf-8")
    d = import_document(p)
    monkeypatch.setattr(sys, "argv", ["paper", "structure-candidates", d["id"]])
    from reader.cli import main
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "page and bbox" in payload["error"]
    assert not (tmp_path / "candidates").exists()


def test_cli_rejects_default_file_symlink_into_data(tmp_path, monkeypatch, capsys):
    """P1: default candidates file symlink to document.json must not overwrite literature data."""
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    doc_path = store.folder(d["id"]) / "document.json"
    before = doc_path.read_bytes()
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir()
    default_out = cand_dir / f'{d["id"]}-structure-candidates.json'
    default_out.symlink_to(doc_path)
    message = "structure candidates output must not be inside the document data directory"
    code, raw = _run_cli(monkeypatch, capsys, "structure-candidates", d["id"])
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert doc_path.read_bytes() == before
    assert default_out.is_symlink()
    assert default_out.resolve() == doc_path.resolve()


def test_cli_rejects_default_dir_symlink_into_data(tmp_path, monkeypatch, capsys):
    """P1: candidates/ directory symlink into data/ must be refused before write."""
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    doc_path = store.folder(d["id"]) / "document.json"
    before = doc_path.read_bytes()
    before_names = sorted(p.name for p in store.folder(d["id"]).iterdir())
    cand_dir = tmp_path / "candidates"
    cand_dir.symlink_to(store.folder(d["id"]))
    message = "structure candidates output must not be inside the document data directory"
    code, raw = _run_cli(monkeypatch, capsys, "structure-candidates", d["id"])
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert doc_path.read_bytes() == before
    assert sorted(p.name for p in store.folder(d["id"]).iterdir()) == before_names
    assert cand_dir.is_symlink()


def test_cli_rejects_out_and_preview_same_resolved_path(tmp_path, monkeypatch, capsys):
    """P2: --out and --preview resolving to the same real path must refuse before any write."""
    from reader import cli
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = tmp_path / "fixture.pdf"
    make_pdf(path)
    d = import_document(path)
    doc_path = store.folder(d["id"]) / "document.json"
    before = doc_path.read_bytes()
    message = "structure candidates --out and --preview must not resolve to the same path"

    same = tmp_path / "collision.json"
    code, raw = _run_cli(
        monkeypatch, capsys, "structure-candidates", d["id"],
        "--out", str(same), "--preview", str(same),
    )
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert not same.exists()
    assert not (tmp_path / "candidates").exists()
    assert doc_path.read_bytes() == before

    out = tmp_path / "out.json"
    preview = tmp_path / "preview.md"
    out.write_text("sentinel-out\n", encoding="utf-8")
    preview.symlink_to(out)
    code, raw = _run_cli(
        monkeypatch, capsys, "structure-candidates", d["id"],
        "--out", str(out), "--preview", str(preview),
    )
    assert code == 1
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == message
    assert out.read_text(encoding="utf-8") == "sentinel-out\n"
    assert preview.is_symlink()
    assert doc_path.read_bytes() == before
    assert not (tmp_path / "candidates").exists()
