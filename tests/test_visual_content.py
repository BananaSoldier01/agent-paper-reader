"""Synthetic regressions for complete crops and renderable formula input."""
import copy
import json

import pytest

from reader import store
from reader.importer import import_document
from reader.regions import crop, crop_coverage_error, crop_edge_risks, crop_edge_warning
from reader.workflow import submit, validate, visual_content_error


def make_two_panel_pdf(path):
    content = (
        b'BT /F1 12 Tf 40 735 Td (Synthetic figure crop example.) Tj ET '
        b'0 0.7 0 rg 50 520 300 100 re f 0 0 0 rg '
        b'BT /F1 12 Tf 60 570 Td (Upper panel has four labels.) Tj ET '
        b'0.9 0 0 rg 50 200 300 100 re f 0 0 0 rg '
        b'BT /F1 12 Tf 60 250 Td (Lower panel has four labels.) Tj ET '
        b'BT /F1 12 Tf 40 150 Td (Figure 1. Two panels belong to one figure.) Tj ET'
    )
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
        b'/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    raw = b'%PDF-1.4\n'
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(raw)
    raw += b'xref\n0 6\n0000000000 65535 f \n'
    raw += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    raw += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    path.write_bytes(raw)


@pytest.fixture
def figure_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'two-panels.pdf'
    make_two_panel_pdf(path)
    doc = import_document(path)
    upper = next(b for b in doc['blocks'] if b['text'].startswith('Upper panel'))
    lower = next(b for b in doc['blocks'] if b['text'].startswith('Lower panel'))
    rows = [dict(b, structure_note='Synthetic source inspected') for b in doc['blocks']
            if b['id'] != lower['id']]
    figure = next(b for b in rows if b['id'] == upper['id'])
    figure.update(kind='figure', text='', source_ids=upper['source_ids']+lower['source_ids'])
    return doc, rows, figure


def test_partial_crop_rejected_before_structure_commit(figure_doc):
    doc, rows, figure = figure_doc
    partial = crop(doc['id'], 1, [40, 170, 370, 300])
    figure['asset'] = partial['asset']
    with pytest.raises(ValueError, match='figure crop excludes source bounds'):
        submit(doc['id'], {'operation': 'structure', 'revision': 0, 'submission_id': 'partial',
                           'agent': 'fixture', 'note': 'Inspect synthetic panels', 'blocks': rows})
    assert store.read(doc['id'])['revision'] == 0
    candidate = copy.deepcopy(doc)
    candidate['blocks'] = rows
    assert any('figure crop excludes source bounds' in error for error in validate(candidate)['errors'])


def test_complete_crop_keeps_both_panels_and_provenance(figure_doc):
    from PIL import Image
    doc, rows, figure = figure_doc
    full = crop(doc['id'], 1, [40, 170, 370, 610])
    figure['asset'] = full['asset']
    assert crop_coverage_error(doc, figure) is None
    path = store.folder(doc['id'])/full['asset']
    metadata = json.loads(path.with_suffix('.crop.json').read_text())
    assert metadata['bbox'] == [40, 170, 370, 610]
    assert metadata['source_sha256'] == doc['source_sha256']
    assert metadata['asset_sha256'] == store.digest(path.read_bytes())
    with Image.open(path) as image:
        assert image.size == (660, 880)
        assert image.getpixel((500, 100))[1] > 150
        assert image.getpixel((500, 740))[0] > 180
    saved = submit(doc['id'], {'operation': 'structure', 'revision': 0, 'submission_id': 'full',
                               'agent': 'fixture', 'note': 'Both panels checked', 'blocks': rows})
    assert saved['revision'] == 1


def test_changed_crop_image_does_not_keep_provenance_pass(figure_doc):
    doc, _rows, figure = figure_doc
    full = crop(doc['id'], 1, [40, 170, 370, 610])
    figure['asset'] = full['asset']
    (store.folder(doc['id'])/full['asset']).write_bytes(b'changed')
    assert 'crop provenance differs' in crop_coverage_error(doc, figure)


def test_caption_outside_image_is_not_treated_as_missing_panel(figure_doc):
    doc, _rows, _figure = figure_doc
    full = crop(doc['id'], 1, [40, 170, 370, 610])
    caption = dict(next(b for b in doc['blocks'] if b['kind'] == 'caption'), asset=full['asset'])
    assert crop_coverage_error(doc, caption) is None


def test_legacy_image_without_metadata_is_not_false_certified(figure_doc):
    doc, _rows, figure = figure_doc
    figure['asset'] = 'region-' + 'a'*16 + '.png'
    assert crop_coverage_error(doc, figure) is None


def test_image_table_crop_checks_all_source_atoms_not_only_displayed_labels(figure_doc):
    doc, rows, figure = figure_doc
    figure.update(kind='table', table_mode='image', text='Upper panel',
                  source_change='Labels only; the original image retains the complete visual source.')
    partial = crop(doc['id'], 1, [40, 170, 370, 300])
    figure['asset'] = partial['asset']
    with pytest.raises(ValueError, match='table crop excludes source bounds'):
        submit(doc['id'], {'operation': 'structure', 'revision': 0, 'submission_id': 'partial-table',
                           'agent': 'fixture', 'note': 'Inspect all table source bounds', 'blocks': rows})
    assert store.read(doc['id'])['revision'] == 0


def test_non_text_edges_warn_when_all_text_bounds_fit(figure_doc):
    doc, rows, figure = figure_doc
    # Rectangle tops/bottoms extend beyond the crop, while both labels fit.
    clipped = crop(doc['id'], 1, [40, 180, 370, 580])
    figure['asset'] = clipped['asset']
    assert crop_coverage_error(doc, figure) is None
    assert clipped['bbox'] == [40, 180, 370, 580]  # Never silently expand.
    assert clipped['edge_risks'] == ['top', 'bottom']
    assert clipped['suggested_bbox'] == [40, 170, 370, 590]
    saved = submit(doc['id'], {'operation': 'structure', 'revision': 0, 'submission_id': 'edge-risk',
                               'agent': 'fixture', 'note': 'Text fits; inspect graphical edges', 'blocks': rows})
    result = validate(saved)
    assert any('crop edge risk (top, bottom)' in w for w in result['warnings'])
    assert not any('crop edge risk' in e for e in result['errors'])
    # A suggestion is a starting point; this diagram needs another 2 points at the bottom.
    expanded = crop(doc['id'], 1, clipped['suggested_bbox'])
    assert expanded['edge_risks'] == ['bottom']
    full = crop(doc['id'], 1, [40, 170, 370, 610])
    figure['asset'] = full['asset']
    assert full['edge_risks'] == []
    assert 'suggested_bbox' not in full
    assert crop_edge_warning(doc, figure) is None


def test_old_crop_edge_check_is_read_only_and_requires_provenance(figure_doc):
    doc, _rows, figure = figure_doc
    clipped = crop(doc['id'], 1, [40, 180, 370, 580])
    figure['asset'] = clipped['asset']
    metadata_path = (store.folder(doc['id'])/clipped['asset']).with_suffix('.crop.json')
    metadata = json.loads(metadata_path.read_text())
    del metadata['edge_risks']
    metadata_path.write_text(json.dumps(metadata))
    before = metadata_path.read_bytes()
    assert 'crop edge risk' in crop_edge_warning(doc, figure)
    assert metadata_path.read_bytes() == before
    metadata_path.unlink()
    assert crop_edge_warning(doc, figure) is None


def test_edge_pixels_ignore_transparency_and_isolated_specks():
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (100, 60), (0, 0, 0, 0))
    assert crop_edge_risks(image) == []
    image.putpixel((50, 0), (0, 0, 0, 255))
    assert crop_edge_risks(image) == []
    ImageDraw.Draw(image).rectangle((20, 0, 35, 1), fill=(211, 211, 211, 255))
    assert crop_edge_risks(image) == ['top']


def test_full_page_border_suggestion_stays_inside_page(figure_doc):
    from PIL import Image, ImageDraw
    doc, _rows, _figure = figure_doc
    full = crop(doc['id'], 1, [0, 0, 612, 792])
    path = store.folder(doc['id'])/full['asset']
    with Image.open(path) as image:
        ImageDraw.Draw(image).line((0, 0, image.width-1, 0), fill='black', width=2)
        image.save(path)
    # Existing crop files are reused, and provenance refreshed only by an explicit crop call.
    repeated = crop(doc['id'], 1, [0, 0, 612, 792])
    assert 'top' in repeated['edge_risks']
    assert repeated['suggested_bbox'][1] == 0


@pytest.mark.parametrize('text', ['MultiHead(Q,K,V) = Concat(head₁,…,head_h)Wᴼ',
                                 r'\frac{QK^T}{\sqrt{d_k}}', '$$ $$', '$$x=1'])
def test_bare_or_unclosed_formula_needs_math_format(text):
    assert 'formula needs' in visual_content_error({'id': 'a'*24},
                                                   {'id': 'f1', 'kind': 'formula', 'text': text})


@pytest.mark.parametrize('text', [r'$$\mathrm{MultiHead}(Q,K,V)=\mathrm{Concat}(\mathrm{head}_1,\ldots,\mathrm{head}_h)W^O$$',
                                 r'$$\text{where }\mathrm{head}_i=\mathrm{Attention}(QW_i^Q,KW_i^K,VW_i^V)$$',
                                 r'$E=mc^2$', '$$\nx=1\n$$'])
def test_math_format_passes_without_changing_expression(text):
    block = {'id': 'f1', 'kind': 'formula', 'text': text}
    assert visual_content_error({'id': 'a'*24}, block) is None
    assert block['text'] == text


def test_formula_image_keeps_original_as_fallback():
    block = {'id': 'f1', 'kind': 'formula', 'text': 'Extracted fragment', 'asset': 'original.png'}
    assert visual_content_error({'id': 'a'*24}, block) is None


def test_bare_formula_rejected_before_structure_freezes(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    source = tmp_path/'formula.txt'
    source.write_text('E = mc^2', encoding='utf-8')
    doc = import_document(source)
    rows = [dict(b, kind='formula', structure_note='Synthetic formula checked') for b in doc['blocks']]
    payload = {'operation': 'structure', 'revision': 0, 'submission_id': 'bare-formula',
               'agent': 'fixture', 'note': 'Formula inspection', 'blocks': rows}
    with pytest.raises(ValueError, match='formula needs'):
        submit(doc['id'], payload)
    assert store.read(doc['id'])['revision'] == 0
    rows[0]['text'] = '$$E=mc^2$$'
    saved = submit(doc['id'], {**payload, 'submission_id': 'formatted-formula'})
    assert saved['revision'] == 1
    assert not any('formula needs' in error for error in validate(saved)['errors'])
